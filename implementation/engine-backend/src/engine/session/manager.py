"""The session manager — a thin layer = a lock + an event log; state lives in the stores. B8.1/B8.4.

Free chat → promotion (identity + subject → related-incident check → create or join) → a shared
session keyed by the subject = one thread = one run. Session creation is idempotent on the subject id
(never two threads for one incident); promotion carries the free-chat context; membership is
re-checked per event; a 2nd operator's input is queued, not a 2nd run; a gate is answered once.
Clients see updates by polling the event log or streaming it over SSE (`since(seq)` / Last-Event-ID).
One member holds the "pen" (the single writer) at a time; everyone else is a read-only viewer.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from engine.domain import SubjectRef

from .eventlog import EventLog, InMemoryEventLog
from .lock import InMemoryRunLock, RunLock

PEN_TTL = 60.0   # seconds — a pen-holder who hasn't acted within this window can be force-taken (B8.5)


class NotAuthorized(Exception):
    """The actor is not (or is no longer) a member of the session."""


class NotWriter(Exception):
    """The actor is a viewer (does not hold the pen) and tried to write or approve."""


@dataclass
class FreeChat:
    """Per-user, ephemeral, no run/graph/playbook — a TTL'd scratch that may never promote (B8.1)."""

    id: str
    actor: str
    messages: list = field(default_factory=list)
    subject: Optional[SubjectRef] = None


@dataclass
class Session:
    id: str                                              # = subject key "domain/id"
    subject: SubjectRef
    status: str = "active"                               # active | closed
    members: set = field(default_factory=set)
    messages: list = field(default_factory=list)
    input_queue: list = field(default_factory=list)      # 2nd-operator inputs, drained per step/gate
    gate_decisions: dict = field(default_factory=dict)   # gate_id → resolved decision (answered-once)
    pen_holder: Optional[str] = None                     # the ONE current writer ("the pen"); others view
    pen_heartbeat: float = 0.0                           # last writer activity — for the pen TTL (B8.5)


def session_id_for(subject: SubjectRef) -> str:
    # ':' (not '/') so the id is a single URL path segment in the API
    return f"{subject.domain}:{subject.id}"


class SessionManager:
    def __init__(self, lock: Optional[RunLock] = None, event_log: Optional[EventLog] = None,
                 authz: Optional[Callable[[SubjectRef, str], bool]] = None,
                 clock: Optional[Callable[[], float]] = None) -> None:
        self.lock = lock or InMemoryRunLock()
        self.events = event_log or InMemoryEventLog()
        self._authz = authz or (lambda subject, actor: True)
        self._clock = clock or time.monotonic
        self._sessions: dict[str, Session] = {}

    # ── lifecycle (B8.1) ────────────────────────────────────────────────
    def create_or_join(self, subject: SubjectRef, actor: str, *,
                        seed_messages: Optional[list] = None) -> Session:
        """Idempotent on the subject id: the first promoter creates the thread, the rest attach —
        never two threads for one incident (B8.4 create-or-join race)."""
        # admission is gated BEFORE any mutation: an unauthorized actor must not pollute `members` or
        # (as creator) be recorded as the sole pen-holder, which would block legitimate operators.
        if not self._authz(subject, actor):
            raise NotAuthorized(f"{actor} is not authorized for {session_id_for(subject)}")
        sid = session_id_for(subject)
        sess = self._sessions.get(sid)
        if sess is None:
            # the creator starts holding the pen (the one writer); later joiners are viewers
            sess = Session(id=sid, subject=subject, messages=list(seed_messages or []),
                           pen_holder=actor, pen_heartbeat=self._clock())
            self._sessions[sid] = sess
        sess.members.add(actor)
        return sess

    def promote(self, free_chat: FreeChat, subject: SubjectRef, actor: str) -> Session:
        """Promotion is not a reset — the free chat's prior messages seed the session (B8.4)."""
        return self.create_or_join(subject, actor, seed_messages=free_chat.messages)

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    # ── membership (B8.4 authorization mid-session) ─────────────────────
    def is_member(self, sess: Session, actor: str) -> bool:
        return actor in sess.members and self._authz(sess.subject, actor)

    def revoke(self, sess: Session, actor: str) -> None:
        if sess.pen_holder == actor:
            sess.pen_holder = None       # losing access drops the pen too
        sess.members.discard(actor)

    # ── the pen (one writer at a time) ──────────────────────────────────
    def take_pen(self, sess: Session, actor: str) -> bool:
        """Become the writer. Succeeds if the pen is free, already yours, or STALE — a holder who
        hasn't acted within PEN_TTL can be force-taken, so a disconnected operator can't permanently
        block everyone else's writes + gate approvals (B8.5 pen liveness)."""
        if not self.is_member(sess, actor):
            raise NotAuthorized(f"{actor} is not a member of {sess.id}")
        now = self._clock()
        holder = sess.pen_holder
        stale = holder is not None and holder != actor and (now - sess.pen_heartbeat) > PEN_TTL
        if holder in (None, actor) or stale:
            sess.pen_holder = actor
            sess.pen_heartbeat = now
            return True
        return False

    def touch_pen(self, sess: Session, actor: str) -> None:
        """Refresh the pen lease on writer activity (a message / a gate decision)."""
        if sess.pen_holder == actor:
            sess.pen_heartbeat = self._clock()

    def release_pen(self, sess: Session, actor: str) -> bool:
        if sess.pen_holder == actor:
            sess.pen_holder = None
            return True
        return False

    def is_writer(self, sess: Session, actor: str) -> bool:
        return sess.pen_holder == actor and self.is_member(sess, actor)

    def role_of(self, sess: Session, actor: str) -> str:
        return "writer" if self.is_writer(sess, actor) else "viewer"

    def require_writer(self, sess: Session, actor: str) -> None:
        if not self.is_writer(sess, actor):
            raise NotWriter(f"{actor} does not hold the pen for {sess.id}")

    # ── events (B8.3) — append to the log; membership re-checked PER EVENT ──
    def post_event(self, sess: Session, actor: str, event: dict) -> int:
        if not self.is_member(sess, actor):
            raise NotAuthorized(f"{actor} is not a member of {sess.id}")
        self.touch_pen(sess, actor)                      # writer activity refreshes the pen lease
        return self.events.append(sess.id, {**event, "actor": actor})

    # ── input queue (B8.2) — never a 2nd run ────────────────────────────
    def enqueue_input(self, sess: Session, message: dict) -> None:
        sess.input_queue.append(message)

    def drain_inputs(self, sess: Session) -> list:
        items = list(sess.input_queue)
        sess.input_queue.clear()
        return items

    # ── gate (B8.4 answered-once) ───────────────────────────────────────
    def answer_gate(self, sess: Session, gate_id: str, decision: str, actor: str) -> dict:
        """First wins; concurrent answers see it resolved — idempotent on the decision. The
        writer-check and terminal-decision rule live HERE (not only in the API), so a second caller
        can't bypass them — but BOTH run AFTER the answered-once short-circuit, so an already-resolved
        gate stays idempotent even for a viewer."""
        if gate_id in sess.gate_decisions:
            return sess.gate_decisions[gate_id]
        self.require_writer(sess, actor)                 # only the pen-holder decides a gate (B8.5)
        self.touch_pen(sess, actor)                      # a gate decision is writer activity
        if decision not in ("approve", "deny"):          # 'refine' is non-terminal — kept out of the store
            raise ValueError(f"answer_gate records only terminal decisions (approve|deny), not {decision!r}")
        resolved = {"gate_id": gate_id, "decision": decision, "actor": actor}
        sess.gate_decisions[gate_id] = resolved
        return resolved
