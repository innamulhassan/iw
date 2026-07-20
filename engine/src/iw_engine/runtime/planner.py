"""Planner — the JUDGMENT seam (one of the three authors). A planner turns the current
context into a PlanOutput: a bounded list of typed ops + the narrative + the verdict. It
sits behind a Protocol so the deterministic `ScriptedPlanner` (tests/scenarios) and a
future live LLM planner are interchangeable (principle 11). Its ONLY structural output
is typed ops — never free prose (principle 7).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..capability.layer import CapabilityCall
from ..domain.enums import Phase
from ..domain.operations import Operation
from ..domain.phase_result import PhaseVerdict
from ..domain.playbook import PhaseSpec, Tunables
from ..domain.subject import SubjectRef


@dataclass
class PlanContext:
    subject: SubjectRef
    phase: Phase
    phase_spec: PhaseSpec
    goal: str
    graph_view: dict
    hypotheses: list[dict] = field(default_factory=list)   # ranked ledger summary
    tunables: Tunables = field(default_factory=Tunables)
    gate_feedback: str | None = None   # WHY the last gate downgraded this phase (GAP 3) — the
    #                                    engine sets it from the prior verdict's gate_reason so a
    #                                    live planner replans against the real reason, not a guess
    messages: list[dict] = field(default_factory=list)   # operator steering (obs 2 two-way chat) —
    #                                    the session injects its chat buffer so the LIVE planner can
    #                                    be steered ("check the DB", "ignore CHG-9"); mock ignores it


class PlanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    phase: Phase
    calls: list[CapabilityCall] = Field(default_factory=list)   # capability invocations -> data ops
    ops: list[Operation] = Field(default_factory=list)          # planner-direct ops (hypotheses, no_evidence)
    narrative: str
    verdict: PhaseVerdict
    next_actions: list[str] = Field(default_factory=list)


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
