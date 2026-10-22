"""ECC adapter: OData V2 (SEGW service ZVIM_AGENT_SRV) via BTP Destination + Cloud Connector.

The ABAP side is a thin read layer plus ONE write path (ActionSet). Policy stays in this service.
"""
import json
from urllib.parse import quote

from .models import Action, ExceptionType, VimException

SERVICE = "/sap/opu/odata/sap/ZVIM_AGENT_SRV"


def _q(v: str) -> str:
    return str(v).replace("'", "''")


def _clean(d):
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items() if k != "__metadata"}
    if isinstance(d, list):
        return [_clean(x) for x in d]
    return d


def _rows(d):
    return d["results"] if isinstance(d, dict) and "results" in d else d


class EccODataClient:
    def __init__(self, session, exception_map: dict[str, str] | None = None):
        self.s = session
        self.exception_map = exception_map or {}
        self._csrf: str | None = None

    # ---------------- plumbing ----------------
    def _get(self, path: str, **params):
        r = self.s.request("GET", f"{SERVICE}/{path}", params={"$format": "json", **params})
        r.raise_for_status()
        return _clean(_rows(r.json()["d"]))

    def _fetch_csrf(self) -> str:
        r = self.s.request("GET", f"{SERVICE}/", headers={"X-CSRF-Token": "Fetch"}, params={"$format": "json"})
        r.raise_for_status()
        self._csrf = r.headers["x-csrf-token"]
        return self._csrf

    def _post(self, entity_set: str, body: dict):
        for attempt in (0, 1):
            token = self._csrf or self._fetch_csrf()
            r = self.s.request("POST", f"{SERVICE}/{entity_set}", json=body, params={"$format": "json"},
                               headers={"X-CSRF-Token": token, "Accept": "application/json"})
            if r.status_code == 403 and attempt == 0:   # stale token
                self._csrf = None
                continue
            r.raise_for_status()
            return _clean(r.json()["d"])

    # ---------------- exceptions ----------------
    def list_open_exceptions(self, limit: int = 50) -> list[VimException]:
        rows = self._get("ExceptionSet", **{"$filter": "Status eq 'OPEN'", "$top": str(limit)})
        return [self._to_exception(r) for r in rows]

    def _to_exception(self, r: dict) -> VimException:
        typ = ExceptionType(self.exception_map.get(r.get("ReasonCode", ""), ExceptionType.OTHER.value))
        return VimException(doc_id=r["DocId"], company_code=r["CompanyCode"], vendor=r["Vendor"],
                            exception_type=typ, amount=float(r["Amount"]), currency=r["Currency"],
                            po_number=r.get("PoNumber") or None, invoice_ref=r.get("InvoiceRef") or None,
                            notes=r.get("Notes", ""))

    # ---------------- read tools ----------------
    def get_po(self, po_number: str) -> dict:
        d = self._get(f"PurchaseOrderSet('{quote(_q(po_number))}')", **{"$expand": "Items"})
        d["Items"] = _rows(d.get("Items", []))
        return d

    def get_goods_receipts(self, po_number: str) -> list[dict]:
        return self._get("GoodsReceiptSet", **{"$filter": f"PoNumber eq '{_q(po_number)}'"})

    def get_vendor(self, vendor: str) -> dict:
        d = self._get(f"VendorSet('{quote(_q(vendor))}')")
        return {**d, "risk_flag": bool(d.get("RiskFlag")), "recent_bank_change": bool(d.get("RecentBankChange"))}

    def find_similar_invoices(self, vendor: str, invoice_ref: str | None, amount: float) -> list[dict]:
        base = f"Vendor eq '{_q(vendor)}'"
        queries = [f"{base} and Amount eq {amount:.2f}m"]
        if invoice_ref:
            queries.append(f"{base} and InvoiceRef eq '{_q(invoice_ref)}'")
        found: dict[str, dict] = {}
        for f in queries:
            for row in self._get("InvoiceSet", **{"$filter": f}):
                found[f"{row['DocNumber']}/{row.get('FiscalYear', '')}"] = row
        return list(found.values())

    def exists(self, doc_number: str) -> bool:
        r = self.s.request("GET", f"{SERVICE}/DocumentSet('{quote(_q(doc_number))}')", params={"$format": "json"})
        if r.status_code == 404:
            return False
        r.raise_for_status()
        return True

    # ---------------- write path (executor only) ----------------
    def post_note(self, doc_id: str, text: str) -> dict:
        return self._post("NoteSet", {"DocId": doc_id, "Text": text[:4000]})

    def execute_action(self, doc_id: str, action: Action, params: dict, dry_run: bool) -> dict:
        return self._post("ActionSet", {"DocId": doc_id, "Action": action.value,
                                        "ParamsJson": json.dumps(params), "DryRun": dry_run})
