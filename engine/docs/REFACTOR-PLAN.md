# Elegance + Composability Refactor Plan (audit synthesis)

I've verified the four audits against source. Every load-bearing claim holds: the `fold()` monopoly is bypassed by the live engine (`engine.py:104-125` inlines `_apply_to_graph` + `ledger.apply`; `fold()` has no live caller), the three domain constants are hardcoded in the "generic" engine (`allow_write = phase == Phase.REMEDIATE` at :88, `NodeType.ANOMALY` at :106, `Phase.CLOSE`/RESOLVED/MITIGATED in `_close_outcome`), the registry is a module of functions over module-global `NODE_SPECS`/`EDGE_SPECS` with closure checked against Python enums, `hyp:` is hand-minted in 4 sites bypassing `registry.node_id`, no `catalog`/`render_llm_schema`/`.discriminator` consumer exists, and `MockSource` is a bare class while `Adapter`/`Planner` are Protocols. Here is the decisive plan.

---

# Investigation Workbench — Consolidated Refactor Plan

**Framing.** The five load-bearing abstractions (uniform `PhaseResult` + one fold, discriminated `Operation` union + reducer-as-trust-boundary, journal-as-truth with `rebuild`, `Planner`/`Adapter` Protocols, types-as-data specs) are correct and survive all four depth-asks intact. The work is **subtractive + additive at existing seams, never a rewrite.** Three problems recur across all four audits: (1) the engine has grown domain/policy leaks that break its own three-authors rule, (2) several promised seams are authored-but-unwired (fold monopoly, discriminators/catalog, `next_actions` feedback, Source Protocol), and (3) `run()` is atomic — the one thing between a batch engine and an interactive one. Fix those and the four asks compose in rather than bolt on.

---

## A. TOP STRUCTURAL REFACTORS — ranked by leverage

### A1 — Unify the mutation path: extract `apply_delta`, make the fold monopoly true again
**What.** `graph/fold.py`: split `fold()` at its natural seam.
```python
def apply_delta(result, seq, graph, ledger):   # THE one graph+ledger mutation seam
    _apply_to_graph(result, graph)
    ledger.apply(result.hypotheses_updated, seq)
def fold(result, seq, graph, ledger, journal):  # convenience for non-gated callers/tests
    apply_delta(result, seq, graph, ledger); journal.append_phase(seq, result)
```
`engine._run_phase` (`engine.py:116-129`) replaces its inlined 9-line apply loop + `ledger.apply` with `apply_delta(result, seq, self.graph, self.ledger)`, then gates, then `journal.append_phase(seq, gated)`. `rebuild()` calls `apply_delta`.
**Why elegance/composability.** Deletes the copy-paste that today keeps replay-equals-live true only by discipline (and which already diverges — engine journals the *gated* verdict, `fold()` the *ungated* one). Makes "ONLY fold writes the projections" structurally true. **This is the keystone for A3**: a pure computed delta that is *applied separately* is exactly what a human write-gate holds un-applied pending approval.
**Files.** `graph/fold.py`, `runtime/engine.py:116-129`.
**Risk.** Low. Behavior-identical; the equivalence test now exercises the real path.

### A2 — De-leak the engine: hoist the three domain constants + the write-gate into the playbook
**What.** Move policy out of `engine.py` into `PhaseSpec`/`Playbook` (`domain/playbook.py`):
- `engine.py:88` `allow_write = phase == Phase.REMEDIATE` → `PhaseSpec.writes_allowed: bool = False` + an injected `ApprovalGate` Protocol (`approve(call) -> bool`) defaulting to `AutoApprove`.
- `engine.py:104-108` `NodeType.ANOMALY` symptom capture → `Playbook.symptom_node: NodeType` (or `PhaseSpec.symptom_anchor: bool`).
- `engine.py:132-135` `Phase.CLOSE` + RESOLVED/MITIGATED rule → `Playbook.terminal: {phase, outcome_rule}`.
**Why.** A folder-loaded domain with different phase names / terminal semantics / focus type currently **cannot be added without editing `engine.py`** — this restores the design's own three-authors principle (playbook owns WHAT/WHEN, engine owns mechanics). The same `writes_allowed`/`requires_approval` flag becomes the **pause signal** the interactive driver (A3) waits on — one refactor, two wins.
**Files.** `runtime/engine.py:88,104-108,132-135`, `domain/playbook.py` (PhaseSpec/Playbook), `playbooks/incident.yaml`.
**Risk.** Low. Defaults reproduce today's behavior exactly (`writes_allowed` only on remediate, `symptom_node: anomaly`, `terminal: close`).

