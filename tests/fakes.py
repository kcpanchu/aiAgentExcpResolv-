from types import SimpleNamespace as NS


class FakeLLM:
    """Scripted model: reads the PO, then proposes."""
    def __init__(self, proposal):
        self.steps = [
            [NS(type="tool_use", id="1", name="get_po", input={"po_number": "4500001"})],
            [NS(type="tool_use", id="2", name="propose_resolution", input=proposal)],
        ]
        self.messages = NS(create=lambda **kw: NS(content=self.steps.pop(0)))


class BoomLLM:
    def __init__(self):
        def boom(**kw):
            raise RuntimeError("llm down")
        self.messages = NS(create=boom)
