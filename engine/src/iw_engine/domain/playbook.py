"""Playbook — declarative config for ONE incident class (DESIGN §2.3). Three-authors rule:
the playbook holds only WHAT/WHEN (phases, allowed_intents, gates, produces_required,
transitions) + a single `tunables:` block that enumerates EVERY knob. The engine owns
only mechanics/arithmetic — no tuning constant lives in engine code.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .enums import NodeType, Phase


class Tunables(BaseModel):
    """Every tuning knob in ONE place (Failure-1 fix). Engine owns arithmetic only."""

    model_config = ConfigDict(extra="forbid")

    confidence_gate: float = 0.8
    op_ceiling: dict[str, int] = Field(default_factory=dict)          # phase -> max ops/phase
    max_retries: int = 2
    delta: float = 0.15                                               # promotion margin
    # Per-source fallback reliability for a MEASURED fact whose payload states none: the
    # reducer fills it, so adapters carry NO hardcoded constants (INV-9). Defaults mirror
    # the constants formerly hardcoded per adapter (behavior-preserving). Special keys:
    # "llm" is the default for a model-AUTHORED measured fact that omitted reliability
    # (an llm-SOURCED fact is inferred — it carries confidence, never reliability);
    # "engine" covers the reducer's own NoEvidence null-result facts.
    source_reliability: dict[str, float] = Field(
        default_factory=lambda: {
            "prometheus": 0.97, "splunk": 0.95, "appd": 0.95, "servicenow": 0.8,
            "cmdb": 0.8, "ocp": 0.99, "artifactory": 0.8, "git": 0.99,
            "bigpanda": 0.8, "llm": 0.9, "human": 0.8, "engine": 1.0})
    confidence_band: dict[str, float] = Field(
        default_factory=lambda: {"low": 0.3, "med": 0.6, "high": 0.9})
    # P3 type airlock: the multiplicative confidence penalty on an airlock-admitted edge (a
    # generic_ci substituted into a structural pair, or a CAUSED_BY blaming a generic_ci) —
    # provisional knowledge is admitted, never at full weight (DOMAIN-v3 §2.4 row 2).
    discovery_penalty: float = 0.75
    # theta / evidence_floors / max_items / clock_skew_bound_s were DELETED as dead,
    # never-read knobs (2026-07-22 review, findings 8/4/9/10): gate.min_facts is the live
    # per-phase evidence floor and op_ceiling the live per-phase bound; a clock-skew bound
    # returns properly when temporal-join enforcement lands (extra="forbid" makes any
    # lingering yaml key a loud load error, not a silent no-op).


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
