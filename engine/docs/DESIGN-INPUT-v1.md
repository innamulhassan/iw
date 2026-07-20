# Investigation Workbench CORE — Design Input v1 (research synthesis + completeness critique)

> Auto-generated from Phase-1 research workflow (9 agents: 3 audit + 4 research + synthesis + critic). The base blueprint; final rulings that resolve the critic's gaps live in DESIGN.md.

---

## PART 1 — SYNTHESIS (lead-architect blueprint)

# Investigation Workbench CORE — DESIGN-INPUT (v1)

*Re-founding blueprint. Reconciles three read-only audits (what exists + why it failed) with four research briefs (the right design). Decisive by construction — this is the input the requirements+design phase formalizes. Where a brief left a fork, §I records it; everything else is a ruling, not a suggestion.*

**The whole system in one sentence:** a small re-enterable state machine of phases, each doing `goal → plan → typed capability calls → ONE uniform PhaseResult`, folded by a single deterministic engine into an append-only **journal** (source of truth) that projects to a **typed bi-temporal graph** (blackboard) and a **hypothesis ledger** (ranked, evidence-backed causal chains on a timeline) — with the LLM emitting only grammar-constrained typed triples against a **closed node/edge registry** it can pick from but never invent.

**The three named failures and their single root cause.** All three trace to *too many integration seams*. Fix the seams and all three collapse together:
1. *Over-detailed / untuned playbook* → detail lived in the wrong file (output schemas in Python, tuning constants in the planner). **Fix:** three authors, each line in exactly one home (§C).
2. *Wrong incident graph model* → one catch-all `system` node, no events, no hypotheses-as-nodes, `props`/`facts` blurred, weak time. **Fix:** typed tiered registry + property/fact/event split + bi-temporal (§B, §D).
3. *Inconsistent per-phase outputs* → four unrelated Pydantic shapes, no envelope, graph and output as two disconnected memories. **Fix:** ONE `PhaseResult` contract, one `fold()` (§C, §F).

---

## A. DESIGN PRINCIPLES — the north star

1. **One contract, one fold.** Every phase emits the identical `PhaseResult`; the engine folds it with a single `fold(PhaseResult)` function. Divergent phase outputs are the root of non-composability — this is the single most important rule. *(fixes failure 3)*
2. **The journal is the source of truth; graph and ledger are projections.** Append-only. Replay, fork-at-any-point, and complete lineage come free. Nothing exists in a projection without a journal `seq` back-reference. A phase returns a *delta*; it never writes a store — mutation is the engine's monopoly.
3. **Three storage shapes for three timescales.** Static **property** (what a thing *is*, on the node) · observed **fact** (what was true of it over a window — reified, sourced, time-boxed, never mutated) · **event** (what happened at an instant — immutable, append-only). Never stamp a mutable `status` on a node; that destroys the incident timeline. *(fixes failure 2)*
4. **Closed vocabulary + one escape hatch.** `NodeType`/`EdgeType`/`predicate`/`event_type` are enums exposed to the LLM as a grammar-constrained tagged union; unknowns route to `GenericCI{class_hint}`, never a minted label. The LLM *classifies into* the catalog, it does not author it. *(fixes failure 2)*
5. **Three authors, no overlap.** Playbook = WHAT/WHEN (declarative config) · Engine = invariant MECHANICS (deterministic) · LLM = JUDGMENT (generative). Every line lives in exactly one. An over-detailed playbook is leaked mechanics or leaked reasoning dressed as config. *(fixes failure 1)*
6. **Constrain the action space, not the thought.** The playbook whitelists `allowed_intents` per phase; the LLM chooses freely within them. Scope, don't script. RCA methods (5-whys, fault-tree, ECFC, change-analysis) are lenses that *emerge* from intent choice over one substrate — never encoded as playbook prose.
7. **Prose is a field, not the medium.** Natural language lives only in `narrative`/journal (and short capped `basis` fields). Everything else is typed. This is what makes output read naturally without being unstructured, and stops the engine over-chunking to stitch prose.
8. **Bi-temporal by default; time is the RCA join key.** Valid-time `[valid_from, valid_to)` for truth, `observed_at` for audit. This is what makes "reconstruct the graph as it was at incident-start" and "what were we blind to?" answerable, and makes RCA a time-windowed join over the symptomatic subgraph.
9. **Keep causation strictly separate from structure.** `DEPENDS_ON`/`RUNS_ON`/`CALLS` are the durable spine; `CAUSED_BY`/`CORRELATED_WITH` are per-investigation, confidence-scored, refutable edges that never mutate the spine. Dependency ≠ causation — the old model's core defect.
10. **Hypotheses carry both sides of the evidence.** Supporting *and* refuting facts; confidence *with* mandatory `basis`. Belief moves only on cited facts with journal refs, never a bare number. Confirmation is Popperian: cross the gate **and** survive a refutation attempt; the verified fix is the strongest confirmation.
11. **Determinism is a test seam, not an aspiration.** LLM planner and capability adapters sit behind `Protocol` interfaces with scripted/mock twins; the reducer/fold is a pure function. Exact-match graph assertions on mocked runs; invariant-only assertions on live runs.
12. **Mitigate before you understand.** Stopping user impact is a distinct, side-effect-gated action that may run before or alongside root-causing — never blocked on it.

---

## B. GRAPH DOMAIN MODEL

### B.1 The core conceptual split (everything hangs off this)

| Concept | Answers | Mutability | Time shape | Lives as | Carries |
|---|---|---|---|---|---|
| **Static property** | *What is this thing?* | slowly-changing, corrected in place | none | field on node | value only |
| **Observed fact** | *What was true of it, when, how do we know?* | **never mutated — superseded** | window `[valid_from, valid_to)` | reified statement pointing at node/edge | value + source + confidence + evidence + observed_at |
| **Event** | *What happened, at what instant?* | **immutable, append-only** | point `occurred_at` | inline on entity, or promoted to node | type + payload + source + observed_at |

The number-one modeling failure the audit found (`status: degraded` stamped as a node prop) is exactly this collapse. Three distinct storage shapes, enforced.

### B.2 Node-type catalog (closed `NodeType` enum, tiered)

Tiering is a design principle: edges mostly cross *adjacent* tiers → bounded traversals, legible graph. Incident is the first (and, for CORE, only-populated) domain, but the taxonomy is registry-driven so a second domain is a new registry file, not an engine change.

**Tier L0 — Logical/business:** `Application`, `Service`, `Microservice`, `Component`, `ApiEndpoint`, `Team`
**Tier L1 — Workload/runtime:** `Deployment`, `ReplicaSet`, `Pod`, `Container`, `Process`, `BatchJob`
**Tier L2 — Platform/orchestration:** `Namespace`, `Cluster`, `Host`, `ConfigItem`
**Tier L3 — Data & messaging:** `Database`, `Schema`, `MessageQueue`, `Cache`
**Tier L4 — Network/edge:** `LoadBalancer`, `Route`, `NetworkSegment`, `FirewallRule`, `Dns`
**Tier L5 — Change & supply chain:** `CodeCommit`, `BuildArtifact`, `Release`, `ChangeEvent`
**Tier L6 — Signals/investigation:** `Alert`, `Incident`, `Anomaly`, `Hypothesis`, `RootCause`, `Remediation`
**Escape hatch:** `GenericCI{class_hint}` (mirrors ServiceNow base `cmdb_ci`).

