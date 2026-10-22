from datetime import date

import pytest

from vim_agent.models import Action, Decision, ExceptionType, Proposal, VimException
from vim_agent.policy import CodingPolicy, Policy, decide
from vim_agent.sap_client import MockSapClient

TODAY = date(2026, 10, 22)
LINE = {"gl": "400000", "cost_center": "1000", "tax_code": "V1", "amount": 100.0, "text": "hosting"}


def history(n=6):
    return [{"doc": f"51{i}", "year": "2026", "date": f"2026-{i + 3:02d}-15",
             "lines": [{"gl": "400000", "cost_center": "1000", "tax_code": "V1", "amount": 100.0, "text": "hosting"}]}
            for i in range(n)]


def sap(hist=None, vendor=None):
    s = MockSapClient(vendors={"V1": vendor or {}})
    s.coding_history = history() if hist is None else hist
    return s


def exc(amount=118.0, net=100.0):
    return VimException("D1", "1000", "V1", ExceptionType.CODING, amount, "USD", net_amount=net)


def prop(lines=None, findings=None, action=Action.APPLY_CODING, conf=0.95):
    return Proposal(action, "matches history", conf, [],
                    {"coding_lines": lines if lines is not None else [dict(LINE)], "image_findings": findings or []})


POL = Policy(auto_enabled=True, coding=CodingPolicy(auto_enabled=True))


def run(p, s=None, e=None, policy=POL):
    return decide(e or exc(), p, policy, s or sap(), today=TODAY)


def test_clean_case_auto_executes():
    assert run(prop())[0] is Decision.AUTO_EXECUTE


def test_auto_requires_both_switches():
    assert run(prop(), policy=Policy(auto_enabled=True))[0] is Decision.NEEDS_APPROVAL
    assert run(prop(), policy=Policy(coding=CodingPolicy(auto_enabled=True)))[0] is Decision.NEEDS_APPROVAL


def test_invalid_master_data_blocks():
    s = sap(); s.invalid_coding["1000"] = "cost center locked"
    d, why = run(prop(), s)
    assert d is Decision.NEEDS_APPROVAL and "cost center locked" in why


def test_coding_not_in_history_blocks():
    d, why = run(prop([{**LINE, "gl": "999999"}]))
    assert d is Decision.NEEDS_APPROVAL and "not found in vendor history" in why


def test_weak_or_stale_history_blocks():
    assert "too weak" in run(prop(), sap(history(3)))[1]
    stale = [{**h, "date": "2024-01-15"} for h in history()]
    assert "not used recently" in run(prop(), sap(stale))[1]


def test_split_vendor_history_is_ambiguous():
    mixed = history(4) + [{**h, "lines": [{**h["lines"][0], "gl": "610000"}]} for h in history(4)]
    assert "too weak" in run(prop(), sap(mixed))[1]


def test_lines_must_reconcile_to_net_and_net_must_be_known():
    assert "!= invoice net" in run(prop([{**LINE, "amount": 90.0}]))[1]
    assert "net amount unknown" in run(prop(), e=exc(net=None))[1]


def test_amount_cap_and_vendor_flags():
    assert "above auto limit" in run(prop([{**LINE, "amount": 5000.0}]), e=exc(5900.0, 5000.0))[1]
    assert "vendor risk" in run(prop(), sap(vendor={"recent_bank_change": True}))[1]


def test_image_conflict_with_proposal_blocks():
    f = [{"type": "handwritten_note", "text": "charge to CC 4711", "page": 1, "field": "cost_center",
          "value": "4711", "affects": ["cost_center"]}]
    d, why = run(prop(findings=f))
    assert d is Decision.NEEDS_APPROVAL and "image says cost_center=4711" in why


def test_image_corroborating_value_does_not_block_but_never_authorises():
    f = [{"type": "stamp", "text": "CC 1000", "page": 1, "field": "cost_center", "value": "0000001000", "affects": []}]
    assert run(prop(findings=f))[0] is Decision.AUTO_EXECUTE          # matches proposal AND history
    # image-only basis cannot pass: coding absent from history stays blocked even if the image names it
    f2 = [{"field": "gl", "value": "999999", "page": 1}]
    assert run(prop([{**LINE, "gl": "999999"}], findings=f2))[0] is Decision.NEEDS_APPROVAL


def test_unverifiable_image_caveat_needs_human():
    f = [{"type": "stamp", "text": "CAPEX - asset", "page": 2, "affects": ["gl"]}]
    assert "image caveat" in run(prop(findings=f))[1]
    assert run(prop(findings=[{"type": "logo", "text": "Acme", "page": 1, "affects": []}]))[0] is Decision.AUTO_EXECUTE


def test_malformed_and_other_actions():
    assert run(prop(lines=[]))[0] is Decision.ESCALATE
    assert run(prop(lines=[{"gl": "1"}]))[0] is Decision.ESCALATE      # missing amount
    assert run(prop(action=Action.SUGGEST_CODING))[0] is Decision.NEEDS_APPROVAL
    assert run(prop(action=Action.APPROVE_VARIANCE))[0] is Decision.ESCALATE
    assert run(prop(conf=0.2))[0] is Decision.ESCALATE
    assert run(prop(findings="oops"))[0] is Decision.NEEDS_APPROVAL
