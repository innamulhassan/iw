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
   denial as a synthetic ledger result fed back so the next plan replans — a divergent journal.

2. **An ordered event stream** derived purely from what the engine already recorded (the
   journal / graph / ledger) — `phase_started`, `reasoning`, `capability_call`, `graph_delta`
   (each node WITH its `created_by` seq), `ledger_delta`, `gate_opened`, `session_state`.
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

from ..api.bundle import export_bundle
from ..capability.layer import CapabilityCall, CapabilityLayer
from ..domain.enums import Effect, NodeType, Source
from ..domain.operations import UpdateHypothesis
from ..domain.playbook import Playbook
from ..domain.registry import node_id
from ..domain.subject import SubjectRef
from .engine import Engine
from .planner import PlanContext, Planner, PlanOutput


class SessionState(StrEnum):
    RUNNING = "running"
    SUSPENDED = "suspended"   # paused at an open write-gate awaiting a human decision
    CLOSED = "closed"


class GateDecision(StrEnum):
    APPROVE = "approve"       # apply the proposed write + continue
    REFINE = "refine"         # edit the write params, then apply + continue
    DENY = "deny"             # drop the write; record the denial as feedback → replan


class _GateSuspend(Exception):
    """Internal control-flow signal: raised by the wrapped planner (after the plan is computed,
    before the write is served) so the engine step unwinds cleanly with nothing applied. Only
    the wrapped planner used by a session ever raises it, so the batch run() path is untouched."""


@dataclass
class _Pending:
    phase: object                 # Phase the gate is open on
    plan: PlanOutput              # the peeked plan (cached so re-entry never re-consumes the planner)
    write_calls: list[CapabilityCall]
    gate_id: str


