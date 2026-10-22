from .agent import investigate
from .audit import AuditLog
from .models import Decision, VimException
from .policy import Policy, decide
from .sap_client import SapClient


def process(exc: VimException, sap: SapClient, llm, policy: Policy, audit: AuditLog, dry_run: bool = True, post_notes: bool = False):
    proposal = investigate(exc, sap, llm, audit)
    audit.log(exc.doc_id, "proposal", proposal=proposal)
    decision, reason = decide(exc, proposal, policy, sap)
    audit.log(exc.doc_id, "decision", decision=decision.value, reason=reason)
    result = None
    if decision is Decision.AUTO_EXECUTE:
        result = sap.execute_action(exc.doc_id, proposal.action, proposal.params, dry_run)
        audit.log(exc.doc_id, "executed", result=result)
    if post_notes:   # assist phase+: reviewers see the agent's reasoning inside VIM
        sap.post_note(exc.doc_id, f"[AI agent] {decision.value}: {proposal.action.value} - {proposal.root_cause} "
                                  f"(confidence {proposal.confidence:.2f}; evidence {', '.join(proposal.evidence)}; {reason})")
    return proposal, decision, reason, result
