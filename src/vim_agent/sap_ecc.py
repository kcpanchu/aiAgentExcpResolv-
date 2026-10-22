"""ECC adapter skeleton: RFC via pyrfc. Function module names are placeholders to be built/confirmed."""
from .models import Action


class EccRfcClient:
    def __init__(self, conn):
        self.conn = conn  # pyrfc.Connection (SNC, technical user)

    def get_po(self, po_number):
        return self.conn.call("BAPI_PO_GETDETAIL", PURCHASEORDER=po_number)

    def get_goods_receipts(self, po_number):
        return self.conn.call("Z_VIM_GET_GR", PO_NUMBER=po_number)["GR_LIST"]

    def get_vendor(self, vendor):
        return self.conn.call("Z_VIM_GET_VENDOR", LIFNR=vendor)["VENDOR"]

    def find_similar_invoices(self, vendor, invoice_ref, amount):
        return self.conn.call("Z_VIM_FIND_DUP", LIFNR=vendor, XBLNR=invoice_ref or "", AMOUNT=amount)["INVOICES"]

    def exists(self, doc_number):
        return self.conn.call("Z_VIM_DOC_EXISTS", DOC_NUMBER=doc_number)["EXISTS"] == "X"

    def execute_action(self, doc_id, action: Action, params, dry_run):
        return self.conn.call("Z_VIM_EXEC_ACTION", DOCID=doc_id, ACTION=action.value,
                              PARAMS=str(params), DRY_RUN="X" if dry_run else "")