### A3 — Make the engine a resumable stepper: `run()` → driver over `step()`
**What.** Hoist loop state (`phase`, `phases_run`, `steps`) off `run()`'s locals (`engine.py:56-64`) into instance/`RunState`; expose `step() -> StepOutcome{phase_result, next_phase, paused_for?}`. `run()` becomes `while not done: step()`. At a `writes_allowed` phase, `step()` suspends *before applying* the write delta (trivial now that A1 separates compute from apply) and resumes with an injected decision + optional operator ops.
**Why.** Unblocks **three asks at once**: interactive pause/approval (d), mid-run chat injection (c/d — a human message is just more ops via the existing `Source.HUMAN`), and stepwise node-expansion (b). Low-risk because the state spine already lives on `self`; the engine is already a resumable object pretending to be a batch function.
**Files.** `runtime/engine.py:54-72`.
**Risk.** Low. `run() = while not done: step()` keeps all 46 tests verbatim.

### A4 — Registry-as-instance: `class Registry` owning specs + id-helpers + validators
**What.** Convert `domain/registry.py`'s module functions over module globals into an instantiable `Registry(node_specs, edge_specs, phases, manifest)` carrying `node_id`/`edge_id`/`edge_allowed`/closure. Keep a module-level default instance bound to the incident domain so every `from ..domain import registry; registry.node_id(...)` call and all 46 tests are untouched. Thread the instance through `reducer`/`engine`. Validate `op.type` against catalog keys, not the global enum.
**Why.** THE structural prerequisite for folder-loaded domains (a): two domains can't coexist in one process today, and closure is enforced against a shared `NodeType`/`EdgeType` enum a new domain would have to append to. This same object is the natural home for the catalog projection (A5) and the domain manifest (A2) — one refactor, three asks.
**Files.** `domain/registry.py`, `graph/reducer.py`, `runtime/engine.py`, `domain/nodes/__init__.py`, `domain/edges/__init__.py`.
**Risk.** Medium (largest of the structural set; touches every id call-site). Contain by keeping the default binding + deferring the enum→str/YAML-loader change to a later, separate step (see E). Do **not** smuggle full folder-loading in as "a refactor" — A4 is the binding-time change that *enables* it.

