"""InvestigationSession — the interactive DRIVER + TRANSPORT around the deterministic fold
(DEPTH-BUILD-PLAN §C / VALIDATION-VERDICT §C). It wraps an `Engine` and drives it via
`step()`, turning the batch while-loop into a re-enterable, human-in-the-loop conversation
without changing the engine or the batch `run()` path.

Two things this layer adds, both by COMPOSITION (no engine edits):

1. **A write-gate that suspends before the side-effect.** At a `writes_allowed` phase whose
   plan contains a WRITE-effect capability call, the session pauses the engine BEFORE the
   write delta is applied (a wrapped planner raises `_GateSuspend` after the plan is computed
   but before the layer serves the write). It emits a `gate_opened` event carrying the
   proposed action + the serving hypothesis and its evidence, then waits. On **approve** it
   re-runs the phase with the write intact (the layer serves it under the approved gate); on
   **refine** it edits the call params first; on **deny** it strips the write and records the
   denial as a synthetic hypothesis store result fed back so the next plan replans — a divergent journal.

2. **An ordered event stream** derived purely from what the engine already recorded (the
   journal / graph / hypothesis store) — `phase_started`, `reasoning`, `capability_call`, `graph_delta`
   (each node WITH its `created_by` seq), `hypotheses_delta`, `gate_opened`, `session_state`.
   Nothing here is invented: every delta is read back off the PhaseResult the engine folded.

The session id is the investigation identity (`subject.key`); the journal is the checkpointer,
so `snapshot()` is export_bundle-shaped and the whole state is reconstructable by journal
replay. A `SessionManager` keeps a registry so many incidents can be listed + reopened
(including CLOSED ones).
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from ..api.bundle import export_bundle, phase_rail
from ..capability.layer import CapabilityCall, CapabilityLayer
from ..domain.enums import Effect, Source, VerdictStatus
from ..domain.operations import UpdateHypothesis
from ..domain.playbook import Playbook
from ..domain.registry import subject_node_id
from ..domain.subject import SubjectRef
from .engine import Engine
from .planner import PlanContext, Planner, PlanOutput
from .store import InvestigationStore


class SessionState(StrEnum):
    RUNNING = "running"
    SUSPENDED = "suspended"          # paused at an open write-gate awaiting a human decision
    AWAITING_REVIEW = "awaiting_review"   # paused at a between-phases DIRECTION review (owner 2026-07-23)
    CLOSED = "closed"


class GateDecision(StrEnum):
    APPROVE = "approve"       # apply the proposed write + continue
    REFINE = "refine"         # edit the write params, then apply + continue
    DENY = "deny"             # drop the write; record the denial as feedback → replan


class ReviewDecision(StrEnum):
    """A human DIRECTION decision on a phase-review (owner 2026-07-23). Reuses the approve/refine/
    deny vocabulary but means direction, not a write: APPROVE advances to the proposed next phase;
    REFINE re-runs the just-completed phase with the operator's steer as a message; DENY halts the
    investigation (terminal)."""

    APPROVE = "approve"       # advance to the proposed next phase
    REFINE = "refine"         # re-enter/repeat the completed phase with the steer as an operator message
    DENY = "deny"             # halt the investigation — terminal


class CloseCause(StrEnum):
    """WHY a session terminated (M17). Every terminal path funnels through `_finalize`, which
    writes ONE `closed` lifecycle record carrying this cause — so 'did it terminate, and why' is a
    single journal read. Before M17, four paths emitted three event names (closed / max_steps_
    exhausted / error) and two skipped a `closed` record, so the answer took three reads."""

    FINISHED = "finished"     # the engine's phase route completed — a DONE terminal, or an unrouted
    #                           verdict that drained the route (both leave current_phase None)
    EXHAUSTED = "exhausted"   # max_steps hit before a terminal — the interactive step budget ran out
    ERROR = "error"           # a live LLM/transport failure crashed the drive mid-run
    DENIED = "denied"         # a human DENIED direction at a phase-review — halted, terminal


class _GateSuspend(Exception):
    """Internal control-flow signal: raised by the wrapped planner (after the plan is computed,
    before the write is served) so the engine step unwinds cleanly with nothing applied. Only
    the wrapped planner used by a session ever raises it, so the batch run() path is untouched."""


@dataclass
class _Pending:
    phase: str                    # playbook phase id the gate is open on
    plan: PlanOutput              # the peeked plan (cached so re-entry never re-consumes the planner)
    write_calls: list[CapabilityCall]
    gate_id: str


@dataclass
class _Decision:
    decision: GateDecision
    params: dict = field(default_factory=dict)
    reason: str = ""


@dataclass
class _PendingReview:
    """A between-phases DIRECTION review awaiting a human decision (owner 2026-07-23). Parallels
    `_Pending` (the write-gate) but sits in its OWN slot + state so decisions never mis-route:
    the write-gate suspends mid-step (before a write); a review suspends between steps (before the
    NEXT phase runs)."""

    from_phase: str          # the phase that just completed its goal
    to_phase: str            # the phase the engine proposes to advance to
    review_id: str
    payload: dict            # the assembled summary (also the phase_review_opened event body)


class _GatePlanner:
    """Wraps the real planner. On a `writes_allowed` phase whose plan carries a WRITE-effect
    call it stashes the plan and raises `_GateSuspend` (suspend BEFORE the write). On the
    resume step it returns the operator-decided plan for that same phase — never re-calling the
    inner planner, so a ScriptedPlanner's cursor stays consistent and the run is deterministic."""

    def __init__(self, inner: Planner, session: InvestigationSession) -> None:
        self._inner = inner
        self._session = session

    def plan(self, ctx: PlanContext) -> PlanOutput:
        s = self._session
        ctx.messages = list(s._messages)                 # two-way steering into the live planner (obs 2)
        if s._pending is not None and s._pending.phase == ctx.phase:
            return s._resolve_pending_plan(ctx)          # resume: the decided plan for this phase
        out = self._inner.plan(ctx)
        write_calls = s._write_calls(ctx, out)
        if write_calls:
            s._open_gate(ctx, out, write_calls)          # suspend before the write delta
            raise _GateSuspend
        return out


