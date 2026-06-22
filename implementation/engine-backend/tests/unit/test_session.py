"""P5 · live session (B8) — unit tests.

The lock serializes the one run (lease + heartbeat → slow vs crashed owner); the event log is one
ordered stream (snapshot + resume-from-seq); the manager handles the B8.4 edge cases:
create-or-join race (never two threads), promotion-carries-context, membership-rechecked-per-event,
input-queued-not-a-2nd-run, gate-answered-once.
"""
from __future__ import annotations

import pytest

from engine.domain import SubjectRef
from engine.session import (
    FreeChat,
    InMemoryEventLog,
    InMemoryRunLock,
    NotAuthorized,
    NotWriter,
    SessionManager,
    session_id_for,
)

SUBJECT = SubjectRef(domain="app-incident", id="INC-4821", kind="incident")


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


# ── lock (B8.2) ─────────────────────────────────────────────────────────
def test_lock_serializes_one_owner():
    lock = InMemoryRunLock()
    token = lock.acquire("s1", "owner-A")
    assert token is not None
    assert lock.acquire("s1", "owner-B") is None         # held → no second run
    assert lock.holder("s1") == "owner-A"


def test_lock_release_frees_it():
    lock = InMemoryRunLock()
    token = lock.acquire("s1", "owner-A")
    assert lock.release("s1", token) is True
    assert lock.acquire("s1", "owner-B") is not None


def test_slow_owner_heartbeats_to_keep_the_lease():
    clock = FakeClock()
    lock = InMemoryRunLock(clock=clock)
    token = lock.acquire("s1", "owner-A", ttl=10)
    clock.advance(8)
    assert lock.heartbeat("s1", token, ttl=10) is True   # slow tool — still working
    clock.advance(8)                                      # 16 total, but lease renewed at t=8 → 18
    assert lock.holder("s1") == "owner-A"
    assert lock.acquire("s1", "owner-B") is None


def test_crashed_owner_lease_expires_and_is_stolen():
    clock = FakeClock()
    lock = InMemoryRunLock(clock=clock)
    lock.acquire("s1", "owner-A", ttl=10)                 # no heartbeats — the owner died
    clock.advance(11)
    assert lock.holder("s1") is None                      # lease expired
    assert lock.acquire("s1", "owner-B") is not None      # another server resumes (B8.2)


# ── event log (B8.3, polled) ──────────────────────────────────────────────
def test_event_log_assigns_monotonic_seq():
    log = InMemoryEventLog()
    assert log.append("s1", {"kind": "msg", "text": "a"}) == 1
    assert log.append("s1", {"kind": "msg", "text": "b"}) == 2
    assert log.snapshot_seq("s1") == 2
    assert [e["text"] for e in log.since("s1")] == ["a", "b"]


def test_event_log_poll_resume_from_seq():
    log = InMemoryEventLog()
    log.append("s1", {"text": "a"})
    snap = log.snapshot_seq("s1")                         # a client polled up to here
    log.append("s1", {"text": "b"})
    log.append("s1", {"text": "c"})
    resumed = log.since("s1", after_seq=snap)             # next poll → only the new deltas
    assert [e["text"] for e in resumed] == ["b", "c"]
    assert [e["seq"] for e in resumed] == [2, 3]


# ── manager lifecycle + B8.4 edge cases ─────────────────────────────────
def test_create_or_join_is_idempotent_on_subject():
    mgr = SessionManager()
    a = mgr.create_or_join(SUBJECT, "alice")
    b = mgr.create_or_join(SUBJECT, "bob")               # race → attaches, never a 2nd thread
    assert a is b
    assert a.id == session_id_for(SUBJECT) == "app-incident:INC-4821"
    assert a.members == {"alice", "bob"}


def test_promotion_carries_context():
    mgr = SessionManager()
    fc = FreeChat(id="fc1", actor="alice", messages=[{"text": "checkout is slow"}])
    sess = mgr.promote(fc, SUBJECT, "alice")
    assert sess.messages == [{"text": "checkout is slow"}]   # promotion is not a reset


def test_membership_rechecked_per_event():
    mgr = SessionManager()
    sess = mgr.create_or_join(SUBJECT, "alice")
    assert mgr.post_event(sess, "alice", {"kind": "msg"}) == 1
    mgr.revoke(sess, "alice")                              # access revoked mid-session
    with pytest.raises(NotAuthorized):
        mgr.post_event(sess, "alice", {"kind": "msg"})    # dropped on the next event


def test_external_authz_rechecked_per_event():
    revoked: set[str] = set()
    mgr = SessionManager(authz=lambda subject, actor: actor not in revoked)
    sess = mgr.create_or_join(SUBJECT, "alice")
    assert mgr.post_event(sess, "alice", {"kind": "msg"}) == 1
    revoked.add("alice")
    with pytest.raises(NotAuthorized):
        mgr.post_event(sess, "alice", {"kind": "msg"})


def test_input_is_queued_not_a_second_run():
    mgr = SessionManager()
    sess = mgr.create_or_join(SUBJECT, "alice")
    mgr.enqueue_input(sess, {"text": "also check the DB"})
    mgr.enqueue_input(sess, {"text": "and the cache"})
    drained = mgr.drain_inputs(sess)
    assert [m["text"] for m in drained] == ["also check the DB", "and the cache"]
    assert sess.input_queue == []                          # drained after the step


def test_gate_answered_once_first_wins():
    mgr = SessionManager()
    sess = mgr.create_or_join(SUBJECT, "alice")
    mgr.create_or_join(SUBJECT, "bob")
    first = mgr.answer_gate(sess, "gate-a1", "approve", "alice")
    second = mgr.answer_gate(sess, "gate-a1", "deny", "bob")  # concurrent → sees it resolved
    assert first == second
    assert first["decision"] == "approve" and first["actor"] == "alice"


# ── the pen (one writer at a time) ──────────────────────────────────────
def test_creator_holds_the_pen_joiner_is_viewer():
    mgr = SessionManager()
    sess = mgr.create_or_join(SUBJECT, "alice")          # creator → holds the pen
    mgr.create_or_join(SUBJECT, "bob")                   # joiner → viewer
    assert sess.pen_holder == "alice"
    assert mgr.is_writer(sess, "alice") and not mgr.is_writer(sess, "bob")
    assert mgr.role_of(sess, "alice") == "writer" and mgr.role_of(sess, "bob") == "viewer"


def test_pen_is_one_at_a_time():
    mgr = SessionManager()
    sess = mgr.create_or_join(SUBJECT, "alice")
    mgr.create_or_join(SUBJECT, "bob")
    assert mgr.take_pen(sess, "bob") is False            # alice still holds it
    assert mgr.release_pen(sess, "alice") is True
    assert mgr.take_pen(sess, "bob") is True             # now free → bob takes it
    assert mgr.is_writer(sess, "bob") and not mgr.is_writer(sess, "alice")


def test_require_writer_raises_for_viewer():
    mgr = SessionManager()
    sess = mgr.create_or_join(SUBJECT, "alice")
    mgr.create_or_join(SUBJECT, "bob")
    mgr.require_writer(sess, "alice")                    # the pen-holder — no raise
    with pytest.raises(NotWriter):
        mgr.require_writer(sess, "bob")                  # a viewer — blocked


def test_revoke_drops_the_pen():
    mgr = SessionManager()
    sess = mgr.create_or_join(SUBJECT, "alice")
    mgr.revoke(sess, "alice")
    assert sess.pen_holder is None                       # losing access drops the pen too
