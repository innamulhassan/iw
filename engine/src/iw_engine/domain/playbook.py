"""Playbook — declarative config for ONE incident class (DESIGN §2.3). Three-authors rule:
the playbook holds only WHAT/WHEN (phases, allowed_intents, gates, produces_required,
transitions) + a single `tunables:` block that enumerates EVERY knob. The engine owns
only mechanics/arithmetic — no tuning constant lives in engine code.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .enums import NodeType, Phase, Source


class Tunables(BaseModel):
    """Every tuning knob in ONE place (Failure-1 fix). Engine owns arithmetic only."""

    model_config = ConfigDict(extra="forbid")

    confidence_gate: float = 0.8
    evidence_floors: dict[str, int] = Field(default_factory=dict)      # phase -> min facts
    max_items: dict[str, int] = Field(default_factory=dict)           # phase -> per-step maxItems
    op_ceiling: dict[str, int] = Field(default_factory=dict)          # phase -> max ops/phase
    max_retries: int = 2
    theta: float = 0.6                                                # promotion score threshold
    delta: float = 0.15                                               # promotion margin
    source_reliability: dict[str, float] = Field(
        default_factory=lambda: {s.value: 0.8 for s in Source})
    confidence_band: dict[str, float] = Field(
        default_factory=lambda: {"low": 0.3, "med": 0.6, "high": 0.9})
    clock_skew_bound_s: dict[str, float] = Field(default_factory=dict)  # source -> seconds


class GateSpec(BaseModel):
    """Declarative guard the engine applies to the planner's proposed 'advance' verdict."""

    model_config = ConfigDict(extra="forbid")

    min_facts: int = 0
    require_confidence_gate: bool = False    # top hypothesis confidence >= tunables.confidence_gate
    require_refutation: bool = False         # >=1 refutation attempt recorded on the leading hypothesis


class PhaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Phase
    goal: str
    allowed_intents: list[str]
    produces_required: list[str] = Field(default_factory=list)   # PhaseResult fields that must be non-empty
    gate: GateSpec = Field(default_factory=GateSpec)
    on_verdict: dict[str, Phase] = Field(default_factory=dict)    # verdict status -> next phase
    writes_allowed: bool = False    # this phase may execute write-effect capabilities (the human-gated phase)


class Playbook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    applies_to: str
    capabilities: list[str] = Field(default_factory=list)
    entry_phase: Phase = Phase.FRAME
    phases: list[PhaseSpec]
    tunables: Tunables = Field(default_factory=Tunables)
    # domain role-bindings (retire the engine's hardcoded constants — DESIGN depth §E):
    symptom_node: NodeType = NodeType.ANOMALY      # the FRAME symptom anchor captured for node-expansion
    terminal_phase: Phase = Phase.CLOSE            # reaching it closes the investigation

    def phase(self, pid: Phase) -> PhaseSpec:
        for p in self.phases:
            if p.id == pid:
                return p
        raise KeyError(f"no phase {pid} in playbook {self.id}")
