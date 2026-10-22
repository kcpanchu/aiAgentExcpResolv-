"""BTP worker: poll ECC for open VIM exceptions, run the pipeline. Config via env vars."""
import json
import logging
import os
import time

import anthropic

from .audit import AuditLog
from .btp import BtpConnectivity, DestinationSession
from .pipeline import process
from .policy import Policy
from .sap_ecc import EccODataClient

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
    seen: set[str] = set()   # TODO: persist (HANA/PostgreSQL service) so restarts do not reprocess
    while True:
        try:
            for exc in sap.list_open_exceptions():
                if exc.doc_id in seen:
                    continue
                seen.add(exc.doc_id)
                try:
                    process(exc, sap, llm, policy, audit, dry_run=dry_run, post_notes=post_notes)
                except Exception:
                    log.exception("failed processing %s", exc.doc_id)
        except Exception:
            log.exception("poll failed")
        time.sleep(interval)


if __name__ == "__main__":
    main()
