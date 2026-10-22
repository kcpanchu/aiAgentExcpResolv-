import base64
import json
from types import SimpleNamespace as NS

from test_coding_policy import exc, history
from vim_agent.agent import investigate, system_for, tools_for
from vim_agent.audit import AuditLog
from vim_agent.models import Action, ExceptionType, ImageResult, VimException
from vim_agent.sap_client import MockSapClient


class Recorder:
    """Scripted model that records what it was sent."""
    def __init__(self, steps):
        self.steps, self.calls = steps, []

        def create(**kw):
            self.calls.append({**kw, "messages": list(kw["messages"])})   # snapshot: the loop keeps appending
            return NS(content=self.steps.pop(0))
        self.messages = NS(create=create)


def use(i, name, args): return NS(type="tool_use", id=str(i), name=name, input=args)


def test_coding_exception_gets_coding_tools_and_playbook():
    names = {t["name"] for t in tools_for(exc())}
    assert {"view_invoice_image", "suggest_coding_from_history", "validate_coding", "propose_resolution"} <= names
    other = VimException("D", "1", "V", ExceptionType.PRICE_VARIANCE, 1, "USD")
    assert "view_invoice_image" not in {t["name"] for t in tools_for(other)}
    assert "CODING task" in system_for(exc()) and "CODING task" not in system_for(other)


def test_image_reaches_the_model_and_findings_come_back():
    s = MockSapClient(vendors={"V1": {}})
    s.coding_history = history()
    s.images["D1"] = [ImageResult(1, 2, "image/png", b"PNGDATA")]
    findings = [{"type": "handwritten_note", "text": "CC 4711", "page": 1, "field": "cost_center", "value": "4711", "affects": ["cost_center"]}]
    llm = Recorder([
        [use(1, "suggest_coding_from_history", {"description": "hosting"})],
        [use(2, "view_invoice_image", {"page": 1})],
        [use(3, "view_invoice_image", {"page": 2})],        # page the store does not have
        [use(4, "propose_resolution", {"action": "SUGGEST_CODING", "root_cause": "image conflicts with history",
                                       "confidence": 0.8, "evidence": [],
                                       "params": {"coding_lines": [], "image_findings": findings}})],
    ])
    p = investigate(exc(), s, llm, AuditLog())
    assert p.action is Action.SUGGEST_CODING and p.params["image_findings"][0]["value"] == "4711"

    hist_result = llm.calls[1]["messages"][-1]["content"][0]["content"]
    assert json.loads(hist_result)[0]["coding"]["gl"] == "400000"
    img_block = llm.calls[2]["messages"][-1]["content"][0]["content"]
    assert img_block[0]["type"] == "image" and img_block[0]["source"]["media_type"] == "image/png"
    assert base64.b64decode(img_block[0]["source"]["data"]) == b"PNGDATA"
    assert "untrusted" in img_block[1]["text"] and "Page" not in img_block[1]["text"]
    assert "no page 2" in llm.calls[3]["messages"][-1]["content"][0]["content"]


def test_tool_errors_are_returned_as_data_not_raised():
    s = MockSapClient()
    s.get_vendor_coding_history = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ECC timeout"))
    llm = Recorder([[use(1, "get_vendor_coding_history", {})],
                    [use(2, "propose_resolution", {"action": "ESCALATE", "root_cause": "no history", "confidence": 0.1, "evidence": []})]])
    p = investigate(exc(), s, llm, AuditLog())
    assert p.action is Action.ESCALATE
    assert "ECC timeout" in llm.calls[1]["messages"][-1]["content"][0]["content"]
