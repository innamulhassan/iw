"""The per-phase journey: PhaseRecord and Step. 04-data-model §4.1–§4.2.

The Step list IS the regulator-grade audit trail; `touched` points into the graph, `evidence`
flows up into the typed output.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import Field, model_validator

from .common import Base
from .enums import PhaseState, StepKind
from .subject import SubjectRef


class Step(Base):
    """One entry in the journey. 04-data-model §4.2."""

    seq: int
    at: Optional[str] = None
    kind: StepKind
    capability: Optional[str] = None          # resolved from a `need` via the registry — never hardcoded
    input: dict[str, Any] = Field(default_factory=dict)
    result: Optional[Any] = None
    touched: list[str] = Field(default_factory=list)   # → graph node/fact ids read or written
    evidence: list[str] = Field(default_factory=list)  # → deep links that flow up into the output
    note: Optional[str] = None

    @model_validator(mode="after")
    def _tool_call_names_a_capability(self) -> "Step":
        if self.kind is StepKind.tool_call and not self.capability:
            raise ValueError("a tool_call step must name the capability it resolved to")
        return self


class PhaseRecord(Base):
    """The per-phase journey. 04-data-model §4.1. `id` = `subject.id:phase:attempt`. One record
    belongs to exactly one subject (per-incident — never cluster-scoped)."""

    id: str
    subject: SubjectRef
    phase: str
    goal: str
    state: PhaseState = PhaseState.active     # active|waiting_input|waiting_approval|blocked|done|failed
    plan: Optional[str] = None
    steps: list[Step] = Field(default_factory=list)
    output: Optional[dict[str, Any]] = None   # the typed phase output (validated vs output_schema)
    summary: Optional[str] = None
    opened_at: Optional[str] = None
    closed_at: Optional[str] = None           # null while open