@dataclass
class _Decision:
    decision: GateDecision
    params: dict = field(default_factory=dict)
    reason: str = ""


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
                 background_drive: bool = False) -> None:
        self.subject = subject
        self.id = subject.key
        # the ServiceNow incident under investigation → renders as node #1 (obs 1)
        self._origin_id = node_id(NodeType.INCIDENT, {"incident_id": subject.id})
        self._clock = clock or (lambda: datetime.now(UTC))
        self._engine = Engine(playbook, _GatePlanner(planner, self), clock=clock, layer=layer)
        # hand a LIVE planner the direct graph ref (full view, parity with run_live);
        # a ScriptedPlanner has no `graph` attribute, so this is a no-op for the mock path.
        if hasattr(planner, "graph"):
            planner.graph = self._engine.graph
        self._engine.start(subject, max_steps=max_steps)
        self.state = SessionState.RUNNING
        self._events: list[dict] = []
        self._event_seq = 0
        self._inv_cursor = 0                 # how many engine invocations have been streamed
        self._pending: _Pending | None = None
        self._decision: _Decision | None = None
        self._gate_count = 0
        self._messages: list[dict] = []      # operator steering / answers
        self._outcome: str = "open"
        # background drive: a live LLM phase is seconds of latency — run the drive loop off the
        # HTTP thread so endpoints return immediately and the SSE stream shows incremental progress.
        self._background = background_drive
        self._drive_lock = threading.Lock()
        self._driving = False

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
        self.state = SessionState.RUNNING
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
            self._emit("session_error", message=f"{type(exc).__name__}: {exc}")
            self.state = SessionState.CLOSED
            self._emit("session_state", state=self.state.value, phase=None, outcome="error")
        finally:
            with self._drive_lock:
                self._driving = False

    def _record_gate_decision(self, decision: GateDecision, *, actor: str, reason: str,
                              params: dict) -> None:
        """Journal the human gate answer (source-of-truth) + emit a `gate_decision` event."""
        p = self._pending
        assert p is not None
        write = p.write_calls[0] if p.write_calls else None
        action = {"gate_id": p.gate_id,
                  "intent": write.intent if write else None,
                  "params": {**(dict(write.params) if write else {}), **params}}
        seq = self._engine.journal.reserve_seq()
        self._engine.journal.append_step(
            seq, p.phase, intent=write.intent if write else "gate",
            reasoning=reason or f"gate {decision.value} by {actor}",
            action=action, observation={"decision": decision.value, "actor": actor},
            decision=decision.value, actor=actor, source=Source.HUMAN)
        self._emit("gate_decision", gate_id=p.gate_id, decision=decision.value,
                   actor=actor, source=Source.HUMAN.value, reason=reason,
                   phase=p.phase.value)

    def add_message(self, text: str, *, actor: str = "operator") -> dict:
        """Record an operator turn in the two-way chat (obs 2). The message becomes a first-class
        `user_message` event on the stream AND a `step` entry in the durable journal (Source.HUMAN),
        and is buffered so the LIVE planner sees it as steering on its next plan (via _GatePlanner).
        It does not itself mutate the graph/ledger fold — the planner decides what to do with it."""
        kind = "answer" if self.state == SessionState.SUSPENDED else "steer"
        msg = {"seq": len(self._messages) + 1, "text": text, "at": self._now(),
               "kind": kind, "actor": actor}
        self._messages.append(msg)
        phase = self._engine.current_phase
        jseq = self._engine.journal.reserve_seq()
        self._engine.journal.append_step(
            jseq, phase, intent="operator_message", reasoning=text,
            action={"kind": kind}, observation={"actor": actor},
            decision=None, actor=actor, source=Source.HUMAN)
        self._emit("user_message", text=text, kind=kind, actor=actor,
                   source=Source.HUMAN.value, phase=phase.value if phase else None)
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

    def snapshot(self) -> dict:
        """export_bundle-shaped cold-load payload (+ session envelope). The engine's journal is
        the checkpointer, so `graph`/`ledger` here equal a fresh journal replay."""
        res = self._engine.result()
        bundle = export_bundle(res)
        return {
            **bundle,
            "session_id": self.id,
            "state": self.state.value,
            "pending_gate": self.pending_gate,
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
                src.phase = cur.value
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
        if self._engine.current_phase is None:
            self._close()

    # ── write-gate machinery ────────────────────────────────────────────────────
    def _write_calls(self, ctx: PlanContext, out: PlanOutput) -> list[CapabilityCall]:
        if not ctx.phase_spec.writes_allowed:
            return []
        return [c for c in out.calls if self._is_write_call(c)]

    def _is_write_call(self, call: CapabilityCall) -> bool:
        layer = self._engine.layer
        if layer is None:
            return False
        a = layer.resolve(call.intent)
        return a is not None and a.effect == Effect.WRITE

    def _open_gate(self, ctx: PlanContext, out: PlanOutput, write_calls: list[CapabilityCall]) -> None:
        self._gate_count += 1
        gate_id = f"{self.id}:gate:{self._gate_count}"
        self._pending = _Pending(phase=ctx.phase, plan=out, write_calls=write_calls, gate_id=gate_id)
        self.state = SessionState.SUSPENDED
        self._emit("phase_started", phase=ctx.phase.value)
        self._emit("gate_opened", **self._gate_payload(ctx, write_calls, gate_id, out.narrative))
        self._emit("session_state", state=self.state.value, phase=ctx.phase.value)

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
        # the serving hypothesis + its supporting facts (the evidence chain), read off the ledger
        lead = self._engine.ledger.leading()
        hypothesis = None
        evidence: list[dict] = []
        if lead is not None:
            hypothesis = {"id": lead.id, "statement": lead.statement,
                          "status": lead.status.value, "confidence": lead.confidence.value,
                          "root_candidate": lead.root_candidate}
            evidence = [self._fact_view(fid) for fid in lead.supporting_facts]
        return {"gate_id": gate_id, "phase": ctx.phase.value, "reasoning": narrative,
                "actions": actions, "hypothesis": hypothesis, "evidence": evidence}

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
        # DENY — drop the write; record the denial as a synthetic ledger result fed back to the
        # next plan (a divergent journal), keeping any non-write calls.
        non_write = [c for c in p.plan.calls if not self._is_write_call(c)]
        lead = self._engine.ledger.leading()
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
            self._emit("phase_started", phase=result.phase_id.value)
        self._emit("reasoning", phase=result.phase_id.value, narrative=result.narrative)
        for inv in self._engine.invocations[self._inv_cursor:]:
            self._emit("capability_call", intent=inv.intent, provider=inv.provider,
                       effect=inv.effect.value, op_count=inv.op_count,
                       blocked=inv.blocked, reason=inv.reason,
                       kind=inv.kind, started_at=inv.started_at, duration_ms=inv.duration_ms,
                       params=inv.params, summary=inv.summary)
        self._inv_cursor = len(self._engine.invocations)
        self._emit("graph_delta",
                   nodes=[{"id": n.id, "type": n.type.value, "created_by": n.created_by,
                           "origin": n.id == self._origin_id} for n in result.nodes_touched],
                   edges=[{"id": e.id, "type": e.type.value, "src": e.src, "dst": e.dst,
                           "origin": e.origin.value,
                           "source": e.source.value if e.source else None,
                           "established": e.valid_from.isoformat() if e.valid_from else None}
                          for e in result.edges_added],
                   facts=[{"id": f.id, "subject": f.subject_ref, "predicate": f.predicate,
                           "value": f.value, "unit": f.unit, "where": f.where,
                           "source": f.source.value,
                           "observed_at": f.observed_at.isoformat(),
                           "at": f.valid_from.isoformat()} for f in result.facts_added],
                   events=[{"id": e.id, "entity": e.entity_ref, "type": e.type}
                           for e in result.events_added])
        self._emit("ledger_delta",
                   hypotheses=[self._hyp_delta_view(dlt) for dlt in result.hypotheses_updated])
        phase = self._engine.current_phase
        self._emit("session_state", state=self.state.value,
                   phase=phase.value if phase is not None else None,
                   verdict=result.verdict.status.value)

    def _hyp_delta_view(self, delta) -> dict:
        hid = delta.hypothesis.id if delta.hypothesis else delta.hypothesis_id
        h = self._engine.ledger.hypotheses.get(hid)
        # carry the full hypothesis on the delta (statement + root + evidence ids), NOT just the id
        # — so the UI shows the real theory the moment it's proposed, never a bare "hyp:h1" waiting
        # on a snapshot backfill.
        return {"id": hid, "action": delta.action.value,
                "status": h.status.value if h else None,
                "confidence": h.confidence.value if h else None,
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
        self.state = SessionState.CLOSED
        res = self._engine.result()
        self._outcome = res.close_outcome.value if res.close_outcome else "open"
        self._emit("session_state", state=self.state.value, phase=None, outcome=self._outcome)

    def _emit(self, etype: str, **payload) -> dict:
        self._event_seq += 1
        ev = {"seq": self._event_seq, "type": etype, "ts": self._now(), **payload}
        self._events.append(ev)
        return ev

    def _now(self) -> str:
        return self._clock().isoformat()


class SessionManager:
    """A registry of investigations so many incidents can be listed + reopened (incl. CLOSED).
    `planner_factory(subject) -> Planner` supplies the per-incident planner (a ScriptedPlanner
    in tests/demos, a live LLM planner in production); `layer_factory(subject)` optionally
    wires the capability layer (its Source is the fixture/live transport)."""

    def __init__(self, playbook: Playbook, planner_factory: Callable[[SubjectRef], Planner], *,
                 layer_factory: Callable[[SubjectRef], CapabilityLayer | None] | None = None,
                 clock: Callable[[], datetime] | None = None, max_steps: int = 60,
                 background_drive: bool = False) -> None:
        self.playbook = playbook
        self._planner_factory = planner_factory
        self._layer_factory = layer_factory
        self._clock = clock
        self._max_steps = max_steps
        self._background_drive = background_drive     # live path: drive off the HTTP thread
        self._sessions: dict[str, InvestigationSession] = {}

    def create(self, subject: SubjectRef, *, advance: bool = True) -> InvestigationSession:
        layer = self._layer_factory(subject) if self._layer_factory else None
        session = InvestigationSession(subject, self.playbook, self._planner_factory(subject),
                                       layer=layer, clock=self._clock, max_steps=self._max_steps,
                                       background_drive=self._background_drive)
        self._sessions[session.id] = session       # register (overwrites a prior run of the same id)
        if advance:
            session.advance()                      # run to the first pause / gate
        return session

    def get(self, session_id: str) -> InvestigationSession | None:
        return self._sessions.get(session_id)

    def list(self) -> list[dict]:
        return [s.list_view() for s in self._sessions.values()]
