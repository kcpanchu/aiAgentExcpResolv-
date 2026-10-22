"""S/4HANA 2025 adapter skeleton: released OData APIs via OAuth2. Paths are placeholders to confirm."""
import requests

from .models import Action


class S4ODataClient:
    def __init__(self, base_url: str, token_provider):
        self.base, self.token = base_url.rstrip("/"), token_provider

    def _get(self, path, **params):
        r = requests.get(f"{self.base}/{path}", params={"$format": "json", **params},
                         headers={"Authorization": f"Bearer {self.token()}"}, timeout=30)
        r.raise_for_status()
        return r.json()

    def get_po(self, po_number):
        return self._get(f"sap/opu/odata/sap/API_PURCHASEORDER_PROCESS_SRV/A_PurchaseOrder('{po_number}')",
                         **{"$expand": "to_PurchaseOrderItem"})

    def get_goods_receipts(self, po_number):
        return self._get("sap/opu/odata/sap/API_MATERIAL_DOCUMENT_SRV/A_MaterialDocumentItem",
                         **{"$filter": f"PurchaseOrder eq '{po_number}'"})

    def get_vendor(self, vendor):
        return self._get(f"sap/opu/odata/sap/API_BUSINESS_PARTNER/A_Supplier('{vendor}')")

    def find_similar_invoices(self, vendor, invoice_ref, amount):
        return self._get("sap/opu/odata/sap/API_SUPPLIERINVOICE_PROCESS_SRV/A_SupplierInvoice",
                         **{"$filter": f"InvoicingParty eq '{vendor}' and SupplierInvoiceIDByInvcgParty eq '{invoice_ref}'"})

    def exists(self, doc_number):
        raise NotImplementedError("map doc type -> API lookup")

    def execute_action(self, doc_id, action: Action, params, dry_run):
        raise NotImplementedError("VIM action wrapper service (custom RAP/OData) - build behind policy")
