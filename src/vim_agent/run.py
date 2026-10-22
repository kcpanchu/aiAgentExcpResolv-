"""BTP worker: poll ECC for open VIM exceptions, run the pipeline. Config via env vars."""
import json
import logging
import os
import socket
import time

import anthropic

from .audit import AuditLog
from .btp import BtpConnectivity, DestinationSession
from .policy import Policy
from .sap_ecc import EccODataClient
from .store import open_store
from .worker import poll_once, run_one

log = logging.getLogger("vim_agent")


def main():
    logging.basicConfig(level=logging.INFO)
    cfg = json.load(open(os.getenv("EXCEPTION_MAP", "config/exception_map.json")))
    cfg.pop("_comment", None)
    sap = EccODataClient(DestinationSession(BtpConnectivity(), os.getenv("ECC_DESTINATION", "ECC_VIM_AGENT")), cfg)
    llm = anthropic.Anthropic()
    policy = Policy(auto_enabled=os.getenv("AUTO_ENABLED") == "1")
    audit = AuditLog(os.getenv("AUDIT_PATH"))
    dry_run = os.getenv("DRY_RUN", "1") == "1"
    post_notes = os.getenv("POST_NOTES") == "1"      # off in shadow phase
    interval = int(os.getenv("POLL_SECONDS", "300"))
    store = open_store(os.environ["DATABASE_URL"])   # sqlite:///file.db (dev) or the BTP PostgreSQL uri
    worker = f"{socket.gethostname()}-{os.getenv('CF_INSTANCE_INDEX', '0')}-{os.getpid()}"
    while True:
        try:
            log.info("poll: %s", poll_once(store, sap))
            while run_one(store, worker, sap, llm, policy, dry_run=dry_run, post_notes=post_notes, mirror=audit):
                pass
        except Exception:
            log.exception("cycle failed")
        time.sleep(interval)


if __name__ == "__main__":
    main()
