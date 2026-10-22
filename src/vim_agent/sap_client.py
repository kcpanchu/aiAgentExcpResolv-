"""SAP access boundary. Real implementations (pyrfc / OData) go behind this Protocol."""
from typing import Protocol

from .models import Action, VimException


class SapClient(Protocol):
    # --- read-only: exposed to the agent as tools ---
    def get_po(self, po_number: str) -> dict: ...
    def get_goods_receipts(self, po_number: str) -> list[dict]: ...
    def get_vendor(self, vendor: str) -> dict: ...
    def find_similar_invoices(self, vendor: str, invoice_ref: str | None, amount: float) -> list[dict]: ...
    def exists(self, doc_number: str) -> bool: ...
    def list_open_exceptions(self, limit: int = 50, offset: int = 0) -> list[VimException]: ...
    # --- write: ONLY the executor may call this ---
    def post_note(self, doc_id: str, text: str) -> dict: ...
    def execute_action(self, doc_id: str, action: Action, params: dict, dry_run: bool) -> dict: ...


class MockSapClient:
    def __init__(self, pos=None, grs=None, vendors=None, invoices=None):
        self.pos, self.grs = pos or {}, grs or {}
        self.vendors, self.invoices = vendors or {}, invoices or []
        self.executed: list[tuple] = []
        self.notes: list[tuple] = []
        self.open_exceptions: list = []

    def get_po(self, po_number): return self.pos.get(po_number, {})
    def get_goods_receipts(self, po_number): return self.grs.get(po_number, [])
    def get_vendor(self, vendor): return self.vendors.get(vendor, {})
    def find_similar_invoices(self, vendor, invoice_ref, amount):
        return [i for i in self.invoices if i["vendor"] == vendor and
                (i.get("ref") == invoice_ref or abs(i["amount"] - amount) < 0.01)]

    def exists(self, doc_number):
        return any(doc_number in (p.get("number"), *[g["doc"] for g in self.grs.get(p.get("number"), [])])
                   for p in self.pos.values()) or any(i.get("doc") == doc_number for i in self.invoices)

    def list_open_exceptions(self, limit=50, offset=0): return self.open_exceptions[offset:offset + limit]

    def post_note(self, doc_id, text):
        self.notes.append((doc_id, text))
        return {"ok": True}

    def execute_action(self, doc_id, action, params, dry_run):
        if not dry_run:
            self.executed.append((doc_id, action, params))
        return {"doc_id": doc_id, "action": action.value, "dry_run": dry_run, "ok": True}