Representative rows (type · key static props · typical facts/events):

| Type | Key static props (identity_key **bold**) | Typical facts (window) | Typical events (point) |
|---|---|---|---|
| `Service` | **service.name + env**, tier, slo_target | RED (rate/errors/duration p50/p95/p99), degraded-window | alert-fired, deployed, scaled |
| `ApiEndpoint` | **service + method + route_template** | per-endpoint RED, status-code dist | 5xx-spike, timeout-burst |
| `Pod` | **uid** (fallback ns+name), node_name, qos_class | phase(Running/CrashLoop) window, ready | scheduled, OOMKilled, evicted, restarted |
| `Host` | **fqdn/asset_id**, cpu_cores, mem_gb, region | USE (cpu/mem/disk/net util+saturation) | reboot, disk-fail, NotReady |
| `Database` | **db_id**, engine, ha_role, endpoint | conn-pool-util, replication-lag, slow-query-rate | failover, connection-storm, deadlock-spike |
| `MessageQueue` | **topic_id**, broker, partitions | consumer-lag, DLQ-depth, throughput | rebalance, partition-offline |
| `FirewallRule` | **rule_id**, direction, proto, port_range, src, dst | deny-count | rule-changed, deny-spike |
| `Deployment` | **uid**, name, namespace, image, strategy | available-replicas, rollout-progress | rollout-started/complete, rollback, image-change |
| `ChangeEvent` | **change_id**, type(deploy/config/infra/db-migration), target_ref, actor, ticket_id | — (essentially a first-class event) | (the node *is* the occurrence) |
| `CodeCommit` | **sha**, repo, author, parent_sha, authored_at | — | (historical point) |
| `Alert` | **alert_id**, rule, severity, metric, threshold | — | fired, ack, resolved, re-fired, flapping |
| `Incident` | **incident_id**, severity(SEV1-4), status, commander, declared_at | — | declared, mitigated, resolved |
| `Hypothesis` | **id**, statement, status, root_candidate | confidence-window (reranks) | created, confirmed, refuted, superseded |

**RED vs USE binding is registry-enforced:** RED predicates attach only to `Service`/`ApiEndpoint`/`CALLS`; USE predicates only to `Host`/`Container`/`Database`/`LoadBalancer`. "Service slow (RED) → because host saturated (USE)" becomes a *typed two-hop traversal*, not a free-text correlation.

### B.3 Edge catalog (closed `EdgeType` enum)

Convention `(from) -[EDGE]-> (to)`; each edge declares an **origin** — `declared` (CMDB/IaC — truth) · `discovered` (telemetry — observation, time-boxed + RED facts) · `inferred` (LLM/causal — hypothesis, confidence+evidence mandatory).

**Structural spine:** `DEPENDS_ON` (dependent→provider; impact=forward, RCA=backward), `CALLS` (discovered, RED-carrying), `REALIZES`/`INSTANCE_OF` (Pod→Deployment), `RUNS_ON`/`HOSTED_ON` (fate-sharing spine), `DEPLOYED_TO`, `CONTAINS`/`MEMBER_OF`, `EXPOSES`, `ROUTES_TO`, `CONNECTS_TO`, `READS_FROM`/`WRITES_TO`, `PRODUCES_TO`/`CONSUMES_FROM`, `SECURED_BY`.
**Ownership/supply-chain:** `OWNS`, `BUILT_FROM` (Artifact→Commit), `RELEASED_AS`, `RUNS_VERSION`.
**Signal/causal (separate layer):** `FIRED_ON`/`AFFECTS`, `TRIGGERED_BY`, `IMPACTS`, `CHANGED_BY` (the RCA workhorse — joins changes to the incident window), `CORRELATED_WITH` (symmetric, weaker-than-causal, carries correlation strength), `CAUSED_BY` (effect→cause, **always** confidence + evidence + `hypothesis_status`), plus `SUPPORTS`/`REFUTES` (Fact→Hypothesis), `EVIDENCE_FOR`/`EVIDENCE_AGAINST`, `REMEDIATED_BY`.

**Directionality discipline:** dependency edges point dependent→provider; causal edges point effect→cause. Two rules keep every query clean.

**Structural ruling (fixes audit finding f):** the runtime graph is a **`MultiDiGraph` with edge ids**, not `DiGraph` — so a structural `DEPENDS_ON` and an inferred `CAUSED_BY` between the same node pair coexist instead of overwriting.

### B.4 Property/Fact/Event/time model — the reified shapes

```
Fact {
  subject_ref: NodeId | EdgeId          # what it's about (facts attach to edges too)
  predicate:   enum                     # registry-controlled, typed per node type
  value:       typed(bool|number+unit|enum|struct)
  valid_from:  datetime                 # real-world truth window opens…
  valid_to:    datetime | null          # null = still true (open interval)
  observed_at: datetime                 # transaction time — when we learned it
  source:      enum(prometheus|splunk|appd|servicenow|cmdb|ocp|artifactory|git|llm|human)
  confidence:  Confidence{value:0..1, basis:str}   # never a bare number
  evidence:    [Ref]                    # metric query / trace id / log link / snapshot id
  supersedes:  FactId | null            # never mutate — supersede + close valid_to
  created_by:  seq                      # journal lineage
}

Event {
  entity_ref:  NodeId
  type:        enum                     # per node type: OOMKilled, rollout_complete, config_changed, failover…
  occurred_at: datetime                 # when it happened in the world
  observed_at: datetime                 # when recorded
  payload:     struct                   # exit_code, old→new image, actor, ticket_id
  source:      enum
  created_by:  seq
}
```

**Rulings that fix the audit's temporal defects (finding d):** `valid_from`/`observed_at`/`occurred_at` are typed `datetime`, **not** `Optional[str]`; the lexicographic recency compare is deleted; the fixtures' mixed formats (`"14:21"` vs full ISO) are rejected at validation. Facts never mutate — new information supersedes. An occurrence becomes a *node* (`ChangeEvent`, `Alert`) when other things need to point at it; otherwise it stays an inline `Event`.

### B.5 How it's exposed as typed classes (the domain package + the registry the LLM sees)