class InvestigationSession:
    """One resumable investigation, driven step-by-step with a human in the write-gate."""

    def __init__(self, subject: SubjectRef, playbook: Playbook, planner: Planner, *,
                 layer: CapabilityLayer | None = None,
                 clock: Callable[[], datetime] | None = None, max_steps: int = 60,
                 background_drive: bool = False, auto_review: bool = True,
                 store: InvestigationStore | None = None) -> None:
        self.subject = subject
        self.id = subject.key
        # PHASE-REVIEW mode (owner 2026-07-23). auto_review=True (the DEFAULT) is the
        # NON-INTERACTIVE / hermetic mode the owner mandates for scripted planners: reviewable
        # transitions AUTO-APPROVE (never suspend, exactly as the write-gate is structurally absent
        # in batch), so a scripted/CI session drives straight through and never hangs. auto_review=
        # False is the INTERACTIVE workbench mode (the LivePlanner backend): a reviewable transition
        # SUSPENDS for the real human's direction approval. The batch Engine.run() path never
        # constructs a session at all, so goldens are untouched either way.
        self._auto_review = auto_review
        # the SUBJECT under investigation → renders as node #1 (obs 1). P7 step 5: derived
        # from the playbook's subject_node role binding — "the incident is the first node"
        # is playbook data, not a session-coded convention.
        self._origin_id = subject_node_id(playbook.subject_node, subject.id)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._engine = Engine(playbook, _GatePlanner(planner, self), clock=clock, layer=layer)
        # hand a LIVE planner the direct graph ref (full view, parity with run_live);
        # a ScriptedPlanner has no `graph` attribute, so this is a no-op for the mock path.
        if hasattr(planner, "graph"):
            planner.graph = self._engine.graph
        self._engine.start(subject, max_steps=max_steps)
        # JOURNAL v2 lifecycle (part2 §1/§3): the run START is durable — the owner goal requires
        # started/resumed/exhausted/closed; 'started' + 'resumed' were documented but never
        # emitted. This is the interactive run-start (the batch run() has no session lifecycle);
        # it shares the ONE seq counter, so the interactive journal stays gap-free.
        self._engine.journal.append_lifecycle(
            "started", phase_id=self._engine.current_phase,
            detail={"subject": subject.key, "max_steps": max_steps})
        self.state = SessionState.RUNNING
        self._events: list[dict] = []
        self._event_seq = 0
        self._inv_cursor = 0                 # how many engine invocations have been streamed
        self._pending: _Pending | None = None
        self._decision: _Decision | None = None
        self._gate_count = 0
        # PHASE-REVIEW slots (owner 2026-07-23) — DISTINCT from the write-gate's `_pending` so the
        # decision router (answer_gate vs answer_review) never mis-fires. `_gated_phases` records
        # which phases opened a write-gate this run, so their phase-review is SUBSUMED (dedup: one
        # pause at Act, not a write-gate then a redundant advance-review).
        self._pending_review: _PendingReview | None = None
        self._review_count = 0
        self._gated_phases: set[str] = set()
        self._messages: list[dict] = []      # operator steering / answers
        self._outcome: str = "open"
        # background drive: a live LLM phase is seconds of latency — run the drive loop off the
        # HTTP thread so endpoints return immediately and the SSE stream shows incremental progress.
        self._background = background_drive
        self._drive_lock = threading.Lock()
        self._driving = False
        # durability: land the journal on disk as the session drives so it survives a restart
        # (a read-only reopen is served from disk by the SessionManager). None = no persistence,
        # so a directly-constructed session (the hermetic unit tests) is unchanged.
        self._store = store
        self._persisted_journal = 0
        if self._store is not None:
            self._store.reset(self.id)   # fresh run overwrites any prior on-disk run of this id

    # ── public driving surface ─────────────────────────────────────────────────
    def advance(self, *, after: int | None = None) -> list[dict]:
        """Step to the next pause (open gate) or to close. Returns the events produced.

        In background mode the drive runs off-thread (returns immediately; watch the SSE stream);
        otherwise it drives inline (deterministic — the mock/test path)."""
        start = self._event_seq if after is None else after
        if self._background:
            self._drive_async()
        else:
            self._drive()
        return self.events(after=start)

    def answer_gate(self, decision: GateDecision | str, *, params: dict | None = None,
                    reason: str = "", actor: str = "operator") -> list[dict]:
        """Resolve an open write-gate (approve | refine | deny) and continue to the next pause.

        Records WHO decided and WHEN in the durable journal (a `step` entry carrying
        `actor` + `decision` + `Source.HUMAN`) and on the event stream, so the journal shows
        the human in the write-gate — not just the phase the approval unblocked."""
        if self.state != SessionState.SUSPENDED or self._pending is None:
            raise RuntimeError("no open gate to answer")
        start = self._event_seq
        dec = GateDecision(decision)
        self._decision = _Decision(dec, params=params or {}, reason=reason)
        self._record_gate_decision(dec, actor=actor, reason=reason, params=params or {})
        # JOURNAL v2 lifecycle: the run RESUMES from the suspended write-gate — a durable
        # 'resumed' record (owner goal: started/resumed/exhausted/closed). It follows the human's
        # gate_decision (question → answer → resume) and precedes the re-drive of the gated phase.
        self._engine.journal.append_lifecycle(
            "resumed", phase_id=self._pending.phase,
            detail={"decision": dec.value, "actor": actor})
        self.state = SessionState.RUNNING
        if self._background:
            self._drive_async()
        else:
            self._drive()
        return self.events(after=start)

    def answer_review(self, decision: ReviewDecision | str, *, text: str = "",
                      actor: str = "operator") -> list[dict]:
        """Resolve an open phase-review — APPROVE (advance to the proposed next phase) · REFINE
        (re-run the just-completed phase with `text` as an operator steer) · DENY (halt, terminal).

        The DIRECTION counterpart to answer_gate(): the write-gate approves the irreversible ACTION;
        a phase-review approves the DIRECTION at a phase transition. Records WHO decided + WHEN in
        the durable journal (a `review_decision` entry, Source.HUMAN) and on the event stream, then
        continues to the next pause (approve/refine) or closes the run (deny)."""
        if self.state != SessionState.AWAITING_REVIEW or self._pending_review is None:
            raise RuntimeError("no open phase-review to answer")
        start = self._event_seq
        dec = ReviewDecision(decision)
        pr = self._pending_review
        self._record_review_decision(dec, actor=actor, reason=text)
        self.state = SessionState.RUNNING
        if dec == ReviewDecision.DENY:
            self._pending_review = None
            self._deny_close(pr, actor=actor)
            return self.events(after=start)
        # JOURNAL v2 lifecycle: the run RESUMES from the review (question → answer → resume),
        # mirroring the write-gate's resumed record so the interactive journal stays diagnosable.
        self._engine.journal.append_lifecycle(
            "resumed", phase_id=pr.from_phase,
            detail={"review": dec.value, "actor": actor, "to_phase": pr.to_phase})
        if dec == ReviewDecision.REFINE:
            # re-enter/repeat the just-completed phase with the steer buffered for the (live)
            # planner as an operator message; the engine pointer moves BACK to that phase.
            if text:
                self.add_message(text, actor=actor)
            self._engine.reenter_phase(pr.from_phase)
        self._pending_review = None          # APPROVE leaves the engine pointed at the next phase
        if self._background:
            self._drive_async()
        else:
            self._drive()
        return self.events(after=start)

    # ── background drive (live path — long LLM latency off the HTTP thread) ──────
    def _drive_async(self) -> None:
        """Start the drive loop on a daemon thread if one isn't already running (idempotent, so a
        double POST /advance can't spawn two)."""
        with self._drive_lock:
            if self._driving:
                return
            self._driving = True
        threading.Thread(target=self._drive_and_clear, daemon=True).start()

    def _drive_and_clear(self) -> None:
        try:
            self._drive()
        except Exception as exc:                          # a live LLM/transport failure mid-drive
            detail = f"{type(exc).__name__}: {exc}"
            self._emit("session_error", message=detail)
            # ONE terminal record carrying cause=error (M17); outcome='error' so a crashed run
            # never reports 'open' on list_view or persisted meta (M18). The durable write is
            # best-effort — the run is already dead — but the close still marks CLOSED + streams.
            self._finalize(CloseCause.ERROR, outcome="error", detail={"error": detail},
                           best_effort=True)
        finally:
            with self._drive_lock:
                self._driving = False

    def _record_gate_decision(self, decision: GateDecision, *, actor: str, reason: str,
                              params: dict) -> None:
        """Journal the human gate answer (source-of-truth) + emit a `gate_decision` event.
        JOURNAL v2: a typed `gate_decision` entry whose seq is assigned at append — nothing
        reserved, so the suspended phase behind it burned no seq (gap-free journal)."""
        p = self._pending
        assert p is not None
        write = p.write_calls[0] if p.write_calls else None
        action = {"gate_id": p.gate_id,
                  "intent": write.intent if write else None,
                  "params": {**(dict(write.params) if write else {}), **params}}
        self._engine.journal.append_gate_decision(
            p.phase, intent=write.intent if write else "gate",
            reasoning=reason or f"gate {decision.value} by {actor}",
            action=action, observation={"decision": decision.value, "actor": actor},
            decision=decision.value, actor=actor)
        self._emit("gate_decision", gate_id=p.gate_id, decision=decision.value,
                   actor=actor, source=Source.HUMAN.value, reason=reason,
                   phase=p.phase)

    def _record_review_decision(self, decision: ReviewDecision, *, actor: str,
                                reason: str) -> None:
        """Journal the human DIRECTION answer (source-of-truth) + emit a `phase_review_decision`
        event. A typed `review_decision` entry whose seq is assigned at append — nothing reserved,
        so the completed phase behind it keeps its seq gap-free (parallels append_gate_decision)."""
        pr = self._pending_review
        assert pr is not None
        self._engine.journal.append_review_decision(
            pr.from_phase, review_id=pr.review_id, to_phase=pr.to_phase,
            decision=decision.value, actor=actor, reason=reason)
        self._emit("phase_review_decision", review_id=pr.review_id, decision=decision.value,
                   actor=actor, source=Source.HUMAN.value, reason=reason,
                   phase=pr.from_phase, to_phase=pr.to_phase)

    def _deny_close(self, pr: _PendingReview, *, actor: str) -> None:
        """DENY halts the investigation at the review — terminal. The unified `closed` lifecycle
        record names the cause (denied) + WHY (phase_review_denied) so the run ends DIAGNOSABLY
        (never a zombie), and the SSE stream closes. The outcome is the engine's terminal label
        (open — close was never reached)."""
        self._finalize(CloseCause.DENIED, phase_id=pr.from_phase,
                       detail={"reason": "phase_review_denied", "actor": actor,
                               "to_phase": pr.to_phase})

    def add_message(self, text: str, *, actor: str = "operator") -> dict:
        """Record an operator turn in the two-way chat (obs 2). The message becomes a first-class
        `user_message` event on the stream AND a `step` entry in the durable journal (Source.HUMAN),
        and is buffered so the LIVE planner sees it as steering on its next plan (via _GatePlanner).
        It does not itself mutate the graph/hypothesis store fold — the planner decides what to do with it."""
        kind = "answer" if self.state == SessionState.SUSPENDED else "steer"
        msg = {"seq": len(self._messages) + 1, "text": text, "at": self._now(),
               "kind": kind, "actor": actor}
        self._messages.append(msg)
        phase = self._engine.current_phase
        self._engine.journal.append_message(phase, text=text, message_kind=kind, actor=actor)
        self._emit("user_message", text=text, kind=kind, actor=actor,
                   source=Source.HUMAN.value, phase=phase)
        self._persist()                      # operator turns are journal entries — keep them durable
        return msg

    # ── event access ────────────────────────────────────────────────────────────
    def events(self, *, after: int = 0) -> list[dict]:
        return [e for e in self._events if e["seq"] > after]

    @property
    def outcome(self) -> str:
        return self._outcome

    @property
    def pending_gate(self) -> dict | None:
        """The last gate_opened payload while SUSPENDED (for reconnect / cold-load)."""
        if self.state != SessionState.SUSPENDED:
            return None
        for e in reversed(self._events):
            if e["type"] == "gate_opened":
                return e
        return None

    @property
    def pending_review(self) -> dict | None:
        """The last phase_review_opened payload while AWAITING_REVIEW (for reconnect / cold-load).
        Distinct from pending_gate so the two suspend surfaces never collide."""
        if self.state != SessionState.AWAITING_REVIEW:
            return None
        for e in reversed(self._events):
            if e["type"] == "phase_review_opened":
                return e
        return None

    def snapshot(self) -> dict:
        """export_bundle-shaped cold-load payload (+ session envelope). The engine's journal is
        the checkpointer, so `graph`/`hypothesis store` here equal a fresh journal replay."""
        res = self._engine.result()
        bundle = export_bundle(res)
        return {
            **bundle,
            "session_id": self.id,
            "state": self.state.value,
            # the full declared phase rail as data (M22) — the UI stepper reads THIS instead of a
            # hardcoded ALL_PHASES; playbook context, so it rides the snapshot envelope not the bundle.
            "phase_rail": phase_rail(self._engine.playbook),
            "pending_gate": self.pending_gate,
            "pending_review": self.pending_review,
            "messages": list(self._messages),
            "events": list(self._events),
        }

    def list_view(self) -> dict:
        return {"id": self.id, "subject": self.subject.model_dump(),
                "state": self.state.value, "outcome": self._outcome}

    # ── the drive loop (step the engine, translate deltas → events) ─────────────
    def _drive(self) -> None:
        while not self._engine.done():
            resolving_gate = self._decision is not None
            # phase-scope the live fixture transport before each step (parity with run_live) so a
            # provider can return the CURRENT world-state per phase (e.g. recovery in verify).
            src = getattr(self._engine.layer, "source", None) if self._engine.layer else None
            cur = self._engine.current_phase
            if src is not None and cur is not None and hasattr(src, "phase"):
                src.phase = cur
            try:
                result = self._engine.step()
            except _GateSuspend:
                return                       # gate opened; gate_opened + session_state already emitted
            if result is None:
                break
            self._emit_step_events(result, include_phase_started=not resolving_gate)
            if resolving_gate:
                self._pending = None
                self._decision = None
            self._persist()                  # durable after every fold/step
            # PHASE-REVIEW (owner 2026-07-23): a phase completed its goal and would advance to a
            # DIFFERENT phase — pause for the human's DIRECTION approval BEFORE the next phase runs.
            # A between-steps pause (the completed phase already claimed + journaled its seq), so it
            # is the SESSION driver's construct, never an engine gate — the batch Engine.run() path
            # (gen_golden/run_live) never reaches here. Skipped in auto_review mode (scripted/CI).
            if not self._auto_review and self._should_review(result):
                self._open_review(result)
                return                       # phase_review_opened + session_state already emitted
        if self._engine.current_phase is None:
            self._close()
        elif self._engine.done():
            self._exhaust()                  # max-steps zombie fix (P6 step 5)

    # ── write-gate machinery ────────────────────────────────────────────────────
    def _write_calls(self, ctx: PlanContext, out: PlanOutput) -> list[CapabilityCall]:
        if not ctx.phase_spec.writes_allowed:
            return []
        return [c for c in out.calls if self._is_write_call(c)]

    def _is_write_call(self, call: CapabilityCall) -> bool:
        # PER-INTENT effect (P6 convergence wiring, part4-capability §1): the gate keys on
        # `layer.effect_for(adapter, intent)` — registry-declared, then the adapter's
        # per-intent `effects` override, then its default — never on the adapter-WIDE effect
        # alone, so a mixed adapter (ocp reads + ocp__restart) gates exactly its write intents.
        layer = self._engine.layer
        if layer is None:
            return False
        a = layer.resolve(call.intent)
        return a is not None and layer.effect_for(a, call.intent) == Effect.WRITE

    def _open_gate(self, ctx: PlanContext, out: PlanOutput, write_calls: list[CapabilityCall]) -> None:
        self._gate_count += 1
        gate_id = f"{self.id}:gate:{self._gate_count}"
        self._pending = _Pending(phase=ctx.phase, plan=out, write_calls=write_calls, gate_id=gate_id)
        # PHASE-REVIEW dedup (owner 2026-07-23): this phase opened the human write-gate, so its
        # own advance-review is SUBSUMED — one pause at Act, never the write-gate THEN a redundant
        # "approve advancing past act" review.
        self._gated_phases.add(ctx.phase)
        self.state = SessionState.SUSPENDED
        self._emit("phase_started", phase=ctx.phase)
        payload = self._gate_payload(ctx, write_calls, gate_id, out.narrative)
        # JOURNAL v2 (part2 §1): the gate OPENING is durable — what was proposed, on whose
        # behalf, on what evidence. Was an in-memory event only ("the journal proves consent"
        # started at the answer; now it starts at the question).
        self._engine.journal.append_gate_opened(
            ctx.phase, gate_id=gate_id, actions=payload["actions"],
            reasoning=out.narrative,
            hypothesis=payload["hypothesis"]["id"] if payload["hypothesis"] else None,
            evidence=[e.get("id") for e in payload["evidence"]])
        self._emit("gate_opened", **payload)
        self._emit("session_state", state=self.state.value, phase=ctx.phase)
        self._persist()                      # a suspended run is durable at the open gate

    def _gate_payload(self, ctx: PlanContext, write_calls: list[CapabilityCall],
                      gate_id: str, narrative: str) -> dict:
        layer = self._engine.layer
        actions = []
        for c in write_calls:
            a = layer.resolve(c.intent) if layer else None
            actions.append({
                "intent": c.intent, "params": dict(c.params),
                "provider": a.provider if a else "?", "effect": Effect.WRITE.value,
                "summary": f"{a.provider if a else '?'}.{c.intent}({c.params})",
            })
        # the serving hypothesis + its supporting facts (the evidence chain), read off the hypothesis store
        lead = self._engine.hypothesis_store.leading()
        hypothesis = None
        evidence: list[dict] = []
        if lead is not None:
            hypothesis = {"id": lead.id, "statement": lead.statement,
                          "status": lead.status.value,
                          "confidence": self._engine.hypothesis_store.score(lead),
                          "root_candidate": lead.root_candidate}
            evidence = [self._fact_view(fid) for fid in lead.supporting_facts]
        return {"gate_id": gate_id, "phase": ctx.phase, "reasoning": narrative,
                "actions": actions, "hypothesis": hypothesis, "evidence": evidence}

    # ── phase-review machinery (between-steps DIRECTION approval — owner 2026-07-23) ─────
    def _should_review(self, result) -> bool:
        """A phase-review fires iff: the completed phase's verdict is a genuine ADVANCE to a
        DIFFERENT, non-terminal phase (never a REPEAT loop or the DONE terminal), the playbook
        DECLARES `review_before_advance` for that phase, and the phase did NOT open the write-gate
        this run (the write-gate subsumes its review — one pause, not two)."""
        if result.verdict.status != VerdictStatus.ADVANCE:
            return False
        nxt = self._engine.current_phase          # the phase the NEXT step() would run
        if nxt is None or nxt == result.phase_id:  # terminal, or a same-phase repeat — no review
            return False
        if result.phase_id in self._gated_phases:  # the write-gate already paused this phase
            return False
        spec = self._engine.playbook.phase(result.phase_id)
        return spec.review_before_advance

    def _open_review(self, result) -> None:
        self._review_count += 1
        review_id = f"{self.id}:review:{self._review_count}"
        payload = self._review_payload(result, review_id)
        self._pending_review = _PendingReview(
            from_phase=result.phase_id, to_phase=self._engine.current_phase,
            review_id=review_id, payload=payload)
        self.state = SessionState.AWAITING_REVIEW
        # durable, gate_opened-style: WHAT the phase did + the proposed direction, on what evidence.
        self._engine.journal.append_phase_review(
            result.phase_id, review_id=review_id, to_phase=payload["to_phase"],
            summary=payload["summary"], verdict=payload["verdict"],
            hypothesis=payload["hypothesis"]["id"] if payload["hypothesis"] else None,
            facts=payload["facts"], nodes=payload["nodes"])
        self._emit("phase_review_opened", **payload)
        self._emit("session_state", state=self.state.value, phase=result.phase_id)
        self._persist()                      # a run awaiting review is durable at the pause

    def _review_payload(self, result, review_id: str) -> dict:
        """Assemble the SUMMARY the human reviews: the phase's goal + reasoning narrative, the
        counts of what it discovered, the leading hypothesis, and the proposed advance + why."""
        to_phase = self._engine.current_phase
        lead = self._engine.hypothesis_store.leading()
        hypothesis = None
        if lead is not None:
            hypothesis = {"id": lead.id, "statement": lead.statement,
                          "status": lead.status.value,
                          "confidence": self._engine.hypothesis_store.score(lead),
                          "root_candidate": lead.root_candidate}
        gate = result.verdict.gate_result.value
        summary = (f"'{result.phase_id}' is complete ({gate} gate) — proposing to advance to "
                   f"'{to_phase}'.")
        return {
            "review_id": review_id, "phase": result.phase_id, "to_phase": to_phase,
            "goal": result.goal_restated, "narrative": result.narrative,
            "verdict": result.verdict.status.value, "summary": summary,
            "discovered": {"facts": len(result.facts_added), "nodes": len(result.nodes_touched),
                           "events": len(result.events_added), "edges": len(result.edges_added),
                           "hypotheses": len(result.hypotheses_updated)},
            "hypothesis": hypothesis,
            "facts": [f.id for f in result.facts_added],
            "nodes": [n.id for n in result.nodes_touched],
        }

    def _resolve_pending_plan(self, ctx: PlanContext) -> PlanOutput:
        """Return the plan the operator's decision implies for the gated phase (called on the
        resume step, in place of the inner planner)."""
        p = self._pending
        d = self._decision
        assert p is not None and d is not None
        if d.decision == GateDecision.APPROVE:
            return p.plan                                    # write intact → served under the gate
        if d.decision == GateDecision.REFINE:
            calls = [c.model_copy(update={"params": {**c.params, **d.params}})
                     if self._is_write_call(c) else c for c in p.plan.calls]
            return p.plan.model_copy(update={
                "calls": calls, "narrative": p.plan.narrative + " [params refined by operator]"})
        # DENY — drop the write; record the denial as a synthetic hypothesis store result fed back to the
        # next plan (a divergent journal), keeping any non-write calls.
        non_write = [c for c in p.plan.calls if not self._is_write_call(c)]
        lead = self._engine.hypothesis_store.leading()
        deny_ops = list(p.plan.ops)
        if lead is not None:
            local_hid = lead.id.split("hyp:", 1)[-1]
            deny_ops.append(UpdateHypothesis(
                hid=local_hid, basis=f"operator DENIED the proposed remediation: {d.reason or 'no reason given'}"))
        return p.plan.model_copy(update={
            "calls": non_write, "ops": deny_ops,
            "narrative": p.plan.narrative + f" [DENIED by operator — {d.reason or 'no reason given'}; replanning]"})

    # ── event emission (all derived from the folded PhaseResult) ────────────────
    def _emit_step_events(self, result, *, include_phase_started: bool) -> None:
        if include_phase_started:
            self._emit("phase_started", phase=result.phase_id)
        self._emit("reasoning", phase=result.phase_id, narrative=result.narrative)
        for inv in self._engine.invocations[self._inv_cursor:]:
            # `outcome` is the load-bearing honesty field (P3 step 1 / part4-capability §4):
            # data · empty (clean-empty) · error · blocked — the UI must never infer "clean"
            # from op_count == 0 alone (that conflation is the silent-empty poison).
            self._emit("capability_call", intent=inv.intent, provider=inv.provider,
                       effect=inv.effect.value, op_count=inv.op_count,
                       outcome=inv.outcome,
                       blocked=inv.blocked, reason=inv.reason,
                       kind=inv.kind, started_at=inv.started_at, duration_ms=inv.duration_ms,
                       params=inv.params, summary=inv.summary,
                       # transport provenance (M1): mock-vs-live + the declared Binding, on the stream
                       served_by=inv.served_by,
                       binding=inv.binding.value if inv.binding else None)
        self._inv_cursor = len(self._engine.invocations)
        self._emit("graph_delta",
                   nodes=[{"id": n.id, "type": n.type.value, "created_by": n.created_by,
                           "origin": n.id == self._origin_id} for n in result.nodes_touched],
                   edges=[{"id": e.id, "type": e.type.value, "src": e.src, "dst": e.dst,
                           "origin": e.origin.value,
                           "source": e.source.value if e.source else None,
                           "established": e.valid_from.isoformat() if e.valid_from else None,
                           **({"provisional": True} if e.provisional else {})}
                          for e in result.edges_added],
                   facts=[{"id": f.id, "subject": f.subject_ref, "predicate": f.predicate,
                           "value": f.value, "unit": f.unit, "where": f.where,
                           "source": f.source.value,
                           "observed_at": f.observed_at.isoformat(),
                           "at": f.valid_from.isoformat(),
                           **({"provisional": True} if f.provisional else {})}
                          for f in result.facts_added],
                   events=[{"id": e.id, "entity": e.entity_ref, "type": e.type,
                            **({"provisional": True} if e.provisional else {})}
                           for e in result.events_added])
        self._emit("hypotheses_delta",
                   hypotheses=[self._hyp_delta_view(dlt) for dlt in result.hypotheses_updated])
        phase = self._engine.current_phase
        self._emit("session_state", state=self.state.value,
                   phase=phase,
                   verdict=result.verdict.status.value)

    def _hyp_delta_view(self, delta) -> dict:
        hid = delta.hypothesis.id if delta.hypothesis else delta.hypothesis_id
        h = self._engine.hypothesis_store.hypotheses.get(hid)
        # carry the full hypothesis on the delta (statement + root + evidence ids), NOT just the id
        # — so the UI shows the real theory the moment it's proposed, never a bare "hyp:h1" waiting
        # on a snapshot backfill.
        return {"id": hid, "action": delta.action.value,
                "status": h.status.value if h else None,
                "confidence": self._engine.hypothesis_store.score(h) if h else None,
                "basis": delta.basis or (h.confidence.basis if h else ""),
                "statement": h.statement if h else "",
                "root_candidate": h.root_candidate if h else None,
                "supporting": list(h.supporting_facts) if h else [],
                "refuting": list(h.refuting_facts) if h else []}

    def _fact_view(self, fid: str) -> dict:
        f = self._engine.graph.facts.get(fid)
        if f is None:
            return {"id": fid, "resolved": False}
        return {"id": f.id, "subject": f.subject_ref, "predicate": f.predicate,
                "value": f.value, "unit": f.unit, "source": f.source.value}

    def _close(self) -> None:
        # The engine's phase route completed — a genuine DONE terminal, or an unrouted verdict
        # that drained the route (both leave current_phase None). ONE `closed` lifecycle record,
        # cause=finished (M17); the terminal outcome is whatever the engine resolved. (Routing
        # BLOCKED/DONE somewhere better is P7's phase work; the close half lives here.)
        self._finalize(CloseCause.FINISHED)

    def _exhaust(self) -> None:
        """ZOMBIE FIX (P6 step 5, part2 §3): max-steps exhaustion used to leave the session
        RUNNING forever — no close, no journal trace, the SSE stream dying only by idle timeout,
        the zombie undiagnosable. Now it funnels through the ONE terminal path: a durable `closed`
        lifecycle record names the cause (exhausted, M17), the session CLOSES (ending the SSE
        loop), and the run stays reopenable read-only from disk like any other closed run."""
        self._finalize(CloseCause.EXHAUSTED, phase_id=self._engine.current_phase)

    def _finalize(self, cause: CloseCause, *, phase_id: str | None = None,
                  outcome: str | None = None, detail: dict | None = None,
                  best_effort: bool = False) -> None:
        """The ONE terminal transition (M17). Finished, exhausted, errored and denied all end
        HERE: state→CLOSED, the outcome resolved once, a SINGLE `closed` lifecycle record carrying
        the CAUSE, the terminal `session_state` on the stream, and the final durable write — so
        'did it terminate, and why' is a single journal read (kind=lifecycle, event=closed, then
        `cause`), where before four paths emitted three event names and two skipped a `closed`
        record. `outcome` OVERRIDES the engine's terminal label: an errored run whose engine never
        reached a terminal carries 'error' (M18), so list_view + persisted meta never report a
        crash as 'open'; the healthy paths pass None and take `close_outcome or 'open'` exactly as
        before (meta byte-identical). `best_effort` swallows a durable-write failure on an
        already-dead run (the error path) while still marking CLOSED and streaming the close."""
        self.state = SessionState.CLOSED
        self._outcome = (outcome if outcome is not None
                         else (self._engine.result().close_outcome or "open"))
        try:
            self._engine.journal.append_lifecycle(
                "closed", phase_id=phase_id, outcome=self._outcome,
                detail={"cause": cause.value, **(detail or {})})
            self._emit("session_state", state=self.state.value, phase=phase_id,
                       outcome=self._outcome, cause=cause.value)
            self._persist()
        except Exception:
            if not best_effort:
                raise

    def _emit(self, etype: str, **payload) -> dict:
        self._event_seq += 1
        ev = {"seq": self._event_seq, "type": etype, "ts": self._now(), **payload}
        self._events.append(ev)
        return ev

    def _now(self) -> str:
        return self._clock().isoformat()

    # ── durability ───────────────────────────────────────────────────────────────
    def _persist(self) -> None:
        """Append the journal + rewrite the graph/meta on disk (no-op without a store). Called
        after each fold/step, when a gate opens, on close, and on an operator message — the
        journal is append-only so this is cheap and crash-safe. Threads the session `_outcome`
        into meta so a crashed run persists 'error', never 'open' (M18) — during a live run it is
        'open' and equals the engine's derived label, so healthy metas stay byte-identical."""
        if self._store is None:
            return
        self._persisted_journal = self._store.persist(
            self.subject, self._engine, prior=self._persisted_journal, state=self.state.value,
            outcome=self._outcome)


