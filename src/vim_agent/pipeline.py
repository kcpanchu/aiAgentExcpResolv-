from .agent import investigate
from .audit import AuditLog
from .models import Decision, VimException
from .policy import Policy, decide
from .sap_client import SapClient


def process(exc: VimException, sap: SapClient, llm, policy: Policy, audit: AuditLog, dry_run: bool = True):
    proposal = investigate(exc, sap, llm, audit)
    audit.log(exc.doc_id, "proposal", proposal=proposal)
    decision, reason = decide(exc, proposal, policy, sap)
    audit.log(exc.doc_id, "decision", decision=decision.value, reason=reason)
    result = None
    if decision is Decision.AUTO_EXECUTE:
        result = sap.execute_action(exc.doc_id, proposal.action, proposal.params, dry_run)
        audit.log(exc.doc_id, "executed", result=result)
    return proposal, decision, reason, result