**One source of truth, two derivations.** A Pydantic v2 **tagged union** keyed on a `Literal` discriminator (`type`/`op`) is the registry; both the JSON schema handed to the grammar-constrained decoder *and* the "allowed types" section of the system prompt are *derived from the same object* so they cannot drift (kills the audit's `svc`/`service`/`microservice` proliferation and Brief-6 pitfall E10).

```python
# domain/nodes/pod.py
class PodNode(BaseModel):
    type: Literal[NodeType.POD]
    uid: str                              # identity_key → idempotent upsert
    namespace: str; name: str; node_name: str | None = None
    qos_class: Literal["Guaranteed","Burstable","BestEffort"] | None = None

Node = Annotated[Union[ServiceNode, PodNode, HostNode, ...], Field(discriminator="type")]
```

The LLM never emits graph structure as free text. Its only output channel is a bounded list of typed **operations** (the capability surface of §F), each a branch of the union:

```python
Operation = Annotated[Union[AddNode, AddFact, AddEdge, ProposeHypothesis, UpdateHypothesis],
                      Field(discriminator="op")]
class PhasePlan(BaseModel):
    steps: list[PlanStep] = Field(max_length=5)     # bounded — over-production is ungrammatical
```

Registry entry the LLM classifies against (machine-readable, one per `NodeType`/`EdgeType`):

```yaml
NodeType: Pod
  identity_key: [uid]
  fallback_keys: [namespace, name]
  static_props: {name: string, namespace: string, node_name: string, qos_class: enum[...]}
  fact_predicates: {phase: enum[Pending,Running,CrashLoopBackOff,Failed], ready: bool, cpu_utilization: number(0-1)}
  event_types: [scheduled, started, OOMKilled, evicted, restarted, terminated]
  allowed_edges: {RUNS_ON: {to: Host, card: n:1}, REALIZES: {to: ReplicaSet, card: n:1}}
```

**Two-layer enforcement (Brief-6 defense in depth):** the grammar-constrained decoder makes off-catalog `type` tokens unsamplable (layer 1); the host-side **reducer** re-validates against the registry — edge legality as a `(source_type, edge_type, target_type)` tuple allow-list, referential integrity, unit coercion, numeric bounds (`confidence ∈ [0,1]`), dedup on `identity_key` — because the grammar cannot express those (layer 2). Rejected ops return a structured error into a **bounded repair loop**, never silently dropped. Confidence in ops is a *rubric enum or a `{value, basis}` pair with mandatory basis*, never a naked float (kills "0.9 everywhere").

---

## C. PLAYBOOK + PHASE MODEL

### C.1 Phases — a re-enterable state machine, not a pipeline

The controller picks the next phase from graph+ledger state (blackboard control). Mirrors the SRE lifecycle with the invariant that **mitigation is distinct and may precede root-causing**.

| # | Phase | Goal | Prime intents | Exit gate |
|---|---|---|---|---|
| 0 | **FRAME** | normalize signal; fix scope/severity/onset + the question | ingest_alert, query_change_log, seed_graph | symptom node + timeline anchor + candidate changes exist |
| 1 | **TRIAGE** | still bleeding? mitigate-now vs investigate; narrow suspects | assess_impact, list_dependencies | severity set; suspect set ≤ k |
| 2 | **HYPOTHESIZE** | generate causal explanations, change-first | enumerate_changes, propose_hypotheses | ≥1 hypothesis with a causal chain |
| 3 | **INVESTIGATE** | confirm/refute leading hypothesis with evidence | fetch_logs/metrics/traces, read_diff, correlate_timeline | top conf ≥ gate **AND** a refutation attempt failed |
| 4 | **REMEDIATE** | propose/enact fix + mitigation (side-effects human-gated) | propose_fix, apply_mitigation* | fix proposed/approved |
| 5 | **VERIFY** | symptom cleared? claim still holds? | recheck_symptom, confirm_chain | symptom cleared **or** hypothesis refuted → backtrack |
| 6 | **CLOSE** | postmortem = confirmed chain + timeline, folded from journal | render_postmortem | — |

`*` side-effectful → explicit human approval. Re-enterability is free because every phase speaks the same contract: VERIFY refuting a "confirmed" hypothesis routes back to HYPOTHESIZE at zero engine cost.

Each phase is exactly `goal + PLAN (LLM's ordered choice of allowed intents) + capability calls + one PhaseResult`.

### C.2 The ONE uniform phase-output contract

Every phase — FRAME through VERIFY — emits the **identical shape**. This is the one seam.

```
PhaseResult {
  phase_id            # frame | triage | hypothesize | investigate | remediate | verify | close
  goal_restated       # one line — the goal this phase pursued
  facts_added:        [Fact]      # → GRAPH   ; each carries source + evidence + node binding
  nodes_touched:      [NodeRef]   # → GRAPH   ; created/updated
  edges_added:        [Edge]      # → GRAPH   ; depends_on|correlates_with|caused_by|supports|refutes
  hypotheses_updated: [HypDelta]  # → LEDGER  ; created|evidence_attached|reranked|confirmed|refuted
  narrative:          string      # → JOURNAL ; the ONLY prose field, becomes the entry body
  next_actions:       [Intent]    # → CONTROLLER
  phase_verdict: {                # → CONTROLLER
    status:     advance | repeat | backtrack | blocked | done
    confidence: 0..1
    basis:      string            # why this verdict — cited, not asserted
    gate_result: pass | fail
  }
}
```

**Each field folds into exactly one store, uniformly, for every phase.** The engine has a single `fold(PhaseResult)`. Adding/reordering a phase or a whole new playbook requires **no new plumbing**. The three stores are pure projections of the `PhaseResult` stream → replay, fork, lineage for free. Each phase is unit-testable in isolation (feed graph+goal, assert on returned `PhaseResult`, no store mocking, because a phase touches no store). The LLM learns ONE schema; prose has exactly one home.

This directly retires the old model's four unrelated shapes (`AssessResult` 11 fields / `RootCauseResult` 4 / `RemediationResult` 3 / `VerifyResult` 9) and the schema-coupled routers that reached into `candidates[].confidence.value` and `recovered` — those promotions now live on the envelope (`phase_verdict.confidence`, ledger status).

### C.3 How a phase composes into graph + journal + hypothesis

`facts_added`+`nodes_touched`+`edges_added` → graph fold. `hypotheses_updated` → ledger apply. `narrative` + the whole delta → one appended journal entry. `next_actions`+`phase_verdict` → controller picks the next phase / seeds the next plan. **The phase returns a delta; the engine owns all mutation.** That monopoly is what makes it deterministic, replayable, composable.

### C.4 What lives where — three authors, each line in one home (fixes failure 1)

| Concern | Owner | Nature | Examples |
|---|---|---|---|
| **WHAT / WHEN** | **Playbook** | declarative config | phases, goals, allowed_intents, gates, transitions, evidence floors, confidence gate |
| **Invariant MECHANICS** | **Engine** | deterministic | the fold, journal append, graph/ledger projection, gate evaluation, retries/timeouts, human-gating, the controller loop, hypothesis rerank bookkeeping |
| **JUDGMENT** | **LLM** | generative | the plan, capability choice, observation interpretation, hypothesis proposals, confidence + basis, the narrative |

**Litmus:** *Same for every incident of this class?* → engine. *In-the-moment reasoning?* → LLM. *Class-specific WHAT/WHEN?* → playbook.

The tuned playbook fits on a page and is *declarative config for one incident class, not a procedure*:

```yaml
playbook: web-tier-5xx-spike
applies_to: <incident class>
capabilities: [servicenow, splunk, appd, prometheus, cmdb, ocp, artifactory, git]
confidence_gate: 0.8
stop_conditions: [max_phase_reentries, wall_clock, human_halt]
phases:
  - id: investigate
    goal: "Confirm or refute the leading hypothesis with evidence."
    allowed_intents: [fetch_logs, fetch_metrics, fetch_traces, read_diff, query_change_log, correlate_timeline]
    gate_exit: "top_hypothesis.confidence >= confidence_gate AND refutation_attempted"
    produces_required: [facts_added, hypotheses_updated]   # which PhaseResult fields must be non-empty
    output_type: PhaseResult                               # ALWAYS PhaseResult
    on_verdict: {advance: remediate, repeat: investigate, backtrack: hypothesize, blocked: triage}
```

**Rulings that fix the audit's misplaced detail:** evidence floors and intent ordering move OUT of `llm_planner.py` (the hardcoded `_floor={assess:3,...}` and "incident-source first, topology next") and INTO the playbook as declarative data. Output schemas move OUT of a 167-line `outputs.py` — there is now ONE `PhaseResult`; per-phase specificity is expressed by `produces_required`, not by a bespoke Pydantic class. The weak `sufficient()` stop-test (bare schema validity) is replaced by the typed `gate_exit` predicate over `produces_required` + ledger confidence.

---

## D. JOURNAL + HYPOTHESIS / EVENT-SEQUENCE MODEL

### D.1 Journal — append-only, human-readable, the source of truth

Two granularities, one schema: fine-grained ReAct step (thought→action→observation→decision) and the coarse phase-boundary entry (the `PhaseResult` narrative + verdict).

```
JournalEntry {
  seq          # monotonic append-only — the event-sourcing spine
  ts           # wall clock
  phase_id
  actor        # which model / agent / human (who)
  intent       # what was attempted (∈ phase.allowed_intents)
  reasoning    # WHY — short natural language
  action       # {capability, args}
  observation  # {summary, evidence_ref, raw_ptr}
  decision     # WHAT it means / next
  refs         # {nodes:[...], hypotheses:[...], facts:[...]} — links into graph + ledger
}
```

Renders as a running story. Every fact/hypothesis in a projection carries a `created_by`/`refs` pointer back to the `seq` that produced it → complete lineage, no orphan claims. **The postmortem is generated by folding the journal**, never authored separately, so it cannot drift from what happened.

### D.2 Hypothesis ledger — ranked causal chains on a shared timeline

```
Hypothesis {
  id
  statement                            # "The 13:47 config push dropped the origin host header, causing 5xx."
  causal_chain: [Event]                # ordered cause→effect; each Event is a timestamped graph node
  root_candidate: NodeRef              # chain head — the proposed initiating change/fault
  status                               # proposed|investigating|supported|confirmed|refuted|superseded
  confidence: {value:0..1, basis}      # basis MANDATORY: which facts, which method, how strong
  supporting_facts: [FactRef]          # SUPPORTS edges — facts that raise belief
  refuting_facts:   [FactRef]          # REFUTES edges — REQUIRED, anti-confirmation-bias
  predictions: [Prediction]            # "if true we'd also see X" — drives the next INVESTIGATE
  created_by / updated_by: [seq]       # journal lineage
}
```

### D.3 How they link to graph facts and form the causal chain

A hypothesis is **not a floating string** (the old model's `Candidate.cause: str` / `RuledOut.hyp: str` — the audit's core defect). It is a graph node whose `causal_chain` is a sequence of `Event`/`condition` nodes joined by `CAUSED_BY`/`CORRELATED_WITH` edges, ordered on the timeline, with `SUPPORTS`/`REFUTES` edges from specific `Fact` objects — so you can walk symptom → hypothesis → evidence, hold multiple live ranked hypotheses, and attach both sides of the evidence structurally instead of as parallel DTO strings.

**One substrate, five lenses** (methods emerge from intent choice, never encoded as prose): 5-whys = walk `causal_chain` backward · causal graph = the full Event graph, evidence-weighted · fault-tree = symptom decomposed AND/OR into conditions · ECFC = Events on the timeline, primary vs secondary chains · change-analysis = seed `root_candidate`s from `ChangeEvent`s and test onset-vs-change correlation first (the cheapest strong signal).

**Evolution across phases:** HYPOTHESIZE *creates* (proposed, change-first) → INVESTIGATE *attaches* supporting/refuting facts, revises `confidence.value`+`basis`, extends/prunes the chain, *reranks* (belief moves only on facts carrying journal refs) → refuted hypotheses are *kept* (status: refuted — they are evidence); a better explanation marks rivals *superseded* → confirmation is Popperian (cross gate **and** survive refutation) → VERIFY gives the strongest confirmation (acting on `root_candidate` cleared the symptom). **The final root cause is exactly one `Hypothesis` with `status: confirmed`ande a verified fix** — not the highest-scoring guess. Its `causal_chain` becomes the postmortem's primary timeline; surviving secondary chains are the contributing factors.

---

## E. CAPABILITY MODEL

The audit's verdict on the existing capability layer is **KEEP** — the governance semantics (registry triad, `govern()`, resolver effect-boundary, gate, idempotency, audit) are the mature core and absorb the 8 tools with essentially zero engine change. The refinement is to model the 8 as **8 distinct Providers** (not collapse into the demo's 3) so ranking, per-provider folding, and per-provider policy get exercised.