class SessionManager:
    """A registry of investigations so many incidents can be listed + reopened (incl. CLOSED).
    `planner_factory(subject) -> Planner` supplies the per-incident planner (a ScriptedPlanner
    in tests/demos, a live LLM planner in production); `layer_factory(subject)` optionally
    wires the capability layer (its Source is the fixture/live transport)."""

    def __init__(self, playbook: Playbook, planner_factory: Callable[[SubjectRef], Planner], *,
                 layer_factory: Callable[[SubjectRef], CapabilityLayer | None] | None = None,
                 clock: Callable[[], datetime] | None = None, max_steps: int = 60,
                 background_drive: bool = False, auto_review: bool = True,
                 store: InvestigationStore | None = None) -> None:
        self.playbook = playbook
        self._planner_factory = planner_factory
        self._layer_factory = layer_factory
        self._clock = clock
        self._max_steps = max_steps
        self._background_drive = background_drive     # live path: drive off the HTTP thread
        # PHASE-REVIEW mode for every session this manager creates (owner 2026-07-23): auto_review=
        # True (default) is the scripted/CI backend (build_manager) — reviews auto-approve; the LIVE
        # workbench backend (live_build_manager) sets False so the real human approves each advance.
        self._auto_review = auto_review
        # durability (opt-in): when set, sessions persist as they drive and reopen read-only from
        # disk after a restart. None keeps the pure in-memory behaviour (the hermetic suite).
        self._store = store
        self._sessions: dict[str, InvestigationSession] = {}

    @property
    def store(self) -> InvestigationStore | None:
        return self._store

    def create(self, subject: SubjectRef, *, advance: bool = True) -> InvestigationSession:
        layer = self._layer_factory(subject) if self._layer_factory else None
        session = InvestigationSession(subject, self.playbook, self._planner_factory(subject),
                                       layer=layer, clock=self._clock, max_steps=self._max_steps,
                                       background_drive=self._background_drive,
                                       auto_review=self._auto_review, store=self._store)
        self._sessions[session.id] = session       # register (overwrites a prior run of the same id)
        if advance:
            session.advance()                      # run to the first pause / gate
        return session

    def get(self, session_id: str) -> InvestigationSession | None:
        return self._sessions.get(session_id)

    def reopen(self, session_id: str) -> dict | None:
        """A read-only reopen after a restart: when `get()` misses in memory, lazy-load the
        investigation from disk (journal replay via `rebuild`) and return its export_bundle-shaped
        snapshot. Returns None when the id is neither in memory nor on disk. Continue-driving a
        reopened session is out of scope — this serves the durable read only."""
        live = self._sessions.get(session_id)
        if live is not None:
            return live.snapshot()
        if self._store is not None:
            # pass the manager's playbook so a disk-reopened run gets the full declared phase rail (M22)
            return self._store.load_bundle(session_id, playbook=self.playbook)
        return None

    def list(self) -> list[dict]:
        """Every investigation, in-memory first then disk-only ones merged in (a live session
        shadows its own on-disk copy, so no duplicates)."""
        rows = [s.list_view() for s in self._sessions.values()]
        if self._store is not None:
            seen = {r["id"] for r in rows}
            rows += [r for r in self._store.list_disk() if r["id"] not in seen]
        return rows
