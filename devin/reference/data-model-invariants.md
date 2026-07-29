# The Investigation Workbench — Data Model Invariants (LAW)

> **Purpose.** The violable domain invariants + where each is enforced. Consulted before any non-trivial
> engine edit (per `_index/per-edit-gate.md`). Violating one of these = the engine misbehaved.
> **Update trigger.** A new invariant discovered OR the closed registry / phase contract changes.
> **Last refreshed:** 2026-07-21 — constitution birth (M0 takeover). Grounded in the shipped engine + `engine/docs/DESIGN.md`.

> **Product invariant (the promise these serve):** *Every production-changing action is human-approved,
> reversible, and reconstructable back to its evidence.*

---

## Domain hierarchy (the top-level invariant — everything else is consequence)

> **One `PhaseResult`, one `fold()`, three projections.** Every phase emits the identical `PhaseResult`;
> a single fold writes it into the **graph blackboard**, the **hypothesis ledger**, and the **append-only
> journal**. The **journal is the source of truth**; graph + ledger are projections that replay rebuilds
> exactly. The investigation flow: `Alert → Anomaly (Frame) → typed graph + Facts → ranked Hypotheses →
> confirmed Hypothesis (= root cause) → gated Remediation → Verify → human Close`.

## Invariants

*(All established 2026-07-21; citations verified against the shipped engine.)*

### INV-1 — Human approves every production-changing action

- **Rule.** A `WRITE`-effect capability is blocked unless the phase carries `writes_allowed` — set **only
  inside an approved REMEDIATE gate**. A read-only phase carries no WRITE intent; the gate blocks any write
  regardless. Closing an investigation is a human act.
- **Engine enforcement.** `runtime/engine.py` passes `spec.writes_allowed` to `CapabilityLayer.serve`
  (`layer.py:168`, gate-first → `_gate` `:141`, WRITE block `:146`) — the single enforcement point.
  `allowed_intents` are LLM-facing abstract categories only, not a write check.
- **Canonical example.** `RemediationAdapter` (WRITE) is **not** in `ALL_ADAPTERS`/`default_adapters()`;
  it's wired inline only where a write is intended (`runtime/scenarios.py`), so the golden path stays write-free.

### INV-2 — The journal is the source of truth; replay rebuilds the rest

- **Rule.** The append-only NDJSON journal is authoritative; graph + ledger are projections. Every mutation
  goes through the single fold; `rebuild(journal)` must reconstruct graph + ledger **exactly**.
- **Engine enforcement.** `graph/fold.py:apply_delta` is the ONLY graph+ledger mutation seam; `fold()` =
  `apply_delta` + `journal.append_phase`; `rebuild()` replays. `journal/journal.py` is append-only/versioned;
  on load the journal wins. `tests/unit/test_projection.py:test_fold_and_replay_equivalence` proves fold ≡ replay.

### INV-3 — Closed typed registry; the LLM never invents a type

- **Rule.** Node/edge/predicate/event vocabularies are **closed enums**; the LLM classifies into the catalog
  and never invents (one escape hatch: `generic_ci`, "never dropped"). Every enum member has a spec.
- **Engine enforcement.** `domain/enums.py` (closed StrEnums); `domain/registry.py` asserts completeness at
  **import** — raises `RuntimeError('registry incomplete')` if a member lacks a spec (re-asserted in
  `domain/nodes|edges/__init__.py`), so any test importing the registry fails at collection.
  `domain/catalog.py:render_catalog` (+ `render_nodes`/`render_edges`) renders the grammar from the registry.
- **Canonical example.** `hypothesis` is a first-class node (root cause *is* `Hypothesis{status=confirmed}`);
  the dead `RootCause`/`Remediation` node types were dropped.

### INV-4 — Fact provenance is split, exactly one channel

- **Rule.** A Fact carries **exactly one** belief channel: `confidence` (for INFERRED facts) OR
  `source_reliability` (for MEASURED facts) — never both, never neither.
- **Engine enforcement.** `domain/fact.py:Fact` — the `_belief_channel` model-validator (`fact.py:57-78`).
  The reducer soft-rejects belief-channel-violating facts rather than crashing a live run (`graph/reducer.py`
  records a Rejection).
