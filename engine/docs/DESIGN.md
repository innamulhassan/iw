# Investigation Workbench — CORE DESIGN (final, build-authoritative)

> Base blueprint: [`DESIGN-INPUT-v1.md`](DESIGN-INPUT-v1.md) (research synthesis §A–I + completeness critique P0–P2).
> This doc = the **final rulings** that resolve every open question + critic gap, the **module layout**, and the **build order**. Where this doc and v1 disagree, **this doc wins**.

## 0. North star

A small **re-enterable phase state machine**. Each phase does `goal → plan → typed capability calls → ONE uniform PhaseResult`. A single deterministic engine **folds** each PhaseResult into an append-only **journal** (source of truth) that projects to a **typed bi-temporal graph** (blackboard) and a **hypothesis ledger** (ranked, evidence-backed causal chains on a timeline). The LLM emits only grammar-constrained **typed operations** against a **closed node/edge registry** — it classifies into the catalog, never invents.

**The 3 named failures → the fixes that retire them:**
1. *Over-detailed/untuned playbook* → **three authors** (playbook=WHAT/WHEN config · engine=MECHANICS · LLM=JUDGMENT); every tuning knob in one `tunables:` block; **no tuning constant in engine code.**
2. *Wrong incident graph model* → **closed typed tiered registry** + strict property/fact/event split + bi-temporal facts + causation-separate-from-structure.
3. *Inconsistent per-phase outputs* → **ONE `PhaseResult` contract + one `fold()`**.

## 1. Adopted principles

All 12 principles of v1 §A are adopted verbatim. The load-bearing ones: **one contract/one fold** · **journal is truth, graph+ledger are projections** · **three storage shapes (property/fact/event)** · **closed vocabulary + one escape hatch** · **constrain the action space not the thought** · **prose is a field not the medium** · **bi-temporal (valid-time + observed-time)** · **causation edges are a separate, refutable layer** · **hypotheses carry both supporting AND refuting evidence** · **determinism is a test seam (Protocols + scripted/mock twins; pure fold).**

---

## 2. FINAL RULINGS (resolve every critic gap + open question)

### 2.1 Graph domain model

- **R-G1 (P0-1 — registry is truly closed).** Every type a `normalize()` can emit is in the registry. Add nodes `ErrorSignature`, `BusinessTransaction`, `PullRequest`; add edges `EMITTED` (entity→signal), `INTRODUCED_BY` (ChangeEvent/Release→CodeCommit), `DEPLOYED_AS` (Release/BuildArtifact→running Deployment/Pod). Drop `Workload` (map to `Deployment`/`Pod`). A **CI test asserts** `⋃ types any adapter.normalize() emits ⊆ registry` — the guardrail that keeps the registry closed as capabilities grow.
- **R-G2 (P1-1 — dead types).** **Drop** `RootCause` and `Remediation` node types: the root cause **is** a `Hypothesis{status=confirmed}`; remediation is a `Remediation` **record** on the ledger + a `REMEDIATED_BY` edge (Hypothesis→ChangeEvent/action), not a node. **`Anomaly` gets a job** → it is the symptom node (R-G4).
- **R-G3 (P1-2 — L0 overlap).** Keep `Application` (business grouping that OWNS services), `Service` (independently deployable unit — has RED, a Deployment, endpoints), `Component` (internal module, no independent deploy). **Drop `Microservice`** (it is a `Service`). Registry carries a machine-readable **discriminator rule** per type so the LLM can choose.
- **R-G4 (P1-7 — symptom node).** FRAME's canonical output is an **`Anomaly`** node carrying the onset fact + `AFFECTS`→(Service|ApiEndpoint). The whole method operates over the subgraph reachable from the Anomaly.
- **R-G5 (P1-6 — static vs point-in-time).** **Boundary test:** a static property is **identity/immutable only** (name, uid, engine kind, repo). **Any time-varying incident-relevant attribute** (`tier`, `slo_target`, `node_name`, running `image`, replica count, pool size) is a **Fact with a valid-time window**, never a node prop. This is what makes "reconstruct the graph as of incident-start" true.
- **R-G6 (P1-9 — missing real-incident types).** Add first-class nodes `Certificate` (expiry events), `FeatureFlag` (flip events), `ExternalService` (third-party/SaaS). These are change-adjacent-but-not-in-CMDB and power no-change incidents.
- **R-G7 (P2-4 — chain conditions).** A `causal_chain` element is a typed `ChainLink{kind: event|fact|change, ref, ts, role: cause|condition|effect}` — so latent states ("pool already at 80% when the deploy landed") enter the chain as Fact links; no new node type.
- **R-G8 (structural).** Runtime graph is a **`MultiDiGraph` with edge ids** (a structural `DEPENDS_ON` and an inferred `CAUSED_BY` between the same pair coexist). Every edge declares `origin ∈ {declared, discovered, inferred}`.

