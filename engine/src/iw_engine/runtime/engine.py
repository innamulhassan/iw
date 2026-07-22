"""Engine — the thin deterministic phase orchestrator (DESIGN §0/§2.3). One phase, one
loop iteration:  plan (typed ops) → reduce (validate+materialise) → apply to graph+store
→ gate the verdict → journal the PhaseResult → route to the next phase. No LangGraph, no
sub-agent sprawl — the uniform contract makes the whole controller a few lines. Fully
deterministic given a ScriptedPlanner + mock capabilities.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..capability.layer import CapabilityLayer, Invocation
from ..domain.enums import CloseOutcome, Phase, VerdictStatus
from ..domain.hypothesis import Hypothesis
from ..domain.phase_result import PhaseResult
from ..domain.playbook import Playbook
from ..domain.subject import SubjectRef
from ..graph.fold import apply_delta
from ..graph.graph import Graph
from ..graph.reducer import Rejection, materialize
from ..graph.render import render_slice
from ..hypothesis.store import HypothesisStore
from ..journal.journal import Journal, JournalEntry
from .controller import check_gate, next_phase
from .planner import PlanContext, Planner


@dataclass
class RunResult:
    subject: SubjectRef
    phases_run: list[Phase]
    graph: Graph
    hypothesis_store: HypothesisStore
    journal: Journal
    confirmed: Hypothesis | None
    close_outcome: CloseOutcome | None
    rejections: list[Rejection] = field(default_factory=list)
    invocations: list[Invocation] = field(default_factory=list)   # capability audit trail


class Engine:
    def __init__(self, playbook: Playbook, planner: Planner, *, clock=None,
                 layer: CapabilityLayer | None = None) -> None:
        self.playbook = playbook
        self.planner = planner
        self._clock = clock or (lambda: datetime.now(UTC))   # wall-clock for trace spans
        self.layer = layer   # owns the fetch transport (Source) since the §C re-seam
        self.graph = Graph()
        self.hypothesis_store = HypothesisStore()
        self.journal = Journal(clock=clock)
        self.subject: SubjectRef | None = None
        self._anomaly_ref: str | None = None
        self._gate_feedback: str | None = None   # last failed-gate reason, fed to the next plan (GAP 3)
        self.rejections: list[Rejection] = []
        self.invocations: list[Invocation] = []
        # P3 airlock step 1 — the engine CONSUMES the boundary outcome (part4-capability §4):
        # the LAST outcome per intent. An intent whose last call errored/was blocked carries no
        # evidentiary weight and may not feed the NoEvidence/refutation path; a later successful
        # call of the same intent clears the bar.
        self._intent_outcomes: dict[str, str] = {}
        # resumable run-state (A3) — the engine is a stepper; run() is a driver over step()
        self._phase: Phase | None = None
        self._phases_run: list[Phase] = []
        self._steps = 0
        self._max_steps = 60

    def start(self, subject: SubjectRef, *, max_steps: int = 60) -> None:
        """Begin an investigation; leaves the engine at the entry phase, nothing run yet."""
        self.subject = subject
        self._phase = self.playbook.entry_phase
        self._phases_run = []
        self._steps = 0
        self._max_steps = max_steps
        self._gate_feedback = None
        self._intent_outcomes = {}

    def done(self) -> bool:
        return self._phase is None or self._steps >= self._max_steps

    @property
    def current_phase(self) -> Phase | None:
        """The phase the next `step()` will run (None once the run is complete). Lets a driver
        (the live runner, the interactive session backend) observe/scope per-phase state — e.g.
        a phase-scoped fixture transport — between steps without reaching into internals."""
        return self._phase

    def step(self) -> PhaseResult | None:
        """Run exactly one phase and route to the next. Returns the PhaseResult, or None when
        the investigation is complete. The interactive driver calls this to pause between
        phases (and, in interactive mode, before a gated write)."""
        if self.done():
            return None
        self._steps += 1
        spec = self.playbook.phase(self._phase)
        result = self._run_phase(self._phase, spec)
        self._phases_run.append(self._phase)
        self._phase = next_phase(spec, result.verdict)
        return result

    def run(self, subject: SubjectRef, *, max_steps: int = 60) -> RunResult:
        self.start(subject, max_steps=max_steps)
        while self.step() is not None:
            pass
        return self.result()

    def result(self) -> RunResult:
        confirmed = self.hypothesis_store.confirmed()
        outcome = self._close_outcome(self._phases_run, confirmed)
        return RunResult(subject=self.subject, phases_run=self._phases_run, graph=self.graph,
                         hypothesis_store=self.hypothesis_store, journal=self.journal,
                         confirmed=confirmed, close_outcome=outcome, rejections=self.rejections,
                         invocations=self.invocations)

    # ── one phase ─────────────────────────────────────────────────────────────
    def _run_phase(self, phase: Phase, spec) -> PhaseResult:
        seq = self.journal.reserve_seq()
        ctx = PlanContext(
            subject=self.subject, phase=phase, phase_spec=spec, goal=spec.goal,
            graph_view=render_slice(self.graph, self._anomaly_ref),
            hypotheses=[{"id": h.id, "statement": h.statement, "status": h.status.value,
                         "confidence": h.confidence.value} for h in self.hypothesis_store.ranked()],
            tunables=self.playbook.tunables, gate_feedback=self._gate_feedback)
        plan = self.planner.plan(ctx)

        # capability calls -> data ops (the tool outputs fold into the graph); writes
        # (remediation actions) execute only in the human-gated REMEDIATE phase. serve() is
        # gate-first: a disallowed write is blocked BEFORE any fetch/side-effect (§C.3/§D).
        data_ops: list = []
        if self.layer is not None:
            allow_write = spec.writes_allowed          # domain role-binding, not a hardcoded phase
            for call in plan.calls:
                started = self._clock().isoformat()
                t0 = time.perf_counter()
                ops_i, inv = self.layer.serve(call, allow_write=allow_write)
                dur_ms = round((time.perf_counter() - t0) * 1000.0, 2)
                # stamp the trace span (obs 9); a write served under a gate is a "workflow" step,
                # a plain read is a "tool" call. Timing is wall-clock, ephemeral (not journaled).
                inv = inv.model_copy(update={
                    "started_at": started, "duration_ms": dur_ms,
                    "kind": "workflow" if inv.effect.value == "write" else "tool"})
                data_ops.extend(ops_i)
                self.invocations.append(inv)
                # P3 airlock step 1 — consume the boundary outcome: remember the last outcome per
                # intent and journal the non-data outcomes DISTINCTLY (error ≠ clean-empty), so a
                # failed read is never silently erased NOR silently read as refuting evidence.
                self._intent_outcomes[inv.intent] = inv.outcome
                if inv.outcome in ("error", "empty"):
                    self._journal_invocation(seq, phase, inv)
        combined = data_ops + list(plan.ops)

        ceiling = self.playbook.tunables.op_ceiling.get(phase.value)
        ops = combined[:ceiling] if ceiling else combined

        # intents whose LAST call errored/was blocked observed NOTHING — a NoEvidence op naming
        # one is rejected by the reducer (fabricated-negative-evidence killer, part4 §4).
        no_weight = frozenset(i for i, o in self._intent_outcomes.items()
                              if o in ("error", "blocked"))
        mat = materialize(ops, seq, self.graph, self.playbook.tunables,
                          anomaly_ref=self._anomaly_ref, no_weight_intents=no_weight)
        self.rejections.extend(mat.rejections)

        # capture the symptom node the first time it is created (domain role-binding)
        if self._anomaly_ref is None:
            for n in mat.nodes:
                if n.type == self.playbook.symptom_node:
                    self._anomaly_ref = n.id
                    break

        result = PhaseResult(
            phase_id=phase, goal_restated=spec.goal, facts_added=mat.facts,
            events_added=mat.events, nodes_touched=mat.nodes, edges_added=mat.edges,
            hypotheses_updated=mat.hyp_deltas, narrative=plan.narrative,
            next_actions=plan.next_actions, verdict=plan.verdict)

        # apply the delta via the single mutation seam FIRST, then gate against the updated store
        apply_delta(result, seq, self.graph, self.hypothesis_store)

        gated = check_gate(spec, result, self.hypothesis_store, self.playbook.tunables)
        # REPEAT CAP (tunables.max_retries): a live planner can vote REPEAT indefinitely (the
        # database run looped in TRIAGE for all 16 steps). After max_retries prior consecutive
        # runs of this phase, force an ADVANCE so the investigation always progresses — the
        # engine's guardrail, independent of the model's judgment. Deterministic scripts never
        # hit it (they emit ADVANCE when ready), so goldens are untouched.
        prior_consecutive = 0
        for p in reversed(self._phases_run):
            if p == phase:
                prior_consecutive += 1
            else:
                break
        if (gated.status == VerdictStatus.REPEAT
                and prior_consecutive >= self.playbook.tunables.max_retries
                and spec.on_verdict.get("advance") is not None):
            reason = (gated.gate_reason or "") + " [repeat cap reached — forced advance]"
            gated = gated.model_copy(update={"status": VerdictStatus.ADVANCE, "gate_reason": reason})
        result = result.model_copy(update={"verdict": gated})
        # remember WHY a gate failed so the NEXT plan is told (clears on a pass) — GAP 3
        self._gate_feedback = gated.gate_reason
        self.journal.append_phase(seq, result)
        return result

    def _journal_invocation(self, seq: int, phase: Phase, inv) -> None:
        """Journal a non-data capability outcome (P3 airlock step 1). `error` and `empty`
        (clean-empty) leave no trace in the phase delta — the ops they'd have carried never
        existed — so without their own entry they'd vanish from the durable record entirely.
        The entry keys the boundary outcome on `decision`/`observation.outcome`, keeping the
        two DISTINGUISHABLE downstream (part4-capability §4: error ≠ clean-empty). These are
        `kind="invocation"` entries: they SHARE the phase's seq (an annotation of that phase,
        not a numbered step of their own), so phase/step numbering — and every golden seq —
        is untouched; replay ignores them (no delta)."""
        self.journal.append(JournalEntry(
            seq=seq, ts=self._clock(), kind="invocation",
            phase_id=phase, actor="engine", intent=inv.intent,
            action={"capability": inv.intent, "provider": inv.provider,
                    "params": dict(inv.params)},
            observation={"outcome": inv.outcome, "reason": inv.reason},
            decision=inv.outcome))

    def _close_outcome(self, phases_run: list[Phase], confirmed) -> CloseOutcome | None:
        if self.playbook.terminal_phase not in phases_run:
            return None
        return CloseOutcome.RESOLVED if confirmed is not None else CloseOutcome.MITIGATED