### E.1 Adapter contract (what makes it mockable)

Every capability is a pure pair — the only side-effecting boundary is `query()`:

```
Capability.query(intent, params) -> RawJSON        # swap for a fixture loader to mock
Capability.normalize(RawJSON)    -> Operation[]     # pure, deterministic → same typed ops as §B.5
```

Operations apply idempotently by `identity_key`, so re-running an adapter is safe (enables streaming/incremental investigation). A test asserts on the resulting graph, never on live systems. Mock = swap `query()`; `normalize()` and the engine are unchanged.

### E.2 Capability → intent → mock-shape → graph-fold catalog (8 tools)

| Tool | Provider.kind | Key intents | Mock shape (abbrev) | Graph fold |
|---|---|---|---|---|
| **ServiceNow** | mcp_remote/api | get_incident, **find_recent_changes**, get_ci, list_related_incidents | `{number, cmdb_ci:{value,display_value}, opened_at, u_release_tag}` | upsert `Incident` `AFFECTS`→Service CI; `ChangeEvent` `INTRODUCED_BY`→Release; supplies `t_incident` + change timeline |
| **Splunk** | mcp_remote/api | search_errors, error_signature_topk, **search_fw_denies**, transaction_trace | `{_time, exception, msg, trace_id, count}` / `{action:"blocked", dest_port, rule_id}` | `ErrorSignature` `EMITTED` by Service/Pod (exception, first_seen, `file:line`, trace_id→AppD join); fw-deny → deny fact + `EVIDENCE_FOR` firewall hyp |
| **AppDynamics** | api | bt_health, **get_snapshots**, healthrule_violations, flowmap | `metric-data[]` / snapshot `{exitCalls:[{type:"JDBC",...}], errorDetails}` | `BusinessTransaction` facts (art_p95, epm, delta-vs-baseline); snapshot exit-calls *discover* downstream backend CIs + `DEPENDS_ON`; exit-call type is the branch switch (JDBC→DB, HTTP→net, app-only→code) |
| **Prometheus** | mcp_remote/api | **active_alerts**, instant_query, range_query, alert_rules | vector `{metric, value:[ts,v]}` / alerts `{labels, state:"firing", activeAt}` | `Alert` `EMITTED` by Service `CORRELATES_WITH` Incident (alertname seeds hyp); metric facts as `EVIDENCE_FOR/AGAINST` on Service/Host/DB |
| **CMDB** | mcp_remote (trusted) | **get_dependencies**, impact_analysis, get_ci_class, find_ci_by_attr | rel `{type:"Depends on::Used by", parent, child, sys_class_name}` | typed `CI` nodes (`sys_class_name` = step-3 dispatch key) + `DEPENDS_ON`/`RUNS_ON`/`CONNECTS_TO` — the backbone every other tool enriches |
| **OpenShift/OCP** | a2a_agent/api | **rollout_status**, pod_status, events, pod_logs | pod `{CrashLoopBackOff, restartCount, exitCode, image@sha256}` / rollout `{updateRevision, previousImage, reason}` | `Workload`/`Pod` `RUNS_ON` Host; `Deployment`/`Release` with revision + `t_deploy` + digest (→Artifactory join) + rollback target; `ocp__restart` **write**→gate |
| **Artifactory** | api | **get_artifact_by_digest**, get_build, list_promotions, aql_search | AQL `{sha256, properties:{git.revision, build.number, promoted.to/at}}` | `Artifact` (digest) `BUILT_FROM`→`Commit` (git.revision→Git join); `DEPLOYED_AS` by digest match with OCP — "what code is actually running" |
| **Git** | mcp_remote/api | get_commit, **diff_range**, get_pr_for_commit, **blame** | `{sha, author, message, pr, blame:{file, line, snippet}}` | `CodeCommit`/`PullRequest`; `INTRODUCED_BY` Change→Commit; blame join (`file:line`→commit) creates the terminal `CAUSED_BY` ErrorSignature→Commit |

