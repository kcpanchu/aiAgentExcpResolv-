from types import SimpleNamespace as NS

from vim_agent.audit import AuditLog
from vim_agent.models import Action, Decision, ExceptionType, VimException
from vim_agent.pipeline import process
from vim_agent.policy import Policy
from vim_agent.sap_client import MockSapClient


class FakeLLM:
    """Scripted model: reads the PO, then proposes."""
    def __init__(self, proposal):
        self.steps = [
            [NS(type="tool_use", id="1", name="get_po", input={"po_number": "4500001"})],
            [NS(type="tool_use", id="2", name="propose_resolution", input=proposal)],
        ]
        self.messages = NS(create=lambda **kw: NS(content=self.steps.pop(0)))


def sap():
    return MockSapClient(pos={"4500001": {"number": "4500001", "price": 10.0}},
                         vendors={"V1": {}, "V2": {"risk_flag": True}})


def exc(vendor="V1", amount=100.0, typ=ExceptionType.PRICE_VARIANCE):
    return VimException("D1", "1000", vendor, typ, amount, "USD", po_number="4500001")


GOOD = dict(action="APPROVE_VARIANCE", root_cause="2% over PO price", confidence=0.95, evidence=["4500001"])


def run(e, proposal, auto=True):
    return process(e, sap(), FakeLLM(proposal), Policy(auto_enabled=auto), AuditLog())


def test_auto_execute_when_in_policy():
    assert run(exc(), GOOD)[1] is Decision.AUTO_EXECUTE


def test_auto_disabled_needs_approval():
    assert run(exc(), GOOD, auto=False)[1] is Decision.NEEDS_APPROVAL


def test_over_limit_needs_approval():
    assert run(exc(amount=9999), GOOD)[1] is Decision.NEEDS_APPROVAL


def test_hallucinated_evidence_blocked():
    assert run(exc(), {**GOOD, "evidence": ["9999999"]})[1] is Decision.NEEDS_APPROVAL


def test_risky_vendor_blocked():
    assert run(exc(vendor="V2"), GOOD)[1] is Decision.NEEDS_APPROVAL


def test_vendor_mismatch_always_escalates():
    assert run(exc(typ=ExceptionType.VENDOR_MISMATCH), GOOD)[1] is Decision.ESCALATE


def test_executor_only_runs_on_auto_and_respects_dry_run():
    s = sap()
    process(exc(), s, FakeLLM(GOOD), Policy(auto_enabled=True), AuditLog(), dry_run=False)
    assert s.executed and s.executed[0][1] is Action.APPROVE_VARIANCE
