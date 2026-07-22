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
    # theta / evidence_floors / max_items were DELETED as dead, never-read knobs
    # (2026-07-22 review, findings 8/4/9): gate.min_facts is the live per-phase evidence
    # floor and op_ceiling the live per-phase bound (extra="forbid" makes any lingering
    # yaml key a loud load error, not a silent no-op).
    #
    # ── P4 belief arithmetic (DOMAIN-v3 §2.5 / DESIGN R-C4) — every weight/decay is a
    # tunable; the engine owns arithmetic only (INV-9). clock_skew_bound_s RETURNS here
    # (deleted as dead in P0, finding 10) now that temporal-join enforcement lands: it is
    # read by hypothesis/belief.py (proximity) and correlate_timeline.
    # Per-source clock-skew bound (seconds), R-J2: a temporal proximity/join between two
    # sources never asserts ordering tighter than the COMBINED bound of the pair;
    # "default" covers a source without an explicit entry. Monitoring stacks are
    # NTP-tight; ticket/CMDB/human records are minute-granular; vcs/build stamps ride
    # client clocks; llm/engine stamps are the engine's own clock.
    clock_skew_bound_s: dict[str, float] = Field(
        default_factory=lambda: {
            "prometheus": 30.0, "ocp": 30.0, "appd": 60.0, "splunk": 60.0,
            "bigpanda": 60.0, "git": 120.0, "artifactory": 120.0, "servicenow": 300.0,
            "cmdb": 300.0, "human": 300.0, "llm": 0.0, "engine": 0.0, "default": 300.0})
    # temporal-proximity decay half-life (seconds) BEYOND the combined skew window: at
    # `excess = proximity_halflife_s` past the window, proximity halves.
    proximity_halflife_s: float = Field(default=1800.0, gt=0)
    # topological specificity: per-structural-hop decay from the anomaly, floored for a
    # subject unreachable over the structural spine (never zero — evidence about an
    # unplaced entity still weighs, dimly).
    specificity_decay: float = Field(default=0.8, gt=0, le=1.0)
    specificity_floor: float = Field(default=0.25, ge=0, le=1.0)
    # the LLM band's pseudo-evidence mass in the weighted blend: a hypothesis with no
    # resolvable evidence scores exactly its band; evidence mass beyond this pulls the
    # score toward the for/against split.
    prior_weight: float = Field(default=1.0, gt=0)
    # correlate_timeline lookback (seconds): how far BEFORE symptom onset a change event
    # still counts as a temporally-correlated candidate; both window edges are additionally
    # widened by the combined clock-skew bound (R-J2 — the join is a tolerance window).
    correlation_window_s: float = Field(default=3600.0, ge=0)


class Doctrine(BaseModel):
    """The playbook-authored investigation METHOD the live planner's prompt carries — persona,
    evidence contracts, fault-class rooting conventions, progression prose — as versioned DATA
    (Part III §3: "prompt doctrine as playbook data"). The planner ASSEMBLES its system prompt
    from these fragments + the derived catalog/validity lists; the engine never restates domain
    doctrine in code, so a doctrine change is a playbook edit, not an engine release (and a
    derived list can't drift the way the hand-restated source list dropped `bigpanda`).
    Each fragment is verbatim prompt text — the `- ` bullet prefix and two-space continuation
    indents included — so assembly is pure concatenation, never re-wrapping."""

    model_config = ConfigDict(extra="forbid")

    persona: str              # who the reasoner is + the per-turn loop + the diagnostic stance
    evidence_ops: str         # ops-vs-calls contract: the direct-op whitelist (symptom node +
                              #   onset facts, hypotheses, no_evidence) — everything else via tools
    fact_rules: str           # which facts may be hand-authored (symptom + service RED predicates)
    frame_contract: str       # the FRAME same-turn contract (call tools AND seed symptom + onset)
    rooting: str              # fault-class rooting conventions (root at the actionable cause)
    investigate_advance: str  # when INVESTIGATE may advance (direct evidence + rival ruled out)
    verify_advance: str       # when VERIFY confirms and advances (symptom cleared)
    hypothesis_method: str    # always a leader + a distinct rival; actively refute
    progression: str          # the per-turn progression rule (phase scope; advance on gate-pass)


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
    # the live planner's prompt doctrine (Part III §3). Optional: scripted/batch runs never read
    # it; a live planner without one falls back to the packaged incident playbook's doctrine.
    doctrine: Doctrine | None = None

    def phase(self, pid: Phase) -> PhaseSpec:
        for p in self.phases:
            if p.id == pid:
                return p
        raise KeyError(f"no phase {pid} in playbook {self.id}")