Only `ServiceNow.create_task` and `ocp__restart` introduce **write** effect; both route through the existing gate for free. Every intent lands on an already-existing abstraction — no new primitives.

**Rulings from the audit (refinements, not redesign):** (1) add a validated **intent vocabulary** (enum or load-time check) — stringly-typed `need in c.intents` silently denies on a typo; (2) `run_phase` must try `caps[1..]` on failover, not only `caps[0]` — matters when an intent is multi-sourced (metrics served by both Prometheus and AppDynamics); (3) standardize fold keying on **provider-id** (kill the result-kind vs provider-id ambiguity); (4) reconcile the browser path's string `"read-only"/"write"` with the domain `Effect` enum.

### E.3 The 5 layered scenarios (summarized)

Same 6-step loop for all; only *which facts fold* differs. Scenario identity is emergent, never a code path.

1. **CODE regression** — checkout 5xx/NPE. AppD fast JDBC + Prometheus DB-normal *refute* DB; OCP healthy *refutes* infra; `find_recent_changes`→deploy v4.12.0 → OCP digest → Artifactory `git.revision` → Git `blame TaxCalculator.java:88` → `CAUSED_BY` ErrorSignature→Commit. *Discriminator: pods Ready but throwing.*
2. **Bad DEPLOYMENT** — pricing crashloop. OCP CrashLoopBackOff + `MissingPropertyException`, rollout `ProgressDeadlineExceeded`; `diff_range(rev42..rev43)`→PR removed ConfigMap key. *Discriminator: pods never reach Ready.*
3. **NETWORK** — checkout→inventory timeouts. AppD caller-degraded but callee BT healthy → boundary problem; Prometheus `RetransSegs`/`probe_success` flapping; ServiceNow MTU change on the uplink. *Discriminator: retransmits, callee healthy.*
4. **DATABASE** — pool exhaustion. AppD JDBC 7900ms "pool timeout" (slow boundary = DB); Prometheus `pg_stat_activity 200/200`; ServiceNow migration dropped index → Git query full-scans. *Discriminator: JDBC-slow at the exit boundary.*
5. **FIREWALL** — egress ACL removed. Prometheus `probe_success=0` to one target; Splunk clean `action=blocked` denies; ServiceNow "tighten egress ACL" change 09:05→deny 09:12. *Discriminator: clean policy denies (not drops); security change → human-gated, not auto-applied.*

**≥10 e2e tests fall out:** 5 scenarios × {happy path, one refuted-hypothesis variant} = 10 hermetic tests, plus per-adapter `normalize()` unit tests and join-key fusion tests. The refuted-hypothesis variants assert the engine records the correct `EVIDENCE_AGAINST` — testing the *reasoning*, not just the answer.

---

## F. ENGINE ORCHESTRATION — running a phase end to end