### A5 — Catalog projection: activate the dead spec fields, give the LLM its grammar
**What.** Add `Registry.catalog()` / `catalog_prompt()`: (a) `Operation.model_json_schema()` is free (already a discriminated union); (b) a ~30-line renderer folds `NODE_SPECS`/`EDGE_SPECS` into the allowed-types prose (type → `discriminator`, `fact_predicates`, `event_types`) + the edge-legality allow-list. Thread allowed-types into `PlanContext` (`planner.py:23-30`).
**Why.** The single biggest blocker for the live-LLM planner (c): the spec claims (`enums.py:6`) to be the source of truth for the LLM's schema, but the derivation function does not exist and `NodeSpec.discriminator`/`tier`/`static_props`, `EdgeSpec.semantics`/`symmetric` are all dead (grep-confirmed). This *activates* them and makes the drift-proof claim true. A live planner otherwise has to import the registry directly (coupling) or hallucinate.
**Files.** new `domain/catalog.py` (or method on A4's Registry), `runtime/planner.py:23-30`.
**Risk.** Low. Purely additive; no test touches it.

---

## B. COMPOSABILITY SEAMS TO ESTABLISH

| Extension point | Protocol/interface | Where it attaches |
|---|---|---|
| **New domain (folder)** | `Registry` instance built from a manifest (`node_specs`, `edge_specs`, `phases`, `symptom_node`, `write_phases`, `terminal`, `entry_phase`). `NodeSpec.from_dict`/`load_specs(folder)` since specs are already frozen dataclasses of primitives. `SubjectRef.domain` already keys it. | A4 + A2. Manifest replaces the `enums.py` constants + the hardcoded tier-import tuples. |
| **New capability/tool** | Two seams, both clean today, plus one new: (1) `Adapter` Protocol (`normalize`) — add a shared `emit_node(ops, T, props) -> node_id` util to kill the ~30× `AddNode` + `node_id` copy-paste and the 3 divergent hand-rolled dedups; (2) **`Source` Protocol** (`query(intent, params) -> dict`) so `MockSource` becomes one impl and a live API client is a typed drop-in; (3) wire `playbook.capabilities` (`playbook.py:58`, currently decorative) to filter the layer so a domain scopes its own tools. | `capability/layer.py:74` (Source Protocol), `capability/adapters/*` (emit_node), `_helpers.py` (capabilities→layer). |
| **Live LLM planner** | `Planner` Protocol (already drop-in) + `PlanContext.allowed_types` from A5's catalog + `PlanContext.prior_next_actions` (close the advisory `next_actions` loop that is written and discarded today) + guardrails: enforce `allowed_intents`, enforce `max_retries`, honest `op_ceiling` truncation. | `runtime/planner.py:23-30`, A5. |
| **Interactive step + gate** | `ApprovalGate` Protocol (`approve(call) -> bool`, default `AutoApprove`) + `step()` suspension (A3) + `PlanContext` operator-message inbox field. Human input is ops via existing `Source.HUMAN`. | A2 + A3. |
| **Node-expansion** | `graph/expansion.py` → `Graph.frontier(anomaly, ledger) -> ranked[NodeId]` over existing `reachable_from`/`neighbors`; add `PlanContext.frontier`; have `render_slice` consume the ranking (fulfilling its own docstring). Optional per-`EdgeSpec` traversal-direction + `NodeType → enrich_intent` map so the planner walks topology it doesn't hardcode. | new `graph/expansion.py`, `runtime/planner.py`, `graph/render.py`. |

---

## C. DEDUPLICATION / SIMPLIFICATION

**Merge redundant concepts:**
- **`HypDelta` ⇄ `UpdateHypothesis`** (`operations.py:78-86`, `hypothesis.py:60-73`): near-identical shapes hand-transcribed field-by-field in `reducer.py:151-176`. Type `UpdateHypothesis.new_status: HypothesisStatus` (not bare `str` re-parsed at `reducer.py:164`); give a `from_status()` classmethod so op and delta share one mutation shape.
- **`evidence_floors` ⇄ `gate.min_facts`**: two competing "min facts" mechanisms — `incident.yaml` sets the frame floor in both places, only `gate.min_facts` is read. Delete `evidence_floors`; single source is `gate.min_facts`.
- **Confirmation logic in two places**: `controller.check_gate` (`controller.py:30-40`) re-derives a weaker confidence test inline while the principled `ledger.promotion_ok(tunables)` (the Popperian test that actually uses `delta`) is never called by the engine. Delegate to `ledger.promotion_ok`; belief logic lives once, in the ledger that owns belief. This also revives dead `delta`.
- **Action↔status derivation** duplicated: `reducer.py:161-170` maps status→action *and* passes `new_status`; `ledger.py:49-52` re-derives status from action. Pick one home.
- **Closure-check triplicated** verbatim (`registry.py:18-23`, `nodes/__init__.py`, `edges/__init__.py`) + hardcoded tier-import tuples → one `assert_closed(enum, specs, label)` helper + `pkgutil` folder scan (directly serves folder-domains).
- **Id-prefix grammar scattered** (`fact:`/`evt:`/`edge:` in registry; `hyp:`/`no_evidence:` in reducer) → centralize; gives C's hyp-fix an obvious home.

**Fix the god-function:** `reducer.materialize()` (`reducer.py:56-190`, 135 lines, 7-way `isinstance` chain, all state in closures) → `ReduceCtx` dataclass + `_HANDLERS: dict[type[Operation], Handler]` with a `produces_node` flag driving the two passes. Adding an 8th op kind (a live planner will want them) becomes a new small function, not an edit past six unrelated branches. Note the good part: adding a NodeType/EdgeType already touches nothing here — closure is at op-kind only.

**Correctness fix (mechanical):** route the 4 hand-minted `hyp:{hid}` sites (`reducer.py:79,154,174`, `git.py:102`) through `registry.node_id(NodeType.HYPOTHESIS, {"hid": hid})` so the one node type whose identity contract is currently a lie obeys the deterministic grammar. Ids change `hyp:` → `hypothesis:`; update the one adapter line + any fixture asserting the literal. Behavior preserved.

**Delete ceremony / dead knobs:** remove unread `Tunables.theta`, `max_items`, `clock_skew_bound_s` (`playbook.py:19-29`); `ledger.py:57` dead `.get(..., 1)` default that silently ranks unknown status above REFUTED; the O(N-total) filter in `render.py:19-22` (iterate `ids`, not all nodes) and the unstable `set[:max_nodes]` slice at `render.py:16`.

---

## D. WHAT TO LEAVE ALONE (already elegant — do not churn)

- **`PhaseResult` + the one fold contract** (`phase_result.py`) — the spine; every phase emits the identical envelope, each field lands in exactly one store. Best decision in the codebase. (A1 restores it, doesn't change it.)
- **Discriminated `Operation` union + reducer-as-trust-boundary** — precisely what makes dropping a live LLM in *safe*. (C splits the *dispatch*, not the contract.)
- **Journal-as-truth + `rebuild` replay-equivalence** — a real event-sourced core, correctly proven.
- **`Planner`/`Adapter` Protocols + `ScriptedPlanner` determinism twin** — two needs, one abstraction; drop-in for the live LLM.
- **Bi-temporal reified `Fact` + supersede**, **MultiDiGraph coexistence of structural+causal edges**, **types-as-data frozen-dataclass specs**, **the import DAG (no cycles)**, **atomic/partial-line-safe persistence**, **`SubjectRef.domain`** neutrality, **the `_EVIDENCE_SOURCES` comprehension**. These earn their weight.

**Over-engineered — park out of `src/` until their ask lands (don't wire speculatively):** `Journal.append_step` + step-only `JournalEntry` fields (unwired two-granularity scaffolding — but revisit when node-expansion wants per-call journaling), `Prediction.checked/held` (Popperian loop modeled, never exercised), the `RetractFact` gap (`FactState.RETRACTED` + `graph.retract_fact` exist with no op to emit — add the op or drop the state), `OcpRestartAdapter` (`ocp.py:199` raises `NotImplementedError`, not in `default_adapters()` — a stub masquerading as code; it's also the only WRITE path, so build a real one when A3 lands). Note: the "dead" spec fields (`discriminator`/`semantics`/`tier`) are **not** deletions — A5 activates them. `symmetric` is a genuine dead promise: either canonicalize `(a,b)`/`(b,a)` in the reducer or drop the field + add a comment that evidence-edge legality is vacuous (`_EVIDENCE_SOURCES` = every node type).

---

## E. SEQUENCED APPLY PLAN (tests green throughout)

Ordered so each step is independently green; risk rises left-to-right, so the risky binding-time change lands last on a settled engine.

**Phase 0 — subtractive, near-zero risk (do first, shrinks the surface):**
1. Delete dead tunables `theta`/`max_items`/`clock_skew_bound_s`; fix `ledger.py:57` default; fix `render.py:16,19-22` slice+iteration. `collapse evidence_floors → gate.min_facts` in `incident.yaml`.
2. Route the 4 `hyp:` sites through `registry.node_id`; centralize id-prefixes; update fixtures asserting the literal. Run tests (ids now `hypothesis:`).

**Phase 1 — unify the mutation path (A1):** extract `apply_delta`; point engine + `rebuild` at it. Tests green; equivalence test now covers the live path.

**Phase 2 — de-leak the engine (A2):** add `PhaseSpec.writes_allowed` + `symptom_node`/`terminal` to playbook with defaults matching today; introduce `ApprovalGate` Protocol + `AutoApprove` default. Behavior identical.

**Phase 3 — resumable stepper (A3):** hoist loop state, add `step()`, `run() = while not done: step()`. Uses Phase 2's `writes_allowed` as the pause signal and Phase 1's compute/apply split to hold the delta. 46 tests verbatim.

**Phase 4 — live-LLM guardrails (small, additive):** enforce `allowed_intents` (skip+record `blocked` invocation), enforce `max_retries` (REPEAT→BACKTRACK past ceiling), fix `op_ceiling` ordering (plan.ops first, record truncated as rejections), delegate confirmation to `ledger.promotion_ok`. Each is one guarded branch; tests unaffected (ScriptedPlanner stays in-bounds).

**Phase 5 — seams for the live world:** `Source` Protocol (`MockSource` becomes one impl); wire `playbook.capabilities`→layer filter; `emit_node` adapter util + unify dedup; `PlanContext.prior_next_actions` + `frontier`; new `graph/expansion.py`. Additive.

**Phase 6 — reducer handler registry (C god-function split):** `ReduceCtx` + `_HANDLERS`. Refactor-only; behavior identical.

**Phase 7 — Registry-as-instance (A4) + catalog (A5):** convert to `class Registry` with a default incident binding; thread through reducer/engine; add `catalog()` and `PlanContext.allowed_types`. Highest risk — lands last, on a settled engine. Only **after** this is green do you tackle the genuine architectural investment (enum→str, YAML spec loader, `Registry.from_folder`) as its own tracked change, not a "refactor."

**Highest-leverage single move if you do only one:** A3 (resumable stepper), which A1+A2 make cheap and clean — it turns three of the four depth-asks from "bolt-on" into "already there."

Key files: `/Users/innamul/Project/iw/engine/src/iw_engine/runtime/engine.py` (:88, :104-108, :116-135), `/graph/fold.py`, `/domain/registry.py`, `/domain/playbook.py` (PhaseSpec/Tunables), `/graph/reducer.py` (:56-190, hyp sites :79/154/174), `/runtime/planner.py:23-30`, `/capability/layer.py:74`, `/capability/adapters/*`.