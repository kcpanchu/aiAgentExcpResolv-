"""Durable run state, leases and idempotent execution records.

Portable SQL (SQLite for dev/tests, PostgreSQL on BTP). Claiming is a conditional UPDATE guarded by a
fencing counter (`attempts`), so it needs no SELECT ... FOR UPDATE / SKIP LOCKED and works on both.
HANA would need its own upsert syntax in `_insert_ignore`; not implemented.
"""
import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Callable

from .models import ExceptionType, VimException

DISCOVERED, CLAIMED, INVESTIGATING, PROPOSED = "DISCOVERED", "CLAIMED", "INVESTIGATING", "PROPOSED"
AWAITING_REVIEW, EXECUTING, VERIFYING = "AWAITING_REVIEW", "EXECUTING", "VERIFYING"
RESOLVED, ESCALATED, RESOLVED_BY_HUMAN = "RESOLVED", "ESCALATED", "RESOLVED_BY_HUMAN"

LEASED = {CLAIMED, INVESTIGATING, PROPOSED, EXECUTING, VERIFYING}   # a worker owns these
TERMINAL = {RESOLVED, ESCALATED, RESOLVED_BY_HUMAN}
TRANSITIONS = {
    CLAIMED: {INVESTIGATING, VERIFYING, ESCALATED},
    INVESTIGATING: {PROPOSED, ESCALATED},
    PROPOSED: {EXECUTING, AWAITING_REVIEW, ESCALATED},
    EXECUTING: {VERIFYING, ESCALATED},
    VERIFYING: {RESOLVED, ESCALATED},
}


class LostLease(Exception):
    """Another worker reclaimed this run (or it moved on). Stop touching it."""


class IllegalTransition(Exception):
    pass


def exception_version(exc: VimException) -> str:
    """Identity of the exception *as the agent sees it*. Free-text notes are excluded on purpose so an
    appended email does not spawn a new run; any structured change does."""
    key = json.dumps([exc.doc_id, exc.exception_type.value, exc.vendor, exc.company_code, exc.amount,
                      exc.currency, exc.po_number, exc.invoice_ref], sort_keys=True)
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _to_json(obj) -> str:
    return json.dumps(obj, default=lambda o: asdict(o) if is_dataclass(o) else str(o))


@dataclass
class Run:
    run_id: str
    doc_id: str
    exc_version: str
    state: str
    attempts: int
    lease_owner: str | None
    prev_state: str | None
    payload: dict = field(repr=False, default_factory=dict)

    @property
    def exception(self) -> VimException:
        p = dict(self.payload)
        p["exception_type"] = ExceptionType(p["exception_type"])
        return VimException(**p)


_DDL = [
    """CREATE TABLE IF NOT EXISTS run (
        run_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, exc_version TEXT NOT NULL, state TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, lease_owner TEXT, lease_expires DOUBLE PRECISION,
        next_attempt_at DOUBLE PRECISION NOT NULL DEFAULT 0, prev_state TEXT, last_error TEXT,
        payload TEXT NOT NULL, outcome TEXT,
        created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL,
        UNIQUE (doc_id, exc_version))""",
    "CREATE INDEX IF NOT EXISTS run_state_idx ON run (state, next_attempt_at)",
    """CREATE TABLE IF NOT EXISTS event (
        run_id TEXT NOT NULL, seq INTEGER NOT NULL, ts DOUBLE PRECISION NOT NULL, kind TEXT NOT NULL,
        data TEXT NOT NULL, PRIMARY KEY (run_id, seq))""",
    """CREATE TABLE IF NOT EXISTS execution (
        run_id TEXT NOT NULL, action TEXT NOT NULL, request TEXT NOT NULL, response TEXT,
        dry_run INTEGER NOT NULL, started_at DOUBLE PRECISION NOT NULL, finished_at DOUBLE PRECISION,
        PRIMARY KEY (run_id, action))""",
]


