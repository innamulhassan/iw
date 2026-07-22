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
from ..journal.journal import Journal
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
        combined = data_ops + list(plan.ops)

        ceiling = self.playbook.tunables.op_ceiling.get(phase.value)
        ops = combined[:ceiling] if ceiling else combined

        mat = materialize(ops, seq, self.graph, self.playbook.tunables,
                          anomaly_ref=self._anomaly_ref)
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

    def _close_outcome(self, phases_run: list[Phase], confirmed) -> CloseOutcome | None:
        if self.playbook.terminal_phase not in phases_run:
            return None
        return CloseOutcome.RESOLVED if confirmed is not None else CloseOutcome.MITIGATED