- **Canonical example.** A metric reading = MEASURED (`source_reliability`); an LLM-proposed correlation =
  INFERRED (`confidence` ∈ {LOW,MED,HIGH} with a mandatory `basis`).

### INV-5 — Bi-temporality; static vs time-varying

- **Rule.** Node `props` are **identity/immutable only**; any time-varying attribute is a **Fact** with a
  valid-time window (`valid_from`/`valid_to`) vs observed-time (`observed_at`); a superseding fact closes the
  prior `valid_to`. Temporal correlations never assert ordering tighter than `clock_skew_bound`.
- **Engine enforcement.** `domain/fact.py`, `domain/edge.py` (bi-temporal lifecycle); `graph/graph.py:108`
  (`facts_valid_at`; superseding closes prior `valid_to`, `graph.py:49`).
- **Canonical example.** A pod's running image or replica count is a Fact, not a node prop — so the graph is reconstructable as of incident-start.

### INV-6 — Evidence edges are derived, never emitted

- **Rule.** SUPPORTS/REFUTES evidence edges are **derived inside the fold** from the ledger's canonical
  fact-id lists — the planner never emits them, so the graph view cannot disagree with the ledger.
- **Engine enforcement.** `graph/fold.py:_project_evidence_edges`.

### INV-7 — Off-catalog output is rejected before the reducer

- **Rule.** The LLM emits only closed typed ops; any op referencing an unknown type/id or otherwise
  off-catalog is **rejected and repaired** before it reaches the reducer — the closed registry is the guard,
  not the prompt.
- **Engine enforcement.** `runtime/live_planner.py` (an unknown op kind is dropped; repairs are tracked) —
  the second authoritative guard after the registry closure (INV-3). The reducer then materializes only valid
  typed ops (`graph/reducer.py`).

### INV-8 — Hypothesis promotion gate

- **Rule.** A hypothesis is promoted only when the leader's confidence clears the `confidence_gate` **and** it
  leads the runner-up by at least `delta` **and** there is no unrefuted rival. Refuted hypotheses are **kept**
  as evidence, never deleted.
- **Engine enforcement.** `ledger/ledger.py:promotion_ok` (`lead.confidence.value ≥ confidence_gate` and
  `(lead − runner) ≥ delta`); thresholds from the playbook `tunables:`, never engine constants. *(`theta` is a
  defined-but-unwired tunable — dead knob.)*

### INV-9 — Zero tuning constants in engine code

- **Rule.** Every knob (`confidence_gate`, evidence floors, op ceilings, promotion `delta`,
  `source_reliability`, `clock_skew_bound`) lives in the playbook `tunables:` block; engine code holds only arithmetic.
- **Engine enforcement.** `playbooks/incident.yaml` `tunables:`; a constant hard-coded in `runtime/` or
  `graph/` is a violation.

## Reference relationships (structural)

| From | To | Rule |
|---|---|---|
| Edge (causal) | Node → Node | Directional **effect → cause**; inferred/causal edges carry mandatory `Confidence` + evidence; `origin` ∈ {declared, discovered, inferred}. |
| Fact | SubjectRef `{domain,id}` | A Fact attaches to a subject via `subject_ref`; predicate is registry-controlled (`predicate_allowed`). |
| Evidence edge | Hypothesis ↔ Fact | SUPPORTS/REFUTES — derived from the ledger (INV-6), never authored. |
| SubjectRef | (domain, id) | Unique on `(domain, id)`; `kind` excluded from the key; never a bare `incident_id`. |

## Anti-patterns to watch for

- **A tuning constant creeping into engine code** → violates INV-9. Move it to `playbooks/*.yaml` `tunables:`.
- **A time-varying attribute stored as a node prop** → violates INV-5. Model it as a Fact.
- **The planner emitting a SUPPORTS/REFUTES edge** → violates INV-6. Evidence edges are fold-derived only.
- **A new NodeType/EdgeType without a spec** → breaks the **import-time closure assertion** (INV-3). Add the spec in the same change.
- **A write path that bypasses `CapabilityLayer.serve`/`_gate`** → violates INV-1. All side effects go through the one gate-first seam.
