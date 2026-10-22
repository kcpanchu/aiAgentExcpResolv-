import json
from types import SimpleNamespace as NS

import pytest

from vim_agent.btp import BtpConnectivity
from vim_agent.models import Action, ExceptionType
from vim_agent.sap_ecc import EccODataClient


class Resp:
    def __init__(self, body=None, status=200, headers=None):
        self.body, self.status_code, self.headers = body, status, headers or {}

    def json(self): return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, handler): self.handler, self.calls = handler, []

    def request(self, method, path, **kw):
        self.calls.append((method, path, kw))
        return self.handler(method, path, kw)


def d(x): return Resp({"d": x})


def test_list_open_exceptions_maps_reason_codes():
    row = {"__metadata": {}, "DocId": "D1", "CompanyCode": "1000", "Vendor": "V1", "ReasonCode": "PRICE_VAR",
           "Amount": "120.50", "Currency": "USD", "PoNumber": "4500001", "InvoiceRef": "", "Notes": "hi"}
    row2 = {**row, "DocId": "D2", "ReasonCode": "WEIRD"}
    s = FakeSession(lambda m, p, kw: d({"results": [row, row2]}))
    out = EccODataClient(s, {"PRICE_VAR": "PRICE_VARIANCE"}).list_open_exceptions()
    assert out[0].exception_type is ExceptionType.PRICE_VARIANCE and out[0].amount == 120.5
    assert out[0].invoice_ref is None
    assert out[1].exception_type is ExceptionType.OTHER      # unmapped -> never auto
    assert s.calls[0][2]["params"]["$filter"] == "Status eq 'OPEN'"


def test_get_po_flattens_items_and_strips_metadata():
    s = FakeSession(lambda m, p, kw: d({"__metadata": {}, "PoNumber": "1", "Items": {"results": [{"__metadata": {}, "Item": "10"}]}}))
    po = EccODataClient(s).get_po("1")
    assert po == {"PoNumber": "1", "Items": [{"Item": "10"}]}


def test_filter_values_are_escaped():
    s = FakeSession(lambda m, p, kw: d({"results": []}))
    EccODataClient(s).get_goods_receipts("A'B")
    assert "A''B" in s.calls[0][2]["params"]["$filter"]


def test_similar_invoices_union_dedupes():
    inv = {"DocNumber": "51", "FiscalYear": "2026"}
    s = FakeSession(lambda m, p, kw: d({"results": [inv]}))
    out = EccODataClient(s).find_similar_invoices("V1", "R1", 10.0)
    assert len(out) == 1 and len(s.calls) == 2


def test_vendor_flags_normalised():
    s = FakeSession(lambda m, p, kw: d({"Vendor": "V1", "RiskFlag": True, "RecentBankChange": False}))
    v = EccODataClient(s).get_vendor("V1")
    assert v["risk_flag"] is True and v["recent_bank_change"] is False


def test_exists_handles_404():
    s = FakeSession(lambda m, p, kw: Resp(status=404))
    assert EccODataClient(s).exists("999") is False
    s = FakeSession(lambda m, p, kw: d({"DocNumber": "999"}))
    assert EccODataClient(s).exists("999") is True


def test_execute_action_fetches_csrf_and_retries_once_on_403():
    state = {"posts": 0, "tokens": 0}

    def handler(m, p, kw):
        if m == "GET":
            state["tokens"] += 1
            return Resp({"d": {}}, headers={"x-csrf-token": f"T{state['tokens']}"})
        state["posts"] += 1
        if state["posts"] == 1:
            return Resp(status=403)
        return d({"__metadata": {}, "Ok": True, "DryRun": True})

    s = FakeSession(handler)
    out = EccODataClient(s).execute_action("D1", Action.APPROVE_VARIANCE, {"a": 1}, True)
    posts = [c for c in s.calls if c[0] == "POST"]
    assert out == {"Ok": True, "DryRun": True}
    assert posts[0][2]["headers"]["X-CSRF-Token"] == "T1" and posts[1][2]["headers"]["X-CSRF-Token"] == "T2"
    assert posts[1][2]["json"] == {"DocId": "D1", "Action": "APPROVE_VARIANCE", "ParamsJson": json.dumps({"a": 1}), "DryRun": True}


def test_btp_destination_and_proxy():
    vcap = {"destination": [{"credentials": {"clientid": "c", "clientsecret": "s", "url": "https://x.auth", "uri": "https://dest"}}],
            "connectivity": [{"credentials": {"clientid": "cc", "clientsecret": "ss", "url": "https://x.auth",
                                              "onpremise_proxy_host": "proxy", "onpremise_proxy_http_port": "20003"}}]}
    seen = []

    class Http:
        def post(self, url, **kw):
            seen.append(url)
            return Resp({"access_token": "tok", "expires_in": 3600})

        def get(self, url, **kw):
            seen.append(url)
            return Resp({"destinationConfiguration": {"URL": "http://ecc:8000"}})

    b = BtpConnectivity(vcap, http=Http(), location_id="LOC1")
    assert b.destination("D")["destinationConfiguration"]["URL"] == "http://ecc:8000"
    assert b.proxies["http"] == "http://proxy:20003"
    h = b.proxy_headers()
    assert h["Proxy-Authorization"] == "Bearer tok" and h["SAP-Connectivity-SCC-Location_ID"] == "LOC1"
    assert seen.count("https://x.auth/oauth/token") == 2   # one per service, then cached
    b.proxy_headers()
    assert seen.count("https://x.auth/oauth/token") == 2