class Store:
    def __init__(self, conn, paramstyle: str = "qmark", clock: Callable[[], float] = time.time,
                 max_attempts: int = 3):
        self.conn, self.ps, self.now, self.max_attempts = conn, paramstyle, clock, max_attempts
        for stmt in _DDL:
            self._exec(stmt)
        self.conn.commit()

    # ---------------- plumbing ----------------
    def _sql(self, q: str) -> str:
        return q.replace("?", "%s") if self.ps == "format" else q

    def _exec(self, q: str, args: tuple = ()):
        cur = self.conn.cursor()
        cur.execute(self._sql(q), args)
        return cur

    def _write(self, q: str, args: tuple = ()) -> int:
        cur = self._exec(q, args)
        self.conn.commit()
        return cur.rowcount

    def _insert_ignore(self, q: str, args: tuple) -> bool:
        """INSERT ... ON CONFLICT DO NOTHING (SQLite >= 3.24 and PostgreSQL). True if a row was inserted."""
        return self._write(q + " ON CONFLICT DO NOTHING", args) == 1

    # ---------------- intake ----------------
    def enqueue(self, exc: VimException) -> tuple[str, bool]:
        """Idempotent on (doc_id, exception_version). Returns (run_id, created)."""
        ver, now, rid = exception_version(exc), self.now(), uuid.uuid4().hex
        payload = _to_json(asdict(exc))
        created = self._insert_ignore(
            "INSERT INTO run (run_id, doc_id, exc_version, state, payload, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)", (rid, exc.doc_id, ver, DISCOVERED, payload, now, now))
        if created:
            return rid, True
        row = self._exec("SELECT run_id FROM run WHERE doc_id=? AND exc_version=?", (exc.doc_id, ver)).fetchone()
        return row[0], False

    # ---------------- claiming ----------------
    def claim_next(self, worker: str, lease_s: float = 600) -> Run | None:
        """Claim the oldest runnable run: never started (and due), or its worker's lease expired."""
        now = self.now()
        leased = ",".join(f"'{s}'" for s in sorted(LEASED))
        rows = self._exec(
            f"SELECT run_id, state, attempts FROM run WHERE "
            f"(state=? AND next_attempt_at<=?) OR (state IN ({leased}) AND lease_expires<?) "
            f"ORDER BY created_at LIMIT 10", (DISCOVERED, now, now)).fetchall()
        for rid, state, attempts in rows:
            if attempts >= self.max_attempts:     # crash-looping run: stop retrying, hand to a human
                self._write("UPDATE run SET state=?, lease_owner=NULL, lease_expires=NULL, outcome=?, updated_at=? "
                            "WHERE run_id=? AND state=? AND attempts=?",
                            (ESCALATED, "max_attempts", now, rid, state, attempts))
                continue
            won = self._write(
                "UPDATE run SET state=?, prev_state=?, lease_owner=?, lease_expires=?, attempts=attempts+1, "
                "updated_at=? WHERE run_id=? AND state=? AND attempts=?",
                (CLAIMED, state, worker, now + lease_s, now, rid, state, attempts))
            if won == 1:
                return self.get_run(rid)
        return None

    def heartbeat(self, run: Run, lease_s: float = 600) -> None:
        n = self._write("UPDATE run SET lease_expires=? WHERE run_id=? AND lease_owner=? AND attempts=? AND state=?",
                        (self.now() + lease_s, run.run_id, run.lease_owner, run.attempts, run.state))
        if n != 1:
            raise LostLease(run.run_id)

    # ---------------- transitions ----------------
    def transition(self, run: Run, to: str, outcome: str | None = None) -> None:
        if to not in TRANSITIONS.get(run.state, set()):
            raise IllegalTransition(f"{run.state} -> {to}")
        keep_lease = to in LEASED
        n = self._write(
            "UPDATE run SET state=?, outcome=COALESCE(?, outcome), lease_owner=?, lease_expires=" +
            ("lease_expires" if keep_lease else "NULL") + ", updated_at=? "
            "WHERE run_id=? AND lease_owner=? AND attempts=? AND state=?",
            (to, outcome, run.lease_owner if keep_lease else None, self.now(),
             run.run_id, run.lease_owner, run.attempts, run.state))
        if n != 1:
            raise LostLease(run.run_id)
        run.state = to
        if not keep_lease:
            run.lease_owner = None

    def fail(self, run: Run, error: str) -> str:
        """Retry with exponential backoff, or escalate once attempts are exhausted. Returns the new state."""
        retry = run.attempts < self.max_attempts
        backoff = min(30 * 2 ** (run.attempts - 1), 1800)
        n = self._write(
            "UPDATE run SET state=?, last_error=?, outcome=?, next_attempt_at=?, lease_owner=NULL, "
            "lease_expires=NULL, updated_at=? WHERE run_id=? AND lease_owner=? AND attempts=?",
            (DISCOVERED if retry else ESCALATED, error[:2000], None if retry else "max_attempts",
             self.now() + backoff, self.now(), run.run_id, run.lease_owner, run.attempts))
        if n != 1:
            raise LostLease(run.run_id)
        run.state = DISCOVERED if retry else ESCALATED
        return run.state

    def reconcile_open(self, open_doc_ids: set[str]) -> int:
        """Runs waiting on a human, or never started, whose document left the open list were resolved in VIM.
        Only call with a COMPLETE listing, or live exceptions will be mislabelled."""
        rows = self._exec("SELECT run_id, doc_id FROM run WHERE state IN (?,?)", (AWAITING_REVIEW, DISCOVERED)).fetchall()
        closed = 0
        for rid, doc in rows:
            if doc not in open_doc_ids:
                closed += self._write(
                    "UPDATE run SET state=?, outcome=?, updated_at=? WHERE run_id=? AND state IN (?,?)",
                    (RESOLVED_BY_HUMAN, "closed_in_vim", self.now(), rid, AWAITING_REVIEW, DISCOVERED))
        return closed

    # ---------------- executions (idempotency) ----------------
    def begin_execution(self, run: Run, action: str, request: dict, dry_run: bool) -> tuple[bool, dict]:
        """True if this call owns the execution. False means it was already attempted: inspect the row, never repeat."""
        created = self._insert_ignore(
            "INSERT INTO execution (run_id, action, request, dry_run, started_at) VALUES (?,?,?,?,?)",
            (run.run_id, action, _to_json(request), int(dry_run), self.now()))
        return created, self.get_execution(run.run_id, action)

    def finish_execution(self, run: Run, action: str, response: dict) -> None:
        self._write("UPDATE execution SET response=?, finished_at=? WHERE run_id=? AND action=?",
                    (_to_json(response), self.now(), run.run_id, action))

    def get_execution(self, run_id: str, action: str | None = None):
        q, args = "SELECT action, request, response, dry_run, started_at, finished_at FROM execution WHERE run_id=?", [run_id]
        if action:
            q, args = q + " AND action=?", args + [action]
        rows = [dict(zip(("action", "request", "response", "dry_run", "started_at", "finished_at"), r))
                for r in self._exec(q, tuple(args)).fetchall()]
        return (rows[0] if rows else {}) if action else rows

    # ---------------- events / reads ----------------
    def add_event(self, run_id: str, kind: str, data: dict) -> None:
        seq = self._exec("SELECT COALESCE(MAX(seq),0)+1 FROM event WHERE run_id=?", (run_id,)).fetchone()[0]
        self._write("INSERT INTO event (run_id, seq, ts, kind, data) VALUES (?,?,?,?,?)",
                    (run_id, seq, self.now(), kind, _to_json(data)))

    def events(self, run_id: str) -> list[dict]:
        return [{"seq": s, "ts": t, "kind": k, "data": json.loads(d)} for s, t, k, d in
                self._exec("SELECT seq, ts, kind, data FROM event WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()]

    def get_run(self, run_id: str) -> Run:
        r = self._exec("SELECT run_id, doc_id, exc_version, state, attempts, lease_owner, prev_state, payload "
                       "FROM run WHERE run_id=?", (run_id,)).fetchone()
        return Run(r[0], r[1], r[2], r[3], r[4], r[5], r[6], json.loads(r[7]))

    def outcome(self, run_id: str) -> str | None:
        return self._exec("SELECT outcome FROM run WHERE run_id=?", (run_id,)).fetchone()[0]

    def counts(self) -> dict[str, int]:
        return dict(self._exec("SELECT state, COUNT(*) FROM run GROUP BY state").fetchall())


def open_store(url: str, **kw) -> Store:
    """sqlite:///path or sqlite:///:memory: ; postgresql://... (BTP PostgreSQL binding uri)."""
    if url.startswith("sqlite:///"):
        return Store(sqlite3.connect(url[len("sqlite:///"):], isolation_level=""), "qmark", **kw)
    if url.startswith(("postgresql://", "postgres://")):
        import psycopg   # optional dependency: pip install "psycopg[binary]"
        return Store(psycopg.connect(url), "format", **kw)
    raise ValueError("unsupported database url (use sqlite:/// or postgresql://)")


class RunAudit:
    """Drop-in for AuditLog that writes into the store, scoped to one run."""

    def __init__(self, store: Store, run: Run, mirror=None):
        self.store, self.run, self.mirror = store, run, mirror

    def log(self, doc_id: str, kind: str, **data):
        self.store.add_event(self.run.run_id, kind, data)
        if self.mirror:
            self.mirror.log(doc_id, kind, run_id=self.run.run_id, **data)