### 2.2 Phase model + the uniform contract

- **R-P1 (the ONE contract).** Every phase emits identical `PhaseResult{phase_id, goal_restated, facts_added[], nodes_touched[], edges_added[], hypotheses_updated[], narrative, next_actions[], phase_verdict{status∈advance|repeat|backtrack|blocked|done, confidence, basis, gate_result}}`. One `fold(PhaseResult)`; each field folds into exactly one store.
- **R-P2 (P0-4 — no fabrication).** A typed **`NoEvidence` fact** (predicate `looked_and_clean{intent, scope, basis}`) satisfies `produces_required` when a phase honestly found nothing — and is itself RCA-meaningful (it **refutes** hypotheses: "we checked the DB, it was clean"). This directly serves the owner's "want less, not more."
- **R-P3 (P1-3 — mitigate early).** TRIAGE gains a gated `apply_mitigation` intent + an `on_verdict` route to REMEDIATE-and-return, so mitigation can precede root-causing (principle 12 is now reachable).
- **R-P4 (P1-4 — mitigated close).** CLOSE has two outcomes: `resolved` (confirmed hypothesis + verified fix) and `mitigated` (impact stopped, best surviving hypotheses + mitigation timeline, no confirmed root cause). The postmortem renders for both.
- **R-P5 (phases).** FRAME → TRIAGE → HYPOTHESIZE → INVESTIGATE → REMEDIATE → VERIFY → CLOSE, re-enterable (VERIFY-refute routes back to HYPOTHESIZE at zero plumbing cost).

### 2.3 Control, planning, tuning

- **R-C1 (P1-10 — one routing authority).** The **controller (engine)** decides WHICH phase runs next, from `phase_verdict` + board state — authoritative. `next_actions` is **advisory**: it seeds the *plan* within the next phase.
- **R-C2 (I.1/I.6/P2-7 — bounded, no gleaning).** Ops are **batch-emitted per phase** (`apply_operations(ops[])`), a **single capped pass** (no gleaning continuation), with per-step `maxItems` **and** a per-phase op ceiling. "Nothing left" = the R-P2 `NoEvidence` sentinel.
- **R-C3 (Failure-1 residual — one tunables home).** The playbook carries a single **`tunables:`** block enumerating **every** knob: `confidence_gate`, `evidence_floors` per phase, `maxItems` per phase, `op_ceiling` per phase, `max_retries`, `theta`/`delta` (promotion margin), `source_reliability` per source, confidence enum→band mapping, `clock_skew_bound` per source. **The engine owns only arithmetic — zero tuning constants in engine code.**
- **R-C4 (I.4/I.5/P2-1 — confidence discipline).** LLM emits a **rubric enum `{LOW,MED,HIGH}`** at op time (kills "0.9 everywhere"); the ledger maps enum→numeric band **with mandatory `basis`**. **Directly-measured** facts carry **`source_reliability`**, not a belief score. Evidence weight = `source_reliability × temporal_proximity × topological_specificity`; promotion when `score>theta` **and** `margin>delta` **and** no unrefuted competitor (all playbook-tunable).