**Keep the LangGraph spine** (the audit's KEEP): `StateGraph` compile, `MemorySaver` checkpointer/resume, `interrupt_before` write-gate with `{decision, actor}` accountability, conditional edges chosen by phase *metadata* not phase id. But collapse the audit's ~20-module sprawl and 4 planner implementations (the generalization outstripped the one live use).

**One phase, deterministically:**

```
run_phase(phase, state):
  1. PLAN      planner.plan(ctx) -> PhasePlan{steps ≤ 5}     # LLM, typed, bounded — from playbook.allowed_intents
  2. for step in plan.steps:                                  # LLM picks intent+order within the whitelist
       caps = resolver.resolve(step.intent, phase.effect)     # effect boundary proven at RESOLVE time
       raw  = layer.invoke(caps[0..], args)                   # govern→gate→exactly-once; failover on error
       ops  = capability.normalize(raw)                       # pure → typed Operation[]
       validated = reducer.validate(ops)                      # registry re-check, edge legality, refs, dedup
       # rejected → bounded repair loop back to the model
  3. PhaseResult = planner.assemble(validated, ctx)           # LLM fills the ONE envelope
  4. engine.fold(PhaseResult):                                # THE single mutation monopoly
       facts/nodes/edges  -> graph  (MultiDiGraph, bi-temporal, idempotent upsert)
       hypotheses_updated -> ledger (rerank, status transitions)
       narrative + delta  -> journal.append(seq++)
  5. controller.route(phase_verdict, next_actions)            # advance|repeat|backtrack|blocked|done
```

**Why it's deterministically testable:** planner and capabilities sit behind `Protocol`s with `ScriptedPlanner(canned_plans)` + `MockCapability(canned_ops)` twins; the reducer/fold is a pure function. Three test seams: (1) *validation* — off-catalog types, illegal edge tuples, out-of-range confidence rejected with the right structured error, repair loop converges; (2) *reducer* — referential integrity, idempotent upsert, dedup, edge legality via golden + property-based tests; (3) *orchestration* — inject scripted planner + mock ops, run the phase engine, exact-match on the final graph. Live-LLM runs assert invariants only (every op registry-valid, every edge legal, ≤N nodes), never exact graphs; temperature 0, pinned model, VCR-style record/replay for fixtures.

**Graph stays out of the checkpoint** (audit KEEP): injected per-node, only the journal/`phase_records` stream is persisted — durable and resumable while the graph re-materializes as a projection.

---

## G. KEEP vs REPLACE — verdict on the existing implementation/

Files of record under `/Users/innamul/Project/iw/implementation/engine-backend/src/`.

**KEEP as-is (the strong spine):**
- `runtime/compile.py` — LangGraph StateGraph compile, checkpointer/resume, `interrupt_before` write-gate, metadata-driven conditional edges.
- `capability/layer.py`, `govern.py`, `resolver.py` — registry triad, pure `govern()` precedence, effect boundary enforced at resolve-time *and* gate, exactly-once idempotency. The strongest part of the build.
- `capability/adapters.py` — the one-method `CapabilityAdapter` Protocol; Mock/Demo/Hybrid all conform.
- `graph_runtime/graph.py` fold-adapter pattern + `render.py` bounded render-slice + idempotent never-overwrite fold + conflicting-facts-side-by-side.
- `domain/common.py` `Confidence{value, basis}`, `extra="forbid"` strictness; `domain/subject.py` `SubjectRef` domain-neutrality; the LLM-never-touches-graph governed tool surface; `domain/phase.py` Step/PhaseRecord audit trail.

**REDESIGN:**
- `domain/graph.py` `Node`/`Fact`/`Edge` — replace the loose `(kind:str, type="generic", layer:str)` catch-all with the tiered typed `NodeType`/`EdgeType` registry (§B); add the reified `Fact` + first-class `Event`; typed `datetime` + `[valid_from, valid_to)`; `props` split from `facts`.
- `graph_runtime/graph.py` — `DiGraph` → `MultiDiGraph` + edge ids; delete the lexicographic recency compare (`:247`).
- `domain/outputs.py` — collapse the four shapes (`AssessResult`/`RootCauseResult`/`RemediationResult`/`VerifyResult`) into the ONE `PhaseResult` envelope (§C.2).
- `runtime/state.py` — remove the schema-coupled routers (`candidates[].confidence.value`, `recovered`); read `phase_verdict` + ledger instead.
- `runtime/llm_planner.py` — move `_floor` and hardcoded intent ordering into the playbook; planner emits typed `PhasePlan` + `PhaseResult`, not a prose sentence; retire `_minimal()` degraded outputs.
- `playbooks/incident-triage.md` — tune to the 5-field-per-phase declarative schema (§C.4); replace unguided `needs` menu with `allowed_intents` + `gate_exit` + `produces_required`.

**DISCARD:**
- The 4 parallel planner implementations (`Planner` Protocol + `ScriptedPlanner` + `MultiPhasePlanner` + `LLMPlanner`) collapse to one `Planner` Protocol + LLM/scripted twins.
- Hypothesis-as-string (`Candidate.cause`, `RuledOut.hyp`), the parallel `Candidate.evidence` re-listed strings, and the `Edge.props["path"]`↔`Candidate.path` duplication — replaced by `Hypothesis` nodes + `SUPPORTS`/`REFUTES` edges.
- `time_factor.kind="cron"` scalar demotion of batch jobs — a `BatchJob` is now a real node that can *be* a cause.
- The demo's collapse of 8 tools into 3 providers — model all 8 as distinct Providers.

---

## H. DATA/APP LAYERING + PERSISTENCE

Clean app/data split; in-memory graph backed by a file; the owner's entity-class folder is first-class.

```
engine-backend/
  src/engine/
    domain/                      # DATA LAYER — pure types, zero I/O, the registry
      nodes/                     # one file per NodeType class (the owner's entity-class folder)
        service.py  pod.py  host.py  database.py  deployment.py
        change_event.py  code_commit.py  alert.py  incident.py
        hypothesis.py  generic_ci.py  __init__.py   # exports the tagged Node union
      edges/                     # one file per EdgeType class
        depends_on.py  runs_on.py  calls.py  caused_by.py
        correlated_with.py  supports.py  __init__.py
      registry.py                # NodeType/EdgeType enums, tagged unions, edge-legality tuple allow-list
      fact.py  event.py  common.py  subject.py       # Fact, Event, Confidence{value,basis}, SubjectRef
      operations.py              # AddNode/AddFact/AddEdge/ProposeHypothesis — the LLM emission union
      phase_result.py            # the ONE PhaseResult envelope
    graph_runtime/               # APP LAYER — the projection engine
      graph.py                   # in-memory MultiDiGraph, bi-temporal, idempotent upsert
      reducer.py                 # host-side validate: registry re-check, edge legality, refs, dedup, repair
      fold.py                    # fold(PhaseResult) -> graph ; per-provider capability fold-adapters
      render.py                  # bounded render-slice for the LLM
      persistence.py             # load/save the graph to data/graph/<incident_id>.json (file-backed)
    ledger/                      # APP LAYER — hypothesis ledger projection
      ledger.py                  # ranked hypotheses, rerank, status transitions
    journal/                     # APP LAYER — append-only source of truth
      journal.py                 # JournalEntry append (seq++), read, fold-to-postmortem
    runtime/                     # APP LAYER — orchestration
      engine.py  compile.py  phase.py  controller.py  planner.py  loader.py
    capability/                  # APP LAYER — governed capability access (KEEP)
      layer.py  govern.py  resolver.py  registry.py  adapters/
        servicenow.py  splunk.py  appd.py  prometheus.py
        cmdb.py  ocp.py  artifactory.py  git.py
    playbooks/
      web-tier-5xx-spike.md      # the tuned incident playbook (declarative, one page)
  data/                          # the owner's data/ folder — file-backed state + fixtures
    graph/<incident_id>.json     # the in-memory graph's backing file (load at start, save on fold)
    journal/<incident_id>.ndjson # append-only journal on disk (the durable spine)
    fixtures/                    # per-capability mock RawJSON, keyed by scenario
      inc4821/ scenario2/ ... scenario5/
  tests/
    unit/                        # normalize() + reducer + validation seams
    e2e/                         # 5 scenarios × {happy, refuted} = 10 hermetic tests
  frontend/                      # React FE — reads graph + journal + ledger projections
    src/{GraphView, JournalTimeline, HypothesisLedger, PhaseController}.tsx
```

**Persistence ruling:** the graph is **in-memory `MultiDiGraph`, backed by a JSON file** (`data/graph/<incident_id>.json`) — loaded at session start, re-saved after each `fold()`. The journal is append-only NDJSON (`data/journal/<incident_id>.ndjson`) and is the *durable* artifact; the graph and ledger can always be rebuilt by replaying it, so the file-backed graph is a fast cache, not the truth. **Skip multi-user/session complexity** — one incident = one `<incident_id>` file set; no concurrency, no DB, no auth. Correctness of the projection first.

**React FE** reads the three projections over a thin read API: `GraphView` (typed nodes/edges, tier-colored), `JournalTimeline` (the running story), `HypothesisLedger` (ranked chains, supporting/refuting evidence, confidence+basis), `PhaseController` (current phase, gate state, HITL approval for `apply_mitigation`/`ocp__restart`).

---

## I. OPEN DESIGN QUESTIONS / TRADE-OFFS for the design phase

1. **Batch-emit vs many-small-tools for LLM ops.** Brief 6 recommends *batch* (`apply_operations(ops[])`, forced tool_choice, `maxItems` cap) for extraction phases and *many-small-tools* for the interactive hypothesis phase. Ruling proposed: batch per phase by default, small-tools for HYPOTHESIZE — design phase to confirm and set the `maxItems` cap per phase.
2. **Enforcement stack per deployment.** Native structured outputs (shape guarantee) + instructor-style repair loop (semantic constraints) is the recommendation. Design phase to pin: which constraints are grammar-enforceable vs reducer-only for *our* provider path, and the `max_retries` before a phase reports `blocked`.
3. **`ChangeEvent`: node or inline event?** Brief 4's rule is "an occurrence becomes a node when other things point at it" — `ChangeEvent` and `Alert` firings are promoted. Confirm the promotion boundary; e.g. does a state-transition fact (`degraded since 14:02`) ever need node-hood, or always stay an inline fact-window?
4. **Confidence representation.** Rubric enum (`LOW/MED/HIGH`) for LLM-emitted ops (Brief 6, anti-"0.9-everywhere") vs `{value:0..1, basis}` for ledger scoring (Brief 5). Proposed: enum at emission, mapped to a numeric band with mandatory basis in the ledger. Design phase to fix the mapping.
5. **Evidence-weight function.** Brief 7's `weight = source_reliability × temporal_proximity × topological_specificity` and promotion rule (`score>θ`, margin`>δ`, no unrefuted competitor). Are θ/δ/reliability-per-source engine constants or playbook-tunable? Proposed: playbook-tunable (they are class-specific WHAT/WHEN), engine owns the arithmetic.
6. **Gleaning / re-extraction.** Owner wants *less*, not more — hard `maxItems` stop, no gleaning continuation past one capped pass with a typed "nothing-left" sentinel. Confirm the sentinel shape and whether any phase (e.g. FRAME topology seeding) legitimately needs a second pass.
7. **Identity resolution across tools.** Join keys (`trace_id`, image digest, `git.revision`, `file:line`, CI `sys_id`, time window) fuse cross-tool facts. Where does canonicalization live — reducer, or a dedicated resolver? Proposed: reducer, keyed by `identity_key` + a small alias table. Design phase to specify the alias/merge rules (e.g. `Service` by `service.name+env`).
8. **Earned autonomy scope.** Current build has whole-row `ask↔allow`; FR13's per-phase×node-type×severity scoping is unbuilt. Out of scope for CORE (single-user, correctness-first) — flag as the first post-CORE increment, not a v1 requirement.
9. **How much of LangGraph to keep vs a hand-rolled controller.** The audit keeps LangGraph; Brief 5's blackboard controller picks next-phase from board state. These are compatible (LangGraph conditional edges *are* the controller), but confirm whether re-enterable backtracking is expressed as LangGraph conditional edges or a thin custom loop over `phase_verdict` — the uniform contract makes either cheap.

*Files written: none — returned inline per the deliverable spec.*

---

## PART 2 — COMPLETENESS CRITIQUE (gaps to resolve)

# Completeness Critique — Investigation Workbench CORE DESIGN-INPUT (v1)

Strong blueprint; the single-contract/single-fold spine is genuinely the right backbone. But the closed registry is not actually closed over the capability layer, the scenario set has a systematic blind spot, and the journal-as-source-of-truth claim is contradicted by its own schema. Details below, prioritized.

## Verdict on the 3 named failures

- **Failure 1 (over-detailed/untuned playbook) — mostly fixed, one residual.** Three-authors + declarative YAML is the right mechanism. But tuning constants are now *scattered across three places*: playbook YAML (`confidence_gate`, evidence floors), engine constants (θ/δ, source-reliability, rerank arithmetic — §I.5), and unresolved opens (`maxItems` per phase §I.1, `max_retries` §I.2, confidence band mapping §I.4). "Untuned" recurs as "tuning surface never inventoried." **Fix:** a single explicit `tunables:` block in the playbook enumerating *every* knob, with the engine owning only arithmetic — and a rule that no tuning constant may live in engine code.
- **Failure 2 (wrong graph model) — directionally fixed but the registry is internally inconsistent.** Typed tiers + property/fact/event + bi-temporal is correct. Undermined by three things: uncataloged types in the folds (P0-1), Service/Microservice/Component/Application classification overlap (P1-2), and dead/contradictory node types (P1-1). As written, failure 2 *partially recurs*.
- **Failure 3 (inconsistent per-phase outputs) — cleanly fixed.** The single `PhaseResult` + `fold()` genuinely retires the four shapes and the schema-coupled routers. Only residuals: `produces_required` forcing fabrication (P0-4) and three overlapping confidences (P2-1). Structurally sound.

---

## P0 — breaks a stated core promise / re-opens a named failure

**P0-1. Capability folds emit node/edge types absent from the closed catalog.** §E.2 folds produce `ErrorSignature`, `BusinessTransaction`, `PullRequest`, `Workload`, and bare `CI` nodes, and edges `INTRODUCED_BY`, `EMITTED`, `DEPLOYED_AS` — none of which appear in the B.2 NodeType or B.3 EdgeType catalog. The closed registry is the *entire* mechanism against failure 2, and the capability layer punches holes in it. **Fix:** reconcile — either add these to the registry (e.g. `ErrorSignature` is a real signal node you'll want to attach facts to) or map each to an existing type in the fold; then add a CI test asserting the union of types any `normalize()` can emit ⊆ registry. This test is the guardrail that keeps the registry closed as capabilities grow.

**P0-2. Every scenario is change-caused; the no-change incident class is untested and methodologically at risk.** All 5 scenarios (§E.3) resolve to a change (`find_recent_changes` → deploy/config/migration/ACL). HYPOTHESIZE is "change-first" and change-analysis is called "the cheapest strong signal." But a huge fraction of real incidents have **no triggering change**: organic traffic surge / thundering herd, retry storms, gradual resource/connection leaks, cascading/dependent failures, noisy-neighbor, cert/credential expiry. When `find_recent_changes` returns empty, the blueprint has no defined fallback path and zero test coverage. **Fix:** add at least one no-change scenario (e.g. traffic-driven pool exhaustion with no migration) and specify the HYPOTHESIZE fallback when the change set is empty (seed `root_candidate`s from USE-saturation / onset-correlated Events instead of ChangeEvents).

**P0-3. Journal schema (D.1) can't rebuild the graph, contradicting the source-of-truth claim.** §H states graph+ledger "can always be rebuilt by replaying" the journal, and C.3 says "narrative + the whole delta → one journal entry." But the D.1 `JournalEntry` schema stores only `refs: {nodes, hypotheses, facts}` (links), not the full `facts_added/edges_added/hypotheses_updated` payloads. As specified, replay reconstructs pointers to nothing. **Fix:** persist the full `PhaseResult` delta as the journal entry's event payload; make `refs` a derived index over it. Without this, "journal is the durable spine" is false.

**P0-4. `produces_required` forces fabrication of null results.** Requiring `facts_added`/`hypotheses_updated` non-empty every phase means an INVESTIGATE that *correctly finds no relevant evidence* must invent a fact to pass the gate — directly contradicting the owner's "want less, not more" (§I.6) and injecting confirmation-bias pressure. **Fix:** define a typed no-evidence/null-result sentinel (e.g. a `NoEvidence{intent, scope, basis}` fact) that satisfies `produces_required` and is itself RCA-meaningful ("we looked at X, it was clean" is evidence that refutes hypotheses).

---

## P1 — real gap, likely to bite

**P1-1. `RootCause` / `Remediation` / `Anomaly` node types are dead or contradictory.** B.2 lists all three as NodeTypes, but D.3 says the root cause *is* a confirmed `Hypothesis` and remediation is a `REMEDIATED_BY` edge; `Anomaly` never appears in any fact/edge/scenario. **Fix:** decide explicitly — either drop `RootCause`/`Remediation` (Hypothesis+edge already model them) or define their exact relationship to Hypothesis; give `Anomaly` a defined role (see P1-6) or remove it.

**P1-2. Service/Microservice/Component/Application overlap re-opens classification drift.** Four L0 types with no discriminator rules is precisely the `svc`/`service`/`microservice` proliferation failure 2 targeted, now re-encoded. The LLM has no basis to choose Service vs Microservice. **Fix:** add explicit machine-readable discriminator rules per type in the registry, or collapse to fewer types plus a `kind`/`role` static prop.

**P1-3. "Mitigate before you understand" (principle 12) has no executable home.** TRIAGE's *goal* is "mitigate-now vs investigate," but its `allowed_intents` are only `assess_impact, list_dependencies` — no mitigation intent — and `apply_mitigation` lives only in REMEDIATE (phase 4), whose entry isn't in TRIAGE's `on_verdict` routing. Early mitigation is stated as a first-class principle but is unreachable in the phase machine. **Fix:** add a gated `apply_mitigation` intent to TRIAGE (or an explicit TRIAGE→REMEDIATE→back route) so mitigation can genuinely precede root-causing.

**P1-4. No CLOSE path for mitigated-but-not-root-caused incidents.** CLOSE/postmortem is defined as "confirmed chain + timeline"; §D.3 says the root cause is "exactly one Hypothesis with status:confirmed and a verified fix." But a very common real outcome is *mitigated, impact stopped, never definitively root-caused*. That incident has no representable close state. **Fix:** model an "unresolved/mitigated" CLOSE that folds the best surviving (unconfirmed) hypotheses + the mitigation timeline; the postmortem must render without a confirmed chain.

**P1-5. Cross-source clock skew breaks the onset-vs-change join that change-analysis relies on.** Prometheus, Splunk, ServiceNow, OCP, and Git each carry their own clock; change-analysis correlates `t_deploy`/`t_change` against symptom `onset` at minute granularity (scenario 5: change 09:05 → deny 09:12). Skew of seconds-to-minutes silently corrupts the "cheapest strong signal." Bi-temporal typing (P0 of failure 2) makes clocks *precise* but not *aligned*. **Fix:** model per-source clock offset / a tolerance window in the temporal join; never assert change→symptom ordering tighter than the combined skew bound.

**P1-6. Static-property "correct in place" contradicts "reconstruct the graph as of incident-start" (principle 8).** B.1 says static properties are "corrected in place" (no history), yet principle 8 promises point-in-time graph reconstruction. Any incident-relevant attribute that is actually mutated in place (a Service's `tier`, a Pod's `node_name`, `slo_target`) is unrecoverable at incident time — the exact reconstruction the design sells. **Fix:** ruling — any time-varying, incident-relevant attribute is a **Fact** with a valid-time window, not a static prop; "static" is reserved for identity/immutable attributes. Document the boundary test explicitly (it's load-bearing and currently only implied).

**P1-7. The symptom / entry-point node type is unspecified.** FRAME must produce "the symptom node," and the entire method operates over "the symptomatic subgraph," but no NodeType is designated for the primary symptom (Alert? Anomaly? an ApiEndpoint `5xx-spike` event?). Ambiguity here fragments every downstream traversal. **Fix:** pin the symptom representation — an `Anomaly` node (giving that dead type a job, per P1-1) carrying the onset fact + `AFFECTS`→Service — as the canonical FRAME output.

**P1-8. Reducer batch atomicity and intra-batch ordering are unspecified.** With batched ops (§I.1) and referential-integrity checks, the spec doesn't say what happens when one op in a batch of 5 is illegal (whole batch rejected, or 4 fold?) or how an `AddEdge` referencing an `AddNode` in the *same* batch is ordered. This is central to composability and determinism. **Fix:** define partial-accept semantics (reject only the offending op, journal the rejection) and a topological apply order within a batch (nodes before their edges/facts).

**P1-9. Missing entity types for common real incidents.** No `Certificate`/`Secret` (TLS/cert expiry and credential rotation are top-tier real causes), no `FeatureFlag` (flag flips are a massive modern incident class; `ChangeEvent{config}` under-models them and no capability surfaces them), no `ExternalService`/third-party (vendor/SaaS outages), no `Quota`/`RateLimit`. **Fix:** add at least `Certificate` and `FeatureFlag` as first-class nodes with expiry/flip events; consider a cert-expiry scenario. These are also change-adjacent-but-not-in-CMDB, reinforcing P0-2.

**P1-10. Two control signals with unclear authority.** `PhaseResult.next_actions` (LLM-emitted) and the blackboard controller picking next-phase from board state (§I.9) are both routing inputs; which wins is unspecified. This is a composability/elegance hazard. **Fix:** declare `next_actions` advisory (seeds the *plan* within the next phase) and make the controller's verdict+board-state routing authoritative for *which phase* — or the inverse — but pick one.

---

## P2 — worth fixing, lower risk

**P2-1. Three overlapping confidences.** `Fact.confidence`, `phase_verdict.confidence`, `Hypothesis.confidence` coexist with unspecified relationships; and confidence-on-a-measured-fact (what does 0.9 mean on a Prometheus CPU reading?) conflates measurement precision with inferential belief. **Fix:** document each confidence's distinct meaning; for directly-measured facts prefer `source_reliability` over a belief score, reserving `confidence{value,basis}` for inferred facts/hypotheses.

**P2-2. No fact retraction distinct from supersession.** `supersedes` handles "newer truth," but bad telemetry that was simply *wrong* (not superseded by a later value) has no tombstone. **Fix:** add a `retracted`/`invalidated` fact state so refuting a fact's validity is distinguishable from time-closing it.

**P2-3. Persistence crash-safety and versioning.** Whole-graph JSON is rewritten each `fold()` with no atomic write (crash mid-save corrupts the cache); NDJSON can end in a partial line; no `schema_version` is stamped, so replaying a journal after a registry change breaks. **Fix:** write-temp-then-rename for the graph cache; skip a trailing partial line on journal load; stamp `schema_version`; on load, treat the journal as authoritative and rebuild if the cache disagrees.

**P2-4. `causal_chain: [Event]` can't hold conditions/states.** D.3 references "Event/condition nodes," but the Hypothesis schema and NodeType catalog have no Condition/State — yet chains routinely include latent states ("pool already at 80% *when* the deploy landed"). **Fix:** allow Facts (windowed conditions) in the chain, or add a `Condition` node type.

**P2-5. GenericCI escape hatch has no catalog-gap feedback loop.** Repeated `GenericCI{class_hint}` for the same hint is the signal that the registry is missing a type, but nothing aggregates it — the closed registry silently ossifies. **Fix:** log `class_hint` frequency as a registry-evolution signal (fits this repo's n-watch/inbox pattern).

**P2-6. Read API between file persistence and React FE unspecified.** §H says the FE reads three projections "over a thin read API" but persistence is bare files with no server described. **Fix:** specify the read interface (even a trivial local read server), since the FE can't read NDJSON/JSON from the browser directly.

**P2-7. Per-phase total op budget unbounded.** Only `steps ≤ 5` and a TBD per-step `maxItems` are set; 5 steps × N ops can still over-produce, against the owner's "less" directive. **Fix:** set an explicit per-phase op ceiling alongside the per-step cap.

---

**Already flagged in §I (not re-counted as gaps, but confirm during design):** gleaning vs full-topology tension (I.6 — note it directly collides with P0-2's need to seed topology), confidence enum→band mapping (I.4 → P2-1), θ/δ/reliability placement (I.5 → Failure-1 residual), identity resolution/alias table (I.7 → relevant to P1-6 static-vs-fact and clock-skew fusion). The blueprint's self-awareness here is good; the items above are the ones it does *not* yet see.