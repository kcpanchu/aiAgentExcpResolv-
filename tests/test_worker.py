from fakes import BoomLLM, FakeLLM
from test_pipeline import GOOD, sap
from test_store import Clock, exc
from vim_agent.models import Action
from vim_agent.policy import Policy
from vim_agent.store import (AWAITING_REVIEW, DISCOVERED, ESCALATED, EXECUTING, RESOLVED, open_store)
from vim_agent.worker import list_all_open, poll_once, run_one


def mk(tmp_path):
    clock = Clock()
    return open_store(f"sqlite:///{tmp_path / 'w.db'}", clock=clock), clock


def seeded(s=None, n=1):
    s = s or sap()
    s.open_exceptions = [exc(f"D{i}") for i in range(n)]
    return s


def test_auto_dry_run_resolves_and_is_not_reprocessed(tmp_path):
    store, _ = mk(tmp_path)
    s = seeded()
    assert poll_once(store, s)["new"] == 1
    run = run_one(store, "w", s, FakeLLM(GOOD), Policy(auto_enabled=True))
    assert store.get_run(run.run_id).state == RESOLVED and store.outcome(run.run_id) == "dry_run"
    assert s.executed == []                                        # dry run: nothing executed in SAP
    assert poll_once(store, s)["new"] == 0                         # same exception does not re-enter
    assert run_one(store, "w", s, FakeLLM(GOOD), Policy(auto_enabled=True)) is None
    kinds = [e["kind"] for e in store.events(run.run_id)]
    assert {"claimed", "tool_call", "proposal", "decision", "executed"} <= set(kinds)


def test_real_execution_happens_once(tmp_path):
    store, _ = mk(tmp_path)
    s = seeded()
    poll_once(store, s)
    run_one(store, "w", s, FakeLLM(GOOD), Policy(auto_enabled=True), dry_run=False)
    assert len(s.executed) == 1 and s.executed[0][1] is Action.APPROVE_VARIANCE


def test_needs_approval_waits_for_human_then_reconciles(tmp_path):
    store, _ = mk(tmp_path)
    s = seeded()
    poll_once(store, s)
    run = run_one(store, "w", s, FakeLLM(GOOD), Policy(auto_enabled=False))
    assert store.get_run(run.run_id).state == AWAITING_REVIEW
    s.open_exceptions = []                                          # clerk resolved it in VIM
    assert poll_once(store, s)["closed_by_human"] == 1


def test_escalation_is_terminal(tmp_path):
    store, _ = mk(tmp_path)
    s = seeded()
    poll_once(store, s)
    run = run_one(store, "w", s, FakeLLM({**GOOD, "confidence": 0.1}), Policy(auto_enabled=True))
    assert store.get_run(run.run_id).state == ESCALATED


def test_llm_failure_retries_then_escalates(tmp_path):
    store, clock = mk(tmp_path)
    s = seeded()
    poll_once(store, s)
    for step in (0, 31, 61):
        clock.t += step
        run = run_one(store, "w", s, BoomLLM(), Policy())
    assert store.get_run(run.run_id).state == ESCALATED and store.outcome(run.run_id) == "max_attempts"


def test_crash_mid_execution_escalates_instead_of_repeating(tmp_path):
    store, clock = mk(tmp_path)
    s = seeded()
    poll_once(store, s)
    run = store.claim_next("dead-worker", lease_s=60)               # worker dies after begin_execution
    from vim_agent.store import INVESTIGATING, PROPOSED
    store.transition(run, INVESTIGATING); store.transition(run, PROPOSED); store.transition(run, EXECUTING)
    store.begin_execution(run, "APPROVE_VARIANCE", {}, False)
    clock.t += 61
    done = run_one(store, "w2", s, FakeLLM(GOOD), Policy(auto_enabled=True), dry_run=False)
    assert store.get_run(done.run_id).state == ESCALATED
    assert store.outcome(done.run_id) == "execution_outcome_unknown" and s.executed == []


def test_crash_after_finished_execution_just_completes(tmp_path):
    store, clock = mk(tmp_path)
    s = seeded()
    poll_once(store, s)
    run = store.claim_next("dead", lease_s=60)
    from vim_agent.store import INVESTIGATING, PROPOSED
    store.transition(run, INVESTIGATING); store.transition(run, PROPOSED); store.transition(run, EXECUTING)
    store.begin_execution(run, "APPROVE_VARIANCE", {}, False)
    store.finish_execution(run, "APPROVE_VARIANCE", {"ok": True})
    clock.t += 61
    done = run_one(store, "w2", s, FakeLLM(GOOD), Policy(auto_enabled=True), dry_run=False)
    assert store.get_run(done.run_id).state == RESOLVED and s.executed == []


def test_incomplete_listing_never_reconciles(tmp_path):
    store, _ = mk(tmp_path)
    s = seeded(n=3)
    poll_once(store, s)
    run_one(store, "w", s, FakeLLM(GOOD), Policy())                 # D0 -> AWAITING_REVIEW

    def flaky(limit=50, offset=0):
        raise RuntimeError("ECC down")
    s.list_open_exceptions = flaky
    out = poll_once(store, s)
    assert out["complete"] is False and out["closed_by_human"] == 0
    assert store.counts()[AWAITING_REVIEW] == 1


def test_listing_pages_until_short_page():
    s = seeded(n=250)
    items, complete = list_all_open(s, page=100)
    assert len(items) == 250 and complete
