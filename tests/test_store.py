import pytest

from vim_agent.models import ExceptionType, VimException
from vim_agent.store import (AWAITING_REVIEW, CLAIMED, DISCOVERED, ESCALATED, INVESTIGATING, RESOLVED_BY_HUMAN,
                             IllegalTransition, LostLease, Store, exception_version, open_store)


class Clock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t


def exc(doc="D1", amount=100.0, notes=""):
    return VimException(doc, "1000", "V1", ExceptionType.PRICE_VARIANCE, amount, "USD", po_number="4500001", notes=notes)


@pytest.fixture
def clock(): return Clock()


@pytest.fixture
def store(tmp_path, clock):
    return open_store(f"sqlite:///{tmp_path / 's.db'}", clock=clock)


def second_store(tmp_path, clock, **kw):
    return open_store(f"sqlite:///{tmp_path / 's.db'}", clock=clock, **kw)


def test_enqueue_is_idempotent_and_version_aware(store):
    rid, created = store.enqueue(exc())
    assert created and store.enqueue(exc()) == (rid, False)
    assert store.enqueue(exc(notes="new email text"))[1] is False       # notes do not change identity
    assert store.enqueue(exc(amount=250.0))[1] is True                   # structured change = new run
    assert exception_version(exc()) != exception_version(exc(amount=1))


def test_exception_round_trips_through_payload(store):
    store.enqueue(exc(notes="x"))
    run = store.claim_next("w1")
    assert run.exception == exc(notes="x")


def test_claim_is_exclusive_across_connections(tmp_path, clock, store):
    other = second_store(tmp_path, clock)
    store.enqueue(exc("D1")); store.enqueue(exc("D2"))
    a, b = store.claim_next("w1"), other.claim_next("w2")
    assert {a.doc_id, b.doc_id} == {"D1", "D2"}
    assert store.claim_next("w1") is None and other.claim_next("w2") is None


def test_expired_lease_is_reclaimed_and_old_worker_fenced(tmp_path, clock, store):
    other = second_store(tmp_path, clock)
    store.enqueue(exc())
    old = store.claim_next("w1", lease_s=60)
    store.transition(old, INVESTIGATING)
    assert other.claim_next("w2") is None                 # lease still valid
    clock.t += 61
    new = other.claim_next("w2", lease_s=60)
    assert new.run_id == old.run_id and new.attempts == 2 and new.prev_state == INVESTIGATING
    with pytest.raises(LostLease):
        store.transition(old, "PROPOSED")                 # zombie worker cannot write
    with pytest.raises(LostLease):
        store.heartbeat(old)


def test_heartbeat_extends_lease(store, clock):
    store.enqueue(exc())
    run = store.claim_next("w1", lease_s=60)
    clock.t += 50
    store.heartbeat(run, lease_s=60)
    clock.t += 50
    assert store.claim_next("w2") is None


def test_illegal_transition_rejected(store):
    store.enqueue(exc())
    run = store.claim_next("w1")
    with pytest.raises(IllegalTransition):
        store.transition(run, "RESOLVED")


def test_fail_backs_off_then_escalates(store, clock):
    store.enqueue(exc())
    run = store.claim_next("w1")
    assert store.fail(run, "boom") == DISCOVERED
    assert store.claim_next("w1") is None                 # backoff not elapsed
    clock.t += 31
    run = store.claim_next("w1")
    assert run.attempts == 2 and store.fail(run, "boom") == DISCOVERED
    clock.t += 61
    run = store.claim_next("w1")
    assert store.fail(run, "boom") == ESCALATED and store.outcome(run.run_id) == "max_attempts"


def test_crash_looping_run_is_escalated_not_reclaimed(store, clock):
    store.enqueue(exc())
    for _ in range(3):
        store.claim_next("w", lease_s=10)
        clock.t += 11
    assert store.claim_next("w") is None
    assert store.counts() == {ESCALATED: 1}


def test_execution_is_idempotent(store):
    store.enqueue(exc())
    run = store.claim_next("w1")
    owns, _ = store.begin_execution(run, "APPROVE_VARIANCE", {"p": 1}, False)
    again, row = store.begin_execution(run, "APPROVE_VARIANCE", {"p": 1}, False)
    assert owns and not again and row["finished_at"] is None
    store.finish_execution(run, "APPROVE_VARIANCE", {"ok": True})
    assert store.get_execution(run.run_id, "APPROVE_VARIANCE")["finished_at"] is not None


def test_reconcile_marks_human_resolved_only_for_waiting_runs(store):
    store.enqueue(exc("D1")); store.enqueue(exc("D2")); store.enqueue(exc("D3"))
    r = store.claim_next("w1")                            # D1 in progress: must not be touched
    r2 = store.claim_next("w1")
    store.transition(r2, INVESTIGATING); store.transition(r2, "PROPOSED"); store.transition(r2, AWAITING_REVIEW)
    assert store.reconcile_open(set()) == 2               # D2 (awaiting) and D3 (never started)
    c = store.counts()
    assert c[RESOLVED_BY_HUMAN] == 2 and c[CLAIMED] == 1


def test_events_are_ordered(store):
    rid, _ = store.enqueue(exc())
    store.add_event(rid, "a", {"x": 1}); store.add_event(rid, "b", {"y": 2})
    assert [e["kind"] for e in store.events(rid)] == ["a", "b"]
