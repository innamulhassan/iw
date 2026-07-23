"""Playbook — declarative config for ONE incident class (DESIGN §2.3). Three-authors rule:
the playbook holds only WHAT/WHEN (phases, allowed_intents, gates, produces_required,
transitions) + a single `tunables:` block that enumerates EVERY knob. The engine owns
only mechanics/arithmetic — no tuning constant lives in engine code.

P7 phase-as-data (Part III §1): the engine has NO Phase enum. A phase id is a STRING this
playbook declares; every cross-reference (`entry_phase`/`terminal_phase` role bindings,
`on_verdict` routes, `tunables.op_ceiling` keys) is validated against the declared phase
list at LOAD time, so a typo is a loud load error, never a silent dead-end. The engine keys
only on role bindings — nothing in the loop needs a phase *name*.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .enums import NodeType, VerdictStatus


class Tunables(BaseModel):
    """The ONE schema/registry of every tuning knob, each with its default (Failure-1 fix); the
    engine owns arithmetic only. NOTE: this is one place knobs are DEFINED, not one place their
    VALUES live — a playbook's yaml `tunables:` block OVERRIDES a subset (restating those values),
    and any knob it omits takes the default here (today `source_reliability`, `discovery_penalty`
    and `derive_transitions` are default-only — set nowhere in the shipped yaml)."""

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
    # P6 step 5 (part2 §3): derive `<state>_started`/`<state>_cleared` transition EVENTS from
    # boolean STATE flips in the reducer, so adapters/scenarios stop dual-authoring occurrence
    # twins. DEFAULT OFF: the shipped scenarios still author their transitions — and several
    # (deployment/infra/network) deliberately model stacks that DON'T emit them, so deriving
    # alongside would add events their goldens don't hold. Flipping this on + de-dual-authoring
    # the scenario twins + regenerating goldens is the recorded P7 follow-up. Threshold flips
    # are absent by design: the dictionary carries no threshold values yet (not invented).
    derive_transitions: bool = False
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
    # ── P7 focus slice (graph/tools.focus_slice — the bounded reasoning view every plan
    # receives). All three are INV-9 knobs of the B9.3 view: ~budget rendered nodes
    # regardless of graph size, latest-per-predicate fact cards, and how far the structural
    # frontier (the expansion surface) reaches beyond the full tier.
    focus_budget: int = Field(default=40, gt=0)
    focus_facts_per_node: int = Field(default=6, ge=0)
    focus_frontier_hops: int = Field(default=1, ge=0)


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
    """Declarative PREDICATE SET the engine applies to the planner's proposed terminal-intent
    verdicts — ADVANCE **and DONE** (P7 step 4: DONE is gated, never a free bypass). Playbooks
    COMPOSE gates from these predicates; the engine implements only the predicate semantics
    (Part III §1). A failed gate downgrades the verdict to REPEAT with the failing reason."""

    model_config = ConfigDict(extra="forbid")

    min_facts: int = 0            # >= this many facts folded THIS phase step
    promotion: bool = False       # the strengthened promotion_ok: leader over the confidence
    #                               gate, beats the field by delta, NO alive unrefuted rival
    refutation_attempted: bool = False   # >=1 rival refuted OR the leader itself challenged
    symptom_cleared: bool = False        # the symptom role-binding carries the playbook's
    #                                      declared cleared-event (recovery confirmed)
    human_approved: bool = False         # a human gate_decision (approve/refine) is on the
    #                                      journal for THIS phase (the governance terminal)


class OutcomeRule(BaseModel):
    """Playbook-declared terminal outcome labels (P7 step 4: the resolved/mitigated rule
    leaves engine code — `_close_outcome` reads THIS, so a new domain can label its
    terminals without an engine release). The rule: reaching the terminal phase WITH a
    confirmed hypothesis yields `confirmed_root`; without one, `no_confirmed_root`."""

    model_config = ConfigDict(extra="forbid")

    confirmed_root: str = Field(default="resolved", min_length=1)
    no_confirmed_root: str = Field(default="mitigated", min_length=1)


class PhaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)   # a playbook-declared phase name (data, not an engine enum)
    goal: str
    allowed_intents: list[str]
    produces_required: list[str] = Field(default_factory=list)   # PhaseResult fields that must be non-empty
    gate: GateSpec = Field(default_factory=GateSpec)
    on_verdict: dict[str, str] = Field(default_factory=dict)    # verdict status -> next phase id
    writes_allowed: bool = False    # this phase may execute write-effect capabilities (the human-gated phase)
    # PHASE-REVIEW (owner 2026-07-23): when this phase COMPLETES its goal and would ADVANCE to a
    # DIFFERENT phase, pause for a human DIRECTION approval first (summary → approve/refine/deny).
    # PLAYBOOK DATA the interactive session driver reads — the batch Engine.run()/gen_golden/
    # run_live path never consults it, so goldens are untouched (the review is a session-driver
    # pause, never an engine/controller gate predicate). A phase that opens the Act WRITE-gate has
    # its review SUBSUMED by that gate (one pause, not two — the write-gate is the human checkpoint).
    review_before_advance: bool = False


class Playbook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    applies_to: str
    capabilities: list[str] = Field(default_factory=list)
    entry_phase: str | None = None                 # role binding; defaults to the FIRST declared phase
    phases: list[PhaseSpec]
    tunables: Tunables = Field(default_factory=Tunables)
    # domain role-bindings (retire the engine's hardcoded constants — DESIGN depth §E):
    symptom_node: NodeType = NodeType.ANOMALY      # the entry-phase symptom anchor captured for node-expansion
    # "the incident is the first node" (owner) — the SUBJECT/ORIGIN node type the
    # investigation's external id binds to (P7 step 5: out of session/bundle code)
    subject_node: NodeType = NodeType.INCIDENT
    terminal_phase: str | None = None              # role binding; defaults to the LAST declared phase
    # the event type on the symptom node that means "the symptom cleared" — DATA the
    # symptom_cleared gate predicate keys on, never an engine-hardcoded vocabulary word
    symptom_cleared_event: str = Field(default="cleared", min_length=1)
    # terminal outcome labels + rule (P7 step 4 — out of engine._close_outcome)
    outcomes: OutcomeRule = Field(default_factory=OutcomeRule)
    # the live planner's prompt doctrine (Part III §3). Optional: scripted/batch runs never read
    # it; a live planner without one falls back to the packaged incident playbook's doctrine.
    doctrine: Doctrine | None = None

    @model_validator(mode="after")
    def _validate_phase_refs(self) -> Playbook:
        """Phase ids are strings OF THIS PLAYBOOK (P7 phase-as-data): unique, and every
        cross-reference — role bindings, verdict routes, op_ceiling keys — must name a
        declared phase. Verdict-route KEYS must be the engine's verdict vocabulary."""
        if not self.phases:
            raise ValueError(f"playbook {self.id!r} declares no phases")
        ids = [p.id for p in self.phases]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"playbook {self.id!r}: duplicate phase ids {dupes}")
        known = set(ids)
        if self.entry_phase is None:
            self.entry_phase = ids[0]
        if self.terminal_phase is None:
            self.terminal_phase = ids[-1]
        for role, ref in (("entry_phase", self.entry_phase),
                          ("terminal_phase", self.terminal_phase)):
            if ref not in known:
                raise ValueError(
                    f"playbook {self.id!r}: {role} {ref!r} is not a declared phase (have {sorted(known)})")
        valid_verdicts = {v.value for v in VerdictStatus}
        for p in self.phases:
            for verdict, target in p.on_verdict.items():
                if verdict not in valid_verdicts:
                    raise ValueError(
                        f"playbook {self.id!r} phase {p.id!r}: on_verdict key {verdict!r} "
                        f"is not a verdict status (have {sorted(valid_verdicts)})")
                if target not in known:
                    raise ValueError(
                        f"playbook {self.id!r} phase {p.id!r}: on_verdict[{verdict!r}] routes to "
                        f"undeclared phase {target!r} (have {sorted(known)})")
        for key in self.tunables.op_ceiling:
            if key not in known:
                raise ValueError(
                    f"playbook {self.id!r}: tunables.op_ceiling key {key!r} "
                    f"is not a declared phase (have {sorted(known)})")
        return self

    def phase(self, pid: str) -> PhaseSpec:
        for p in self.phases:
            if p.id == pid:
                return p
        raise KeyError(f"no phase {pid} in playbook {self.id}")