### 2.4 Journal / ledger / persistence

- **R-J1 (P0-3 — journal can rebuild everything).** A phase-level `JournalEntry` stores the **full PhaseResult delta** as its event payload; `refs` is a **derived index**. Replaying the NDJSON journal fully reconstructs graph + ledger. Two granularities, one schema: fine ReAct step + coarse phase entry.
- **R-J2 (P1-5 — clock skew).** Each source carries `clock_skew_bound` (tunable); temporal correlations (change→symptom onset) **never assert ordering tighter than the combined skew bound** — the join uses a tolerance window.
- **R-J3 (P2-2 — retraction).** Fact state includes `retracted`/`invalidated` (a tombstone for *wrong* telemetry) — distinct from `supersedes` (newer truth).
- **R-J4 (P2-3 — crash safety + versioning).** Graph cache: **write-temp-then-rename**. Journal load: **skip a trailing partial line**. Every artifact stamps **`schema_version`**; on load the journal is authoritative and the cache is rebuilt if it disagrees.
- **R-J5 (I.7 — identity resolution).** The **reducer** canonicalizes by `identity_key` + a small alias table (`Service` by `name+env`; `Artifact`/`Release` by image digest; `Commit` by sha; cross-tool joins by `trace_id`, `file:line`, CI `sys_id`, time window).

### 2.5 Capabilities + scenarios + reducer

- **R-K1 (adapter contract).** Every capability is a pure pair: `query(intent, params) -> RawJSON` (the **only** side-effecting boundary; swap for a fixture loader to mock) + `normalize(RawJSON) -> Operation[]` (pure, deterministic, idempotent by `identity_key`). Model all **8 tools as 8 distinct Providers**.
- **R-K2 (P1-8 — reducer batch semantics).** Batch apply is **partial-accept**: reject only the offending op (journal the rejection into a bounded repair loop), apply in **topological order** within a batch (nodes before their facts/edges). Reducer re-validates: registry membership, edge legality as `(src_type, edge_type, dst_type)` allow-list, referential integrity, unit coercion, numeric bounds (`confidence∈[0,1]`), dedup on `identity_key`.
- **R-K3 (P0-2 — the no-change class).** Six scenarios (one per layer + one no-change): **(1) app code regression, (2) bad deployment, (3) network, (4) database, (5) firewall, (6) NO-CHANGE** (organic traffic surge → connection-pool exhaustion, empty change set). HYPOTHESIZE **fallback** when `find_recent_changes` is empty: seed `root_candidate`s from **USE-saturation / onset-correlated Anomalies**, not ChangeEvents.
- **R-K4 (E2E matrix).** 6 scenarios × {happy, refuted-hypothesis variant} = **12 hermetic e2e tests** (≥10). Plus per-adapter `normalize()` unit tests, reducer/validation tests, join-key fusion tests. The refuted variants assert the correct `EVIDENCE_AGAINST` — testing the *reasoning*, not just the answer.

### 2.6 Deferred (out of CORE scope — flagged, not built)

- Multi-user / "the pen" / sessions (owner: skip).  · Earned-autonomy per-phase×type×severity (I.8).  · Live real capability integrations (real ServiceNow/Splunk/… APIs) — CORE is mock-first; a live **LLM planner** (Grok/Gemini) is wired only after the scripted/mock core is green.

---

## 3. Module layout (`/Users/innamul/Project/iw/engine/`)

