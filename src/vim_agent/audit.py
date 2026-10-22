import json
import time


class AuditLog:
    def __init__(self, path: str | None = None):
        self.path, self.events = path, []

    def log(self, doc_id: str, kind: str, **data):
        ev = {"ts": time.time(), "doc_id": doc_id, "kind": kind, **data}
        self.events.append(ev)
        if self.path:
            with open(self.path, "a") as f:
                f.write(json.dumps(ev, default=str) + "\n")
