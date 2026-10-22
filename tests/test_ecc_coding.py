from test_ecc_client import FakeSession, Resp, d
from vim_agent.sap_ecc import EccODataClient
from datetime import date


def row(doc, ms, gl="0000400000", cc="0000001000", amt="100.00", yr="2026"):
    return {"__metadata": {}, "DocNumber": doc, "FiscalYear": yr, "PostingDate": f"/Date({ms})/", "InvoiceRef": "R",
            "Currency": "USD", "Gl": gl, "CostCenter": cc, "Order": "", "Wbs": "", "ProfitCenter": "", "TaxCode": "V1",
            "Amount": amt, "Text": "hosting"}


def test_history_groups_lines_into_invoices_and_parses_dates():
    rows = [row("51", 1760000000000), row("51", 1760000000000, gl="0000610000"), row("52", 1750000000000), row("53", 1740000000000)]
    s = FakeSession(lambda m, p, kw: d({"results": rows}))
    inv = EccODataClient(s).get_vendor_coding_history("V1", "1000", limit=2)
    assert [i["doc"] for i in inv] == ["51", "52"]                       # newest 2 invoices only
    assert len(inv[0]["lines"]) == 2 and inv[0]["date"] == "2025-10-09" and inv[0]["lines"][0]["amount"] == 100.0
    assert "Vendor eq 'V1'" in s.calls[0][2]["params"]["$filter"]


def test_validate_coding_filters_and_requires_every_field_ok():
    rows = [{"Field": "gl", "Value": "400000", "Ok": True, "Reason": ""},
            {"Field": "cost_center", "Value": "1000", "Ok": False, "Reason": "locked for primary postings"}]
    s = FakeSession(lambda m, p, kw: d({"results": rows}))
    out = EccODataClient(s).validate_coding("1000", {"gl": "400000", "cost_center": "1000", "wbs": ""}, date(2026, 10, 22))
    f = s.calls[0][2]["params"]["$filter"]
    assert out["ok"] is False and "Gl eq '400000'" in f and "CostCenter eq '1000'" in f and "Wbs" not in f
    assert "datetime'2026-10-22T00:00:00'" in f
    assert EccODataClient(FakeSession(lambda m, p, kw: d({"results": []}))).validate_coding("1000", {"gl": "1"}, date.today())["ok"] is False


def test_invoice_image_fetches_meta_then_stream():
    def handler(m, p, kw):
        if p.endswith("/$value"):
            return Resp(headers={"Content-Type": "image/png"}) if False else type("R", (), {
                "content": b"IMG", "headers": {"Content-Type": "image/png"}, "status_code": 200,
                "raise_for_status": lambda self: None})()
        return d({"__metadata": {}, "DocId": "D1", "Page": 1, "PageCount": 3, "MimeType": "image/png"})
    img = EccODataClient(FakeSession(handler)).get_invoice_image("D1", 1)
    assert (img.page, img.page_count, img.media_type, img.data) == (1, 3, "image/png", b"IMG")


def test_unsupported_image_becomes_error_data():
    def handler(m, p, kw):
        if p.endswith("/$value"):
            return type("R", (), {"content": b"%PDF", "headers": {"Content-Type": "application/pdf"}, "status_code": 200,
                                  "raise_for_status": lambda self: None})()
        return d({"PageCount": 1})
    out = EccODataClient(FakeSession(handler)).get_invoice_image("D1", 1)
    assert out["retryable"] is False and "PDF" in out["error"]


def test_exception_carries_net_amount():
    r = {"DocId": "D1", "CompanyCode": "1000", "Vendor": "V1", "ReasonCode": "NO_CODING", "Amount": "118.00",
         "NetAmount": "100.00", "Currency": "USD"}
    s = FakeSession(lambda m, p, kw: d({"results": [r]}))
    e = EccODataClient(s, {"NO_CODING": "CODING"}).list_open_exceptions()[0]
    assert e.net_amount == 100.0 and e.exception_type.value == "CODING"