```
src/iw_engine/
  domain/                      # DATA LAYER — pure types, zero I/O, the registry
    nodes/  <one file per NodeType>   # service, application, component, api_endpoint,
            deployment, replicaset, pod, container, process, batch_job,
            namespace, cluster, host, config_item,
            database, schema, message_queue, cache,
            load_balancer, route, network_segment, firewall_rule, dns,
            code_commit, build_artifact, release, change_event, pull_request,
            certificate, feature_flag, external_service,
            alert, incident, anomaly, error_signature, business_transaction,
            hypothesis, generic_ci
    edges/  <one file per EdgeType>   # structural spine + signal/causal layer
    registry.py                # NodeType/EdgeType enums, tagged unions, edge-legality allow-list, discriminator rules
    fact.py  event.py  common.py  subject.py   # Fact(bi-temporal, retractable), Event, Confidence{value,basis}, SubjectRef
    operations.py              # AddNode/AddFact/AddEdge/ProposeHypothesis/UpdateHypothesis (+ NoEvidence) — LLM emission union
    phase_result.py            # the ONE PhaseResult envelope
    playbook.py                # tuned playbook schema (phases + allowed_intents + gates + produces_required + tunables)
  graph/                       # APP LAYER — graph projection
    graph.py                   # in-memory MultiDiGraph, bi-temporal, idempotent upsert
    reducer.py                 # host-side validate + partial-accept + identity resolution + repair
    fold.py                    # fold(PhaseResult) -> graph ; per-provider capability fold
    render.py                  # bounded render-slice for the LLM
    persistence.py             # file-backed load/save (write-temp-rename, schema_version)
  journal/journal.py           # append-only NDJSON, full-delta entries, replay, fold-to-postmortem
  ledger/ledger.py             # ranked hypotheses, evidence weight, rerank, status transitions
  runtime/                     # APP LAYER — orchestration
    engine.py  phase.py  controller.py  planner.py  loader.py   # LangGraph spine + Protocol planner + scripted twin
  capability/                  # governed capability access
    layer.py  govern.py  resolver.py  registry.py
    adapters/  servicenow.py splunk.py appd.py prometheus.py cmdb.py ocp.py artifactory.py git.py
  playbooks/incident.yaml      # the tuned incident playbook (declarative, one page, with tunables:)
data/  graph/ journal/ fixtures/<scenario>/   # file-backed state + per-scenario capability mocks
tests/ unit/ e2e/
docs/  DESIGN.md  DESIGN-INPUT-v1.md  REQUIREMENTS.md
```
React FE lives in `iw/workbench/` and reads the three projections over a thin local read API.

---

## 4. Build order (each layer lands green before the next)

1. **Domain layer** — registry + node/edge classes + Fact/Event/Confidence + Operations + PhaseResult + playbook schema. Unit tests: schema validity, discriminator rules, edge-legality allow-list, registry-closure test.
2. **Projection engine** — graph (MultiDiGraph bi-temporal) + reducer (validate/partial-accept/identity) + fold + persistence (crash-safe) + journal (full-delta replay) + ledger (evidence weight/rerank). Unit + property tests; **replay-equivalence** test (journal → rebuilt graph == live graph).
3. **Capability layer** — 8 adapters (`query` fixture-backed + pure `normalize`) + per-scenario fixtures. *(Parallelizable once §1–2 contracts are frozen.)* Unit: `normalize()` golden tests + closure test.
4. **Engine orchestration** — planner Protocol + ScriptedPlanner + phase runner + controller (LangGraph conditional edges on `phase_verdict`) + playbook loader. Unit tests per phase (feed graph+goal, assert PhaseResult).
5. **E2E scenarios** — 6 scenarios × {happy, refuted} = 12 hermetic tests. *(Parallelizable.)* Iterate to green.
6. **Read API + React FE** — GraphView · JournalTimeline · HypothesisLedger · PhaseController.
7. **Reorg** — move superseded generations (root `src/lunasre` + old root docs, `demo/`, old `implementation/`, `frontend/`) into `legacy/`; relabel repo front door.
8. **(post-core)** live LLM planner (Grok/Gemini) behind the same Planner Protocol — the mock/scripted core is unchanged.

**Definition of done (mission accomplished):** the 12 e2e scenarios pass hermetically (scripted planner + mock capabilities, zero credentials/network); `ruff` + type-check clean; the React FE renders a real scenario's graph + journal + ledger + phase/gate; the repo is reorganized (superseded → `legacy/`); README front door describes the Investigation Workbench.
