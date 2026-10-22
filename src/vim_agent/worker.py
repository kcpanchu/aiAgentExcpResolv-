"""Run lifecycle on top of the store: poll -> enqueue -> claim -> investigate -> decide -> execute."""
import logging

from .agent import investigate
from .models import Decision
from .policy import Policy, decide
from .sap_client import SapClient
from .store import (AWAITING_REVIEW, ESCALATED, EXECUTING, INVESTIGATING, PROPOSED, RESOLVED, VERIFYING,
                    LostLease, Run, RunAudit, Store)

log = logging.getLogger("vim_agent")


def list_all_open(sap: SapClient, page: int = 100, cap: int = 5000):
    """Returns (exceptions, complete). `complete` is False on error or cap, so callers must not reconcile."""
    out = []
    try:
        while len(out) < cap:
            batch = sap.list_open_exceptions(limit=page, offset=len(out))
            out += batch
            if len(batch) < page:
                return out, True
    except Exception:
        log.exception("listing open exceptions failed")
    return out, False


def poll_once(store: Store, sap: SapClient) -> dict:
    exceptions, complete = list_all_open(sap)
    new = sum(store.enqueue(e)[1] for e in exceptions)
    closed = store.reconcile_open({e.doc_id for e in exceptions}) if complete else 0
    return {"seen": len(exceptions), "new": new, "closed_by_human": closed, "complete": complete}


def _escalate(store: Store, run: Run, audit, reason: str):
    audit.log(run.doc_id, "escalated", reason=reason)
    store.transition(run, ESCALATED, outcome=reason)


def _finish_execution(store: Store, run: Run, audit, dry_run: bool):
    store.transition(run, VERIFYING)
    # Verification is minimal today: the executor reported success. TODO: re-read the document and check state.
    store.transition(run, RESOLVED, outcome="dry_run" if dry_run else "executed")


def _recover(store: Store, run: Run, audit, dry_run: bool) -> bool:
    """Crash recovery for a run that died while EXECUTING/VERIFYING. True if handled."""
    rows = store.get_execution(run.run_id)
    if not rows:
        return False                      # crashed before any side effect: safe to start over
    if any(r["finished_at"] is None for r in rows):
        _escalate(store, run, audit, "execution_outcome_unknown")   # may have changed SAP; never repeat blindly
    else:
        _finish_execution(store, run, audit, bool(rows[0]["dry_run"]))
    return True


def _note(sap, run, audit, decision, proposal, reason):
    try:
        sap.post_note(run.doc_id, f"[AI agent] {decision.value}: {proposal.action.value} - {proposal.root_cause} "
                                  f"(confidence {proposal.confidence:.2f}; evidence {', '.join(proposal.evidence)}; {reason})")
    except Exception as e:   # a failed note must not fail or repeat the run
        audit.log(run.doc_id, "note_failed", error=repr(e))


def run_one(store: Store, worker: str, sap: SapClient, llm, policy: Policy, *, dry_run: bool = True,
            post_notes: bool = False, lease_s: float = 600, mirror=None) -> Run | None:
    run = store.claim_next(worker, lease_s)
    if run is None:
        return None
    audit = RunAudit(store, run, mirror)
    try:
        audit.log(run.doc_id, "claimed", worker=worker, attempt=run.attempts, resumed_from=run.prev_state)
        if run.prev_state in (EXECUTING, VERIFYING) and _recover(store, run, audit, dry_run):
            return run
        exc = run.exception
        store.transition(run, INVESTIGATING)
        proposal = investigate(exc, sap, llm, audit)
        store.transition(run, PROPOSED)
        audit.log(run.doc_id, "proposal", proposal=proposal)
        decision, reason = decide(exc, proposal, policy, sap)
        audit.log(run.doc_id, "decision", decision=decision.value, reason=reason)
        if post_notes:
            _note(sap, run, audit, decision, proposal, reason)

        if decision is Decision.AUTO_EXECUTE:
            store.transition(run, EXECUTING)
            owns, row = store.begin_execution(run, proposal.action.value,
                                              {"params": proposal.params}, dry_run)
            if not owns:
                _recover(store, run, audit, dry_run)
                return run
            try:
                result = sap.execute_action(run.doc_id, proposal.action, proposal.params, dry_run)
            except Exception as e:
                # outcome unknown: SAP may or may not have acted. Do NOT retry.
                audit.log(run.doc_id, "execution_error", error=repr(e))
                _escalate(store, run, audit, "execution_outcome_unknown")
                return run
            store.finish_execution(run, proposal.action.value, result)
            audit.log(run.doc_id, "executed", result=result, dry_run=dry_run)
            _finish_execution(store, run, audit, dry_run)
        elif decision is Decision.NEEDS_APPROVAL:
            store.transition(run, AWAITING_REVIEW, outcome="needs_approval")
        else:
            _escalate(store, run, audit, f"policy: {reason}")
    except LostLease:
        log.warning("lost lease on run %s; abandoning", run.run_id)
    except Exception as e:
        log.exception("run %s failed", run.run_id)
        try:
            store.fail(run, repr(e))
        except LostLease:
            pass
    return run
