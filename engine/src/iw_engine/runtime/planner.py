"""Planner — the JUDGMENT seam (one of the three authors). A planner turns the current
context into a PlanOutput: a bounded list of typed ops + the narrative + the verdict. It
sits behind a Protocol so the deterministic `ScriptedPlanner` (tests/scenarios) and a
future live LLM planner are interchangeable (principle 11). Its ONLY structural output
is typed ops — never free prose (principle 7).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..capability.layer import CapabilityCall
from ..domain.operations import Operation
from ..domain.phase_result import PhaseVerdict, Rejection
from ..domain.playbook import PhaseSpec, Tunables
from ..domain.subject import SubjectRef


@dataclass
class PlanContext:
    subject: SubjectRef
    phase: str                       # playbook-declared phase id (P7 phase-as-data)
    phase_spec: PhaseSpec
    goal: str
    focus: dict = field(default_factory=dict)   # graph/tools.focus_slice — THE bounded reasoning
    #                                  view (P7 projections-drive-reasoning): cause path + suspects
    #                                  + frontier in full, healthy/ruled-out collapsed to counts.
    #                                  Replaces the old flat render_slice full-graph-capped dump.
    entry_phase: str | None = None   # the playbook's entry-phase role binding, handed through so
    #                                  a planner can key entry-phase behaviour on DATA, never a name
    hypotheses: list[dict] = field(default_factory=list)   # ranked hypothesis store summary
    tunables: Tunables = field(default_factory=Tunables)
    gate_feedback: str | None = None   # WHY the last gate downgraded this phase (GAP 3) — the
    #                                    engine sets it from the prior verdict's gate_reason so a
    #                                    live planner replans against the real reason, not a guess
    messages: list[dict] = field(default_factory=list)   # operator steering (obs 2 two-way chat) —
    #                                    the session injects its chat buffer so the LIVE planner can
    #                                    be steered ("check the DB", "ignore CHG-9"); mock ignores it
    rejections: list[Rejection] = field(default_factory=list)   # the reducer rejections from the
    #                                    PREVIOUS step (P3 step 2 — the R-K2 bounded repair loop):
    #                                    the planner is told WHY each dropped op was dropped, so a
    #                                    live model repairs instead of re-emitting into silence
    correlations: list[dict] = field(default_factory=list)   # P4 — the ENGINE-computed skew-tolerant
    #                                    change→onset correlation (belief.correlate_timeline), handed
    #                                    to every phase whose playbook allowed_intents declare
    #                                    `correlate_timeline`: a deterministic evidence HINT (never a
    #                                    graph mutation); `ordering_certain=False` items sit inside
    #                                    the combined clock-skew bound (R-J2 — ordering not asserted)


class TodoStatus(StrEnum):
    """A to-do's lifecycle. The planner AUTHORS a plan of PENDING to-dos; the engine executes each
    1:1 (call→invocation) and the checklist ticks DONE as its phase folds. Kept deliberately small —
    the to-do layer is a CHECKLIST, not a workflow engine (owner: lightweight; the LLM authors the
    to-dos; execution stays 1:1; NO automatic decomposition engine)."""

    PENDING = "pending"
    DONE = "done"


class Todo(BaseModel):
    """One checklist item of a plan (F1 — the to-do LAYER). A to-do groups the capability CALLS and
    the planner-direct OPS that serve ONE short objective, so a plan reads as a checklist: "here is
    my plan as to-dos, and here is each one executing." It is an ATTRIBUTION layer over the SAME
    ops the engine already runs — `PlanOutput` flattens {calls, ops} back to the unchanged 1:1
    execution loop, never a decomposition engine. `op_budget` and `delegate` are documented SEAMS
    this layer OWNS (a per-to-do op ceiling — the op-ceiling-per-todo reserve-quota home; and a
    delegatable to-do for MCP/A2A fan-out — F2): declared here, deliberately NOT wired."""

    model_config = ConfigDict(extra="forbid")

    objective: str                                              # the short aim this to-do serves
    calls: list[CapabilityCall] = Field(default_factory=list)   # capability invocations -> data ops
    ops: list[Operation] = Field(default_factory=list)          # planner-direct ops
    status: TodoStatus = TodoStatus.PENDING
    # ── SEAMS (declared, deliberately UNWIRED — the to-do layer is their documented home) ──
    op_budget: int | None = None   # per-to-do op ceiling (op-ceiling-per-todo) — the F1 reserve-quota seam
    delegate: bool = False         # a delegatable to-do (MCP/A2A fan-out) — F2 seam; execution stays local


class PlanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: str
    calls: list[CapabilityCall] = Field(default_factory=list)   # capability invocations -> data ops
    ops: list[Operation] = Field(default_factory=list)          # planner-direct ops (hypotheses, no_evidence)
    narrative: str
    verdict: PhaseVerdict
    next_actions: list[str] = Field(default_factory=list)
    # the reject+repair drops the planner made mapping raw LLM output -> this plan (off-catalog
    # tool, unparseable/illegal op, coerced verdict). The engine journals them as `repair` entries
    # and feeds them into the next PlanContext.rejections (M6) — unifying the planner's own
    # enforcement channel with the reducer's. Empty for the deterministic ScriptedPlanner, so the
    # scripted/golden path is untouched.
    repairs: list[str] = Field(default_factory=list)
    # F1 — the TO-DO LAYER: the plan grouped into a CHECKLIST of to-dos (each an objective + the
    # calls/ops that serve it). ADDITIVE + derivable: when to-dos are authored they are AUTHORITATIVE
    # and `calls`/`ops` are set to their EXACT flattening (so the engine's 1:1 execution loop reads
    # the same flat lists, UNCHANGED, and each call/op attributes to its to-do by position); when
    # absent, `effective_todos` derives ONE default to-do from calls/ops — so every existing
    # (scripted) plan reads as a single-item checklist with zero authoring churn.
    todos: list[Todo] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reconcile_todos(self) -> PlanOutput:
        # to-dos are AUTHORITATIVE when authored: pin the flat lists to their exact concatenation so
        # call→to-do and op→to-do attribution is exact AND the engine still executes the same flat
        # ops 1:1. When none are authored, the flat lists stand and effective_todos derives one.
        if self.todos:
            self.calls = [c for td in self.todos for c in td.calls]
            self.ops = [o for td in self.todos for o in td.ops]
        return self

    @property
    def effective_todos(self) -> list[Todo]:
        """The plan AS a checklist: the authored to-dos, or ONE synthesized to-do wrapping the flat
        calls/ops (objective = the narrative) for a plan that authored none — the scripted default,
        so every existing plan reads as a single-item checklist. DERIVED (never stored), so a
        post-construction `model_copy(update={"calls": ...})` — the session's write-gate injection
        and refine/deny — stays consistent without re-running the validator."""
        if self.todos:
            return self.todos
        if self.calls or self.ops:
            return [Todo(objective=self.narrative, calls=list(self.calls), ops=list(self.ops))]
        return []

    def call_todo_indices(self) -> list[int]:
        """The to-do index each flat call serves (parallel to `calls`) — the engine stamps it on
        every invocation so the record shows which to-do each call executed for."""
        return [i for i, td in enumerate(self.effective_todos) for _ in td.calls]

    def op_todo_indices(self) -> list[int]:
        """The to-do index each flat op serves (parallel to `ops`)."""
        return [i for i, td in enumerate(self.effective_todos) for _ in td.ops]


@runtime_checkable
class Planner(Protocol):
    def plan(self, ctx: PlanContext) -> PlanOutput: ...


class ScriptedPlanner:
    """Replays a fixed sequence of PlanOutputs — the deterministic twin used by scenarios
    and unit tests. Asserts each output's phase matches what the engine is running, so a
    mis-ordered script fails loudly instead of silently drifting."""

    def __init__(self, script: list[PlanOutput]) -> None:
        self._script = list(script)
        self._i = 0

    def plan(self, ctx: PlanContext) -> PlanOutput:
        if self._i >= len(self._script):
            raise RuntimeError(
                f"ScriptedPlanner exhausted at phase {ctx.phase} (script has {len(self._script)} steps)")
        out = self._script[self._i]
        self._i += 1
        if out.phase != ctx.phase:
            raise RuntimeError(
                f"ScriptedPlanner step {self._i} is for {out.phase} but engine is in {ctx.phase}")
        return out

    @property
    def exhausted(self) -> bool:
        return self._i >= len(self._script)
