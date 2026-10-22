"""ECC adapter: OData V2 (SEGW service ZVIM_AGENT_SRV) via BTP Destination + Cloud Connector.

The ABAP side is a thin read layer plus ONE write path (ActionSet). Policy stays in this service.
"""
import json
import re
from datetime import date, datetime, timezone
from urllib.parse import quote

from .images import UnsupportedImage, normalize_image
from .models import Action, ExceptionType, ImageResult, VimException

SERVICE = "/sap/opu/odata/sap/ZVIM_AGENT_SRV"


def _q(v: str) -> str:
    return str(v).replace("'", "''")


def _clean(d):
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items() if k != "__metadata"}
    if isinstance(d, list):
        return [_clean(x) for x in d]
    return d


def _odata_date(v) -> str:
    """OData V2 Edm.DateTime arrives as '/Date(1700000000000)/'; return ISO 'YYYY-MM-DD'."""
    m = re.search(r"/Date\((-?\d+)", str(v))
    if m:
        return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).date().isoformat()
    return str(v)[:10]


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
    def list_open_exceptions(self, limit: int = 50, offset: int = 0) -> list[VimException]:
        rows = self._get("ExceptionSet", **{"$filter": "Status eq 'OPEN'", "$top": str(limit), "$skip": str(offset)})
        return [self._to_exception(r) for r in rows]

    def _to_exception(self, r: dict) -> VimException:
        typ = ExceptionType(self.exception_map.get(r.get("ReasonCode", ""), ExceptionType.OTHER.value))
        return VimException(doc_id=r["DocId"], company_code=r["CompanyCode"], vendor=r["Vendor"],
                            exception_type=typ, amount=float(r["Amount"]), currency=r["Currency"],
                            po_number=r.get("PoNumber") or None, invoice_ref=r.get("InvoiceRef") or None,
                            notes=r.get("Notes", ""),
                            net_amount=float(r["NetAmount"]) if r.get("NetAmount") not in (None, "") else None)

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

    # ---------------- coding ----------------
    def get_vendor_coding_history(self, vendor: str, company_code: str, limit: int = 100) -> list[dict]:
        """Flat line rows (newest first) grouped into invoices; only the newest `limit` invoices are kept."""
        rows = self._get("VendorCodingSet", **{
            "$filter": f"Vendor eq '{_q(vendor)}' and CompanyCode eq '{_q(company_code)}'",
            "$top": str(min(limit * 10, 1000))})
        invoices: dict[tuple, dict] = {}
        for r in rows:
            k = (r["DocNumber"], r["FiscalYear"])
            if k not in invoices:
                if len(invoices) >= limit:
                    continue
                invoices[k] = {"doc": r["DocNumber"], "year": r["FiscalYear"], "date": _odata_date(r["PostingDate"]),
                               "ref": r.get("InvoiceRef", ""), "currency": r.get("Currency", ""), "lines": []}
            invoices[k]["lines"].append({
                "gl": r.get("Gl", ""), "cost_center": r.get("CostCenter", ""), "order": r.get("Order", ""),
                "wbs": r.get("Wbs", ""), "profit_center": r.get("ProfitCenter", ""), "tax_code": r.get("TaxCode", ""),
                "amount": float(r.get("Amount") or 0), "text": r.get("Text", "")})
        return list(invoices.values())

    def validate_coding(self, company_code: str, line: dict, posting_date: date) -> dict:
        names = {"gl": "Gl", "cost_center": "CostCenter", "order": "Order", "wbs": "Wbs",
                 "profit_center": "ProfitCenter", "tax_code": "TaxCode"}
        parts = [f"CompanyCode eq '{_q(company_code)}'", f"PostingDate eq datetime'{posting_date.isoformat()}T00:00:00'"]
        parts += [f"{n} eq '{_q(line[k])}'" for k, n in names.items() if line.get(k)]
        rows = self._get("CodingCheckSet", **{"$filter": " and ".join(parts)})
        fields = [{"field": r["Field"], "value": r["Value"], "ok": bool(r["Ok"]), "reason": r.get("Reason", "")} for r in rows]
        return {"ok": bool(fields) and all(f["ok"] for f in fields), "fields": fields}

    def get_invoice_image(self, doc_id: str, page: int) -> ImageResult | dict:
        key = f"DocumentImageSet(DocId='{quote(_q(doc_id))}',Page={int(page)})"
        try:
            meta = self._get(key)
            r = self.s.request("GET", f"{SERVICE}/{key}/$value")
            r.raise_for_status()
            data, mime = normalize_image(r.content, r.headers.get("Content-Type") or meta.get("MimeType", ""))
        except UnsupportedImage as e:
            return {"error": str(e), "retryable": False}
        return ImageResult(page=int(page), page_count=int(meta.get("PageCount") or 1), media_type=mime, data=data)

    # ---------------- write path (executor only) ----------------
    def post_note(self, doc_id: str, text: str) -> dict:
        return self._post("NoteSet", {"DocId": doc_id, "Text": text[:4000]})

    def execute_action(self, doc_id: str, action: Action, params: dict, dry_run: bool) -> dict:
        return self._post("ActionSet", {"DocId": doc_id, "Action": action.value,
                                        "ParamsJson": json.dumps(params), "DryRun": dry_run})
