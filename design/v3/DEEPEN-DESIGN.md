## Verdict on observation 10 (reasoning ownership)

**Today the demo depth is 100% scripted. No LLM runs behind the workbench you see.** `create_server` (server.py:88-94) is built with no `planner_factory`, so it falls through to `build_manager()` (scenarios.py:120-145), whose `planner_factory` returns a `ScriptedPlanner` that replays a hand-authored `list[PlanOutput]` (planner.py:52-70). The "12 facts", the INC-4788/INC-4790 related incidents, the "confirmed root cause" basis strings — every one is a literal `fact(...)`/`update(...)` call authored by a human in `tests/e2e/scenario_*.py`. The Engine/reducer/ledger/journal only *fold* those canned outputs deterministically. So the OWNER is right: **the reasoning currently lives in the mock, and that violates "you should not be in the execution."**

The good news: the live path is real code, not vaporware. `LivePlanner` (live_planner.py:299-326) is a genuine Planner-Protocol implementation that prompts Gemini/xAI for one JSON plan per phase with reject+repair, and its narratives/hypotheses/verdicts *are* model-authored. It is simply wired in exactly one place — `scripts/run_live.py:250`, a batch CLI that prints to stdout. It never touches `server.py`, `session.py`, or `SessionManager`.

**The exact architectural change (mock becomes the CI net, LLM becomes the product):**

1. **Add `live_build_manager()` to scenarios.py**, mirroring `build_manager` but with `planner_factory` returning a `LivePlanner` and `layer_factory` returning the live `CapabilityLayer`, reusing `run_live.py`'s wiring verbatim (`render_catalog`/`render_tools`/`tool_intents`/`ScenarioSource`/`available_intents`, run_live.py:248-251). **No engine or session change is required** — `create_server` already accepts `planner_factory`/`layer_factory` (server.py:76-97). Select live vs mock by env flag / API-key presence, exactly like `run_live`'s `make_client` (run_live.py:222-229). Keep `build_manager` (ScriptedPlanner) as the always-on deterministic CI/offline net.
2. **Move the two `run_live`-only hooks into `session._drive`** so the interactive path matches the batch path: set `planner.graph = engine.graph` at session construction (run_live.py:254) and phase-scope the source with `source.phase = engine.current_phase.value` before each step (run_live.py:260-261). Both are additive.
3. **Make the LLM call non-blocking for SSE.** `LivePlanner.plan` is synchronous with a 4.5s min-interval and 120s timeout (live_planner.py:123-161); driven inline it would freeze the SSE loop for the full latency per phase. Run it in a thread/executor so keep-alive frames keep flowing; drop `min_interval` when a paid key is present.
4. **Bring live to parity.** The live fixtures in `run_live.py` are leaner than the twins (no related-incidents, no full DB USE pull, only 3 of 6 scenarios). Promote them to carry the same evidence, or — cleaner for "not in the execution" — replace `ScenarioSource` with real provider Sources so the *data* is also live. Author the missing scenarios so live covers every layer.
5. **Prove it.** Promote `run_live`'s converged check (run_live.py:264-269) to a gated (skippable, key-required) live test asserting each incident converges to the expected root cause through the HTTP/SSE surface and the write-gate. The scripted golden/e2e suite stays always-on as the deterministic guarantee. **"Proven" = the same six+ incidents converge with the ScriptedPlanner deleted from the factory, driven only by Gemini/xAI, asserted green in the gated job.**

Net: one new factory function + one env switch + one threading change turns the LLM into the product experience while the mock is demoted to precisely what the OWNER wants it to be — the deterministic CI net.

---

## Per-observation design table

| Obs | Current state (grounded in map) | Gap | Elegant + composable fix | Files to touch |
|---|---|---|---|---|
| **1** Incident = origin = #1 | `nodesWithOrder` sorts by `created_by` (phase seq) → anomaly-only symptom tiebreak → id (store.ts:368-376). Cold-loaded nodes all get `created_by:0`, so among seeds order is symptom-then-id. **#1 today = the anomaly, never the incident.** | Origin is never pinned; incident node floats. | Add an **origin rank 0** ahead of `created_by`: `node.origin || (type==='incident' && id===\`incident:{subject.id}\`)` sorts first; existing keys stay as fallback; dense `i+1` renumber already flows to badge + drawer `#`. Back it with an `origin: bool` on the Node so journal replay reproduces #1 deterministically (see Data-model). | store.ts:368-376; node.py; reducer.py:73-82 |
| **2** Real two-way chat | ChatPane renders phase cards only (ChatPane.tsx:25-89); no textarea/send anywhere. Backend `add_message` (session.py:163-169) buffers into `_messages` but "does not mutate the fold", never `_emit()`s, never journals, and the planner never reads it. Frontend has no POST fn. | No input, no transport, no steering, no provenance, no user turn in the model. | Make the message a **first-class event on the existing SSE+fold seam**: `add_message` calls `self._emit('user_message', …, source=Source.HUMAN)` + `journal.append_step(Source.HUMAN)` (mirror `_record_gate_decision`, session.py:144-161). Add `sendMessage` to api.ts + useInvestigation.ts, a `user_message` SessionEvent variant, and a store fold case that interleaves a user Turn by `seq`. Add a composer to ChatPane (always available while state≠closed). Feed `_messages` into `PlanContext` so the LLM planner sees operator context on the next plan — the load-bearing change. For interrupt, let a queued message create a lightweight "question" suspend analogous to `_GateSuspend`. | ChatPane.tsx; api.ts:56-81; useInvestigation.ts:175; store.ts:77-93,219-347; types.ts:286-294,301; session.py:163-169,208-222,342-346; server.py:135-138 |
| **3** Approval gate = decision prompt | ApprovalCard has a refine-params `<input>` + deny-reason `<textarea>` (ApprovalCard.tsx:116,124); gate evidence already ships via `gate_opened`/`_fact_view` (session.py:329-334). | Reads as approve/refine/deny, not "here's the ask, the options, the recommendation, or something else". | Restructure the card into a **decision prompt**: (a) *what's asked* = the remediation intent + its evidence facts (reuse `_fact_view`); (b) *options* = the candidate actions with the model's **RECOMMENDED** one marked (confidence rung from the hypothesis); (c) the existing refine `<input>` becomes the **"do something else" free-text** box; (d) deny stays. Carry the three Datadog/AG-UI message classes (steer / approve-proposed-action / direct-command); every side-effecting option keeps its explicit confirm. | ApprovalCard.tsx:100-130; session.py:320-334; ChatPane.tsx |
| **4** Graph by architectural layer + LAYER/SOURCE badges | `tiers.ts` columns are change/logical/data/network/runtime/signal (analytic families, not architecture); `TIER_BY_TYPE` is stale vs the NodeType enum (keys `queue`, `dns_record`; omits cluster/pod/container/etc.). Layout is one column per tier (LiveGraph.tsx:100-120). No layer chip, no source chip on the node face. | Not architectural; many nodes mis-lane; layer implied only by column color; source only inferable per-fact in the drawer. | Introduce a first-class **`LAYER_BY_NODETYPE`** derived from the engine's NodeType L0-L6 groups (see Layer taxonomy). Reuse `TIER_ORDER→LAYER_ORDER`, `TIER_LABELS→LAYER_LABELS` so the column layout code is untouched. Add an on-node **LAYER chip** (text, beside `node.type` at LiveGraph.tsx:364) and per-layer rect color for *every* layer (fix the uncolored `runtime`/messaging/infra at styles.css:585-601). Add an on-node **SOURCE chip** fed by the new `Node.source` (Data-model). Delete/converge the unmounted `IncidentGraph.tsx`. | tiers.ts:6-52; LiveGraph.tsx:100-120,277-287,349-369; styles.css:585-601; IncidentGraph.tsx |
| **5** Node provenance: where-from + when-first-seen | `Node` = id/type/props/created_by only (node.py). Provenance lives on Facts (`Fact.source`, bi-temporal) and on `Invocation.provider` (layer.py:65-75) — but the producing provider is **never linked back onto the node**. `created_by` is a phase seq, not wall-clock. GraphDeltaNode carries only {id,type,created_by}. | "Where fetched from + when created" is unrepresented at the node level, end-to-end. | Thread provenance through the **existing fold seam**: stamp `source`/`first_source`/`first_observed_at`/`observed_at` on the Node in `reducer.materialize`/`apply_delta` using `Invocation.provider`; carry on GraphDeltaNode/GraphNode; fold onto LiveNode. Render as a badge "fetched from {source} · {relative time}". Prefer the fold path so replay is deterministic. | node.py; reducer.py:73-82; fold.py:18-27,53-70; types.ts:23-27,218-222; store.ts:19-24,243-252; LiveGraph.tsx:349-369 |
| **6** Fuller static props, each with source + ts | `Node.props` holds `app_id`/CI attrs as bare key-values with no source, no timestamp. Facts already carry the full envelope. | Static props have no provenance. | **Represent sourced CI attributes as Facts, not bare props** (predicate=`app_id`, value, `source=cmdb`, `observed_at=…`). Keep `Node.props` for identity-only fields. The drawer already renders facts with `f.source` (LiveGraph.tsx:431), so richer props "just work" once emitted as facts — one envelope, no parallel `{value,source,ts}` maps. | fact.py; scenario/live fixtures; LiveGraph.tsx:397-437 |
| **7** Facts as full W's; edges = relation + WHEN | Facts already carry W's: `source` (WHO), `subject_ref` (WHERE/WHAT), `valid_from/valid_to/observed_at` (WHEN), `evidence` (WHY/proof). Edges carry `origin` (declared/discovered/inferred) + type but **no established time**. Live `GraphDeltaFact` drops source (store.ts:266 sets `source:''`). | Facts lack an optional spatial `where` qualifier; edges lack established-WHEN + source; live facts show blank WHO. | Facts: **scope to surfacing** — add source/unit/valid_from to `GraphDeltaFact`, stop blanking source. Add one optional `where` qualifier to Fact. Edges: add `source` + `valid_from`/`valid_to`/`observed_at` (established/ended time) — identical envelope to Fact (see Data-model); surface relation-type + "established {time}" on edge hover. | fact.py; edge.py; types.ts:230-235; store.ts:266; fold.py |
| **8** Hypothesis ledger expandable + clickable cross-highlight | **Engine model is already rich**: `Hypothesis` carries `supporting_facts`/`refuting_facts`/`causal_chain` (typed `ChainLink` with ts/role/kind/ref) + `root_candidate`; `export_bundle` ships all of it. But `HypothesisLedger.tsx` renders only `.length` count chips (54-61) — never resolves ids, never renders the chain, nothing clickable. Graph `selectedId` is component-local (LiveGraph.tsx:85), never lifted. | Evidence shown as counts only; no expand; no cross-highlight; ledger has no access to facts/events to resolve ids or show WHO. | **UI-only for the model; plumbing for live.** Lift a single `selection {kind, id}` to Workbench (or a `selection` field on LiveState); make LiveGraph's `selectedId` a *controlled prop* and reuse its existing `focusNode()` + `graph-node--selected` highlight (LiveGraph.tsx:218-227) as the sink. Pass `onHighlight` + the graph maps (`live.facts/events/nodes`) into HypothesisLedger; render `<details>` sections (supporting / refuting / ordered causal_chain) with each row a button, each showing WHO via `· {fact.source}` (reuse JournalPane.tsx:101). Add a typed `ChainLink` interface (replace `chain: unknown[]`). Close the live gap: carry `supporting/refuting/chain` on `ledger_delta` + `source` on `GraphDeltaFact`. | HypothesisLedger.tsx:54-61; LiveGraph.tsx:85,218-227; Workbench.tsx:39-47; types.ts:77-87,248-254; store.ts:298-312,258-271 |
| **9** Agent-trace depth: purpose, tool, tool-vs-workflow, when + duration | `capability_call` carries only {intent,provider,effect,op_count,blocked,reason} (session.py:300-304); `Invocation` has **no timing** (layer.py:65-76); `ToolCallCard` shows none. The only timestamps are phase-batched emit `ts` and the *simulated* incident clock. **Per-step timing exists nowhere.** | No when/duration; no explicit tool-vs-workflow kind; no per-step purpose surfaced. | Adopt the OTel-GenAI / Honeycomb Agent-Timeline span shape. Wrap `layer.serve(...)` in `engine._run_phase` (engine.py:124-126) with `perf_counter`; extend `Invocation` with `started_at`/`duration_ms` + `kind ∈ (llm\|tool\|workflow\|handoff)` (tool-vs-workflow is just `span.kind`); surface on `capability_call`; render start-time + duration + a "purpose" line on `ToolCallCard`. Optionally record LLM latency per phase into `PhaseTrace` (live_planner.py:284-296) as a live "thinking time" indicator. | engine.py:124-126; layer.py:65-76; session.py:300-304; ToolCallCard.tsx; live_planner.py:284-296 |
| **11** ≥2 detailed use cases per layer | Exactly 6 twins, `_CATALOG` maps one incident per layer (scenarios.py:41-60). Messaging/infra/edge = 0, though the NodeTypes are already modeled (enums.py:146-168). Variant flags are the same subject replayed, not new cases. | Every modeled layer sits at 1; three OWNER-named layers at 0. | Author the second use case per existing layer + full 2-per-layer for the 3 new layers (see Use-case matrix). Each new twin = `e2e.scenario_<key>` with a **unique catalog id** + remediation string in `_CATALOG`; extend `test_scenarios.py:16` `CATALOG_IDS` + the layer-set assertion; resolve the INC-9001 native-id collision. Widen DESIGN.md:61 (R-K3) from 6-one-per-layer to a ≥2/layer matrix. | scenarios.py:41; tests/e2e/scenario_*.py; test_scenarios.py:16; enums.py:146-168; docs/DESIGN.md:61 |

---

## Data-model changes

One shared provenance envelope (source + two clocks), attached identically to Node, Fact, and Edge — reusing the existing `Source`/`Origin` enums, `apply_delta`/`fold`, and the `NodeSpec`/`EdgeSpec` plan-output shapes. All fields are **optional/defaulted, so goldens stay green**; the engine simply stamps them in the one mutation seam.

**Node** (`domain/node.py`) — add where-fetched-from, when-first-seen, which-source-first, and the origin pin:

```python
@dataclass(frozen=True)
class Node:
    id: NodeId
    type: NodeType
    props: dict[str, Any] = field(default_factory=dict)
    created_by: int = 0                          # phase seq (lineage) — UNCHANGED
    # --- provenance envelope (all optional; additive) ---
    source: Source | None = None                 # WHO/where-fetched-from (last writer)
    first_source: Source | None = None           # which Source FIRST materialized it
    first_observed_at: datetime | None = None    # WHEN first seen
    observed_at: datetime | None = None          # WHEN last touched
    origin: bool = False                         # True on the ServiceNow incident row → pins #1
```

Stamp in `reducer.materialize` (reducer.py:73-82) / `fold.apply_delta` (fold.py:18-27,53-70): on **first insert** set `first_source`/`first_observed_at` from `Invocation.provider` (layer.py:65-75) + the run clock; on every touch update `source`/`observed_at`; set `origin=True` when `type==NodeType.INCIDENT and id == f"incident:{subject.id}"`. `NodeSpec` gets the same optional fields so both planners can populate them.

**Fact** (`domain/fact.py`) — already carries `source` (WHO), `subject_ref` (WHERE/WHAT), `valid_from`/`valid_to`/`observed_at` (WHEN), `evidence` (WHY/proof). **Only one W is missing** — an optional spatial/context qualifier:

```python
    where: str | None = None   # optional: segment / AZ / node / region context
```

The rest of obs 5/6/7 for facts is **surfacing, not modeling**: add `source`, `unit`, `valid_from` to `GraphDeltaFact` (types.ts:230-235) and stop setting `source:''` in the reducer (store.ts:266).

**Edge** (`domain/edge.py`) — already carries `type` (relation) + `origin` (declared/discovered/inferred). Add source + established/ended time (same envelope as Fact):

```python
    source: Source | None = None
    valid_from: datetime | None = None   # WHEN the relation was established
    valid_to: datetime | None = None     # null = still live
    observed_at: datetime | None = None
```

`EdgeSpec` gets the matching optional fields; `fold` reads them; the FE surfaces "relation-type · established {time}" on edge hover.

**Node #1 = origin** — with `Node.origin` above, the pin becomes a two-line selector change in `nodesWithOrder` (store.ts:368-376): rank origin nodes `0` before the `created_by → symptom → id` keys. The existing dense `i+1` renumber already flows to the node badge and the drawer `#`. FE-only pin works immediately; the `origin` flag makes it survive journal replay deterministically.

**SSE carriers** — extend `GraphDeltaNode` (types.ts:218-222) with `source`/`first_source`/`created_at`/`origin`; `GraphNode` (types.ts:23-27) the same; fold onto `LiveNode` (store.ts:19-24,243-252). `Invocation` (layer.py:65-76) gains `started_at`/`duration_ms`/`kind` for obs 9.

Design invariants (from the bitemporal/PROV-O/CMDB research): **two clocks are mandatory, every other W optional; the minimal core everywhere is `source + two timestamps`; one envelope reused by node/fact/edge; append + supersede (close by `valid_to`), never overwrite.**

---

## Layer taxonomy

Lane = a **pure function of `NodeType`**, never hand-assigned (Dynatrace/AppD/New-Relic invariant). Derive `LAYER_BY_NODETYPE` from the engine's existing L0-L6 groups (enums.py) rather than the stale `TIER_BY_TYPE` table. Columns stack left-to-right; the case/signal/change lanes frame the OWNER's five architectural lanes and encode the ServiceNow origin→symptom→topology→change chain.

| Lane (column) | Role | NodeTypes mapped |
|---|---|---|
| **Case** | Origin + reasoning | `incident` (origin, #1), `hypothesis` |
| **Signal** | Symptom / observation | `anomaly`, alert/metric signal nodes |
| **Change** | Candidate causes (time+CI-scoped) | `change_event`, `code_commit`, `pull_request`, `release`, `deployment`(as change), `feature_flag`, `build_artifact`, `job`/`batch_job`(as change) |
| **Service** | App tier | `service`, `application`, `component` |
| **Messaging** | Async transport | `message_queue` |
| **Database** | Datastore | `database`, `schema`, `cache` |
| **Infra** | Compute/platform | `host`, `cluster`, `namespace`, `pod`, `container`, `replicaset`, `deployment`(as workload) |
| **Network** | Wire + edge | `load_balancer`, `route`, `dns`, `proxy`, `api_gateway`, `cdn`, `waf`, `certificate`, `network_device` |

Notes: keep exactly **two edge kinds** — `calls` (horizontal peer, within a lane) and `runs_on`/`depends_on` (vertical, across lanes) — which alone reproduces Smartscape and AppD flow maps. Refresh the stale keys (`queue→message_queue`, `dns_record→dns`). Render **inferred** entities (discovered only from a caller's exit-call, à la AppD backends / NR agentless DBs) dimmed / dashed so users see what iw only knows second-hand. Reuse `TIER_ORDER→LAYER_ORDER` and `TIER_LABELS→LAYER_LABELS` so LiveGraph.tsx:100-120 layout is untouched; extend styles.css:585-601 to color every lane (the currently-uncolored `runtime`/messaging/infra included).

**Two orthogonal badges + one freshness dot, never fused** (New Relic pattern): TYPE/LAYER chip (function of type) + SOURCE chip (function of `Node.source`) + freshness dot `fresh|stale|gone` derived from `observed_at` (in the simulated incident clock for scenarios).

---

## Use-case matrix (the test plan)

≥2 detailed use cases per layer. `[E]` = existing twin (keep); `[N]` = new. Each is a fresh `e2e.scenario_<key>` with a unique catalog id; all keep the sourced/reliability-weighted/timestamped-facts + real-intent-fixtures discipline or they will not fold.

| Layer | # | Incident origin | Symptom | Topology touched | Candidate changes | Expected confirmed root cause | Tools/capabilities the LLM calls |
|---|---|---|---|---|---|---|---|
| **Network** | 1 [E] | INC-9001: MTU/uplink CHG-77 on SEG-EDGE-12 | checkout-svc→pricing-svc timeouts ~30m post-change | service→service across SEG-EDGE-12; pricing-db ruled out | CHG-77 (MTU) | Wire-level loss at segment boundary (retransmits/packet-loss, healthy callee) | prometheus fetch_metrics (RED), servicenow find_recent_changes, appd fetch_traces/bt_health |
| | 2 [N] | INC-91xx: LB target-group config change deregisters healthy backends | 503s at the edge; every backend's RED clean | load_balancer→service targets | LB config CHG | LB target-health misconfig (no retransmits, backends healthy) | prometheus fetch_metrics (LB target-health + backend RED), servicenow find_recent_changes |
| **Firewall / Security** | 1 [E] | INC-7702: ACL tightening CHG-3311 on FW-EGR-118 | fraud-scoring egress blocked; link-flap ruled out | service→egress via FW-EGR-118 | CHG-3311 (ACL) | Clean policy denies (vs packet loss); `premature_write` exercises gate block | splunk search_fw_denies/fetch_logs, servicenow query_change_log |
| | 2 [N] | INC-77xx: service→service call fails TLS; CERTIFICATE notAfter lapsed | handshake_errors climb; NO firewall denies | service→service mTLS; certificate node | none (cert expiry, no change) | Expired cert / mTLS handshake failure | splunk fetch_logs (handshake errors), certificate notAfter check; remediation = reissue (human-gated) |
| **Database** | 1 [E] | INC-7734: CHG-9 dropped index on orders.order_items | orders-api p99 latency; app regression ruled out | service→database (JDBC exit-call) | CHG-9 (index drop) | Missing index → full scans (flat p50, 200/200 pool) | appd fetch_traces (exit-call), prometheus instant_query, servicenow find_recent_changes |
| | 2 [N] | INC-77xx: long migration / batch job holds a lock | queries queue behind blocking pid; pool not exhausted | database→schema; blocking txn | migration/job CHG | Lock contention (lock_wait climbs, pool moderate) | prometheus instant_query (lock metrics), servicenow find_recent_changes; remediation = kill blocking txn |
| **Messaging** | 1 [N] | INC-MQxx: consumer deploy slows processing | consumer_lag climbs, downstream SLA breach; broker+producer healthy | message_queue→consumer service | consumer deploy CHG | Consumer-group lag localized to one group | prometheus fetch_metrics (lag/depth), ocp rollout_status, git diff_range |
| | 2 [N] | INC-MQxx: upstream schema change → poison messages | DLQ depth spikes, deserialization errors; broker+net healthy | message_queue DLQ; upstream service | schema CHG | Poison-message / DLQ flood after schema change | prometheus fetch_metrics (DLQ depth), splunk fetch_logs (deserialize errors) |
| **Infra** | 1 [N] | INC-INxx: noisy-neighbor batch_job saturates a host | tier-1 pod evicted; app workload itself healthy | host→pod; batch_job co-tenant | (platform, not app) | Node MemoryPressure eviction (noisy neighbor) | ocp events (eviction/MemoryPressure), prometheus fetch_metrics (HOST USE) |
| | 2 [N] | INC-INxx: log volume hits 100% on a node | ALL co-located pods on that node degrade together | host→multiple pods | (disk fills, no app change) | Host disk-full (disk_utilization=1.0, multi-service) | prometheus fetch_metrics (HOST disk USE), ocp events |
| **Service / App** | 1 [E] | INC-4821: NPE in TaxCalculator.java:88 after v4.12.0 (commit abc123) | payments-api 5xx; DB pool ruled out | service→database (ruled out); pods Ready | v4.12.0 deploy | Code regression NPE; `refuted_variant` forces a backtrack | appd bt_health/fetch_traces, git blame/diff_range/get_pr_for_commit, splunk error_signature_topk |
| | 2 [N] | INC-48xx: code change grows in-process cache without eviction | heap + GC-pause climb until OOM restarts | service→(heap); pod Ready between GC pauses | the cache-growth commit | JVM memory leak (pod Ready, DB pool healthy) | appd get_snapshots/fetch_traces (GC/heap), git blame/diff_range; remediation = roll back commit |
| **Deployment** | 1 [E] | INC-7731: rev43 removed ConfigMap key DB_HOST (PR #482 / 9f2a1e0) | checkout-api CrashLoopBackOff | deployment→pod→configmap; checkout-db ruled out | rev43 (config removal) | Missing config key → crash; pod NEVER Ready; `mitigated` closes MITIGATED | ocp rollout_status/pod_status/events/pod_logs, git diff_range/get_pr_for_commit |
| | 2 [N] | INC-77xx: manifest change lowered memory limit | pods restart reason=OOMKilled; flap Ready intermittently | deployment→pod resource limits | manifest limit CHG | OOMKilled from resource-limit change (not config panic) | ocp events (OOMKilled)/pod_status, prometheus mem_utilization, git diff_range |
| **Saturation / No-change** | 1 [E] | INC-9100: 3.4x organic surge, EMPTY change log | checkout-api DB-pool saturation; phantom-change rival ruled out | service→database pool | none (organic) | DB-pool saturation under surge; closes MITIGATED | prometheus range_query/fetch_metrics, servicenow find_recent_changes (empty) |
| | 2 [N] | INC-9Nxx: app-tier thread-pool saturation under surge, EMPTY change log | thread_pool_active pegs at max + CPU climbs; **DB pool healthy** | service thread-pool + host CPU | none (organic) | App-tier thread/CPU saturation (distinct from DB-pool case); MITIGATED after scaling replicas | prometheus range_query/fetch_metrics (thread-pool + host CPU) |

Discriminator discipline is what makes each pair genuinely distinct (not a variant replay): e.g. Network-2 is discriminated from Network-1 by *no retransmits*; Deployment-2 from Deployment-1 by *OOMKilled + intermittent-Ready* vs *never-Ready*; Saturation-2 from Saturation-1 by *DB pool healthy while thread-pool pegs*.

---

## Build order

Each step is independently testable and keeps pytest + goldens green (additive-optional fields, no behavior change until a surface opts in).

1. **Data-model envelope (engine, additive).** Add the optional Node/Fact/Edge fields + `Invocation.started_at/duration_ms/kind`; stamp them in `reducer.materialize`/`fold.apply_delta`; extend `NodeSpec`/`EdgeSpec`. Goldens unchanged because fields default to `None`/`False`. *Test:* existing e2e/golden suite green; a new unit test asserts `first_source`/`observed_at`/`origin` are stamped.
2. **Origin pin (#1).** `Node.origin` flag + the `nodesWithOrder` rank-0 selector. *Test:* golden asserts the incident node renders as `#1` for every scenario.
3. **LLM-live path wiring (obs 10).** `live_build_manager()` + env/key switch + move the two `run_live` hooks into `session._drive` + run `LivePlanner.plan` off the event loop. Mock `build_manager` stays default. *Test:* server boots live-backed with a key present; SSE keep-alives keep flowing (no blocking); a smoke run of one scenario converges.
4. **Per-step tool timing (obs 9).** Wrap `layer.serve` with `perf_counter`; surface `started_at`/`duration_ms`/`kind` on `capability_call`; render on `ToolCallCard`. *Test:* `capability_call` payload carries positive duration; UI snapshot shows when/how-long/tool-vs-workflow.
5. **Provenance on the SSE stream.** Add source/unit/valid_from to `GraphDeltaFact`; source/first_source/created_at/origin to `GraphDeltaNode`; stop blanking `source` (store.ts:266); fold onto `LiveNode`. *Test:* live facts show WHO without a snapshot refresh.
6. **UI surfaces (parallel-safe, each behind its own component).**
   a. Layer lanes + LAYER/SOURCE/freshness badges (obs 4/5/6) via `LAYER_BY_NODETYPE`; delete `IncidentGraph.tsx`.
   b. Hypothesis ledger expand + shared selection bus + cross-highlight (obs 8); typed `ChainLink`; carry supporting/refuting/chain on `ledger_delta`.
   c. Two-way chat: `user_message` event + composer + steering into `PlanContext` (obs 2).
   d. Approval gate as decision prompt with RECOMMENDED + "do something else" (obs 3).
   *Test:* per-component RTL/store test; selection round-trips; a user message reaches the planner's next `PlanContext`.
7. **Use-cases (obs 11).** Author the 10 new twins (2/layer incl. messaging/infra + firewall/db/app/deployment/network/saturation second cases), unique catalog ids, extend `test_scenarios.py:16` + the layer-set assertion; resolve INC-9001 collision; widen DESIGN.md:61. *Test:* every catalog id runs through the write-gate contract; layer-set assertion covers all 8 layers.
8. **Verify with Gemini/xAI (proof of obs 10).** Promote `run_live`'s converged check to a gated (key-required, skippable) live test asserting each use case converges to its expected root cause through the HTTP/SSE surface with the ScriptedPlanner absent from the factory. Scripted goldens remain always-on. *Test:* gated job green with a real key; CI job green without one.

---

## Risks / watch-items

- **LLM latency vs SSE.** `LivePlanner.plan` is 4.5-120s synchronous with backoff (live_planner.py:123-161). If not moved off the event loop it freezes the stream and breaks keep-alives. Non-blocking execution is a hard prerequisite for step 3, not a nice-to-have.
- **Nondeterminism vs goldens.** The live path is nondeterministic; goldens must stay pinned to the ScriptedPlanner. Do **not** let the live test assert exact narrative text — assert convergence (expected root cause + terminal hypothesis state + write-gate reached), per `run_live`'s converged check.
- **Live fixtures are thinner than the mock.** Today live has 3 of 6 scenarios and leaner evidence. "Proven" is hollow until live fixtures reach parity (or `ScenarioSource` is replaced by real Sources). Track parity per use case, not per scenario count.
- **`created_at` is a real plumbing gap.** `created_by` is a phase seq, not wall-clock, and the Node has no datetime today. Until the engine stamps `first_observed_at`, the drawer/badge must *approximate* from the earliest `fact.observed_at` for the node and label it as approximate — don't present a synthesized time as authoritative.
- **Freshness in a simulated clock.** Scenario `observed_at` is the *simulated* incident timeline (e.g. 14:00, 14:25), not real execution time. The `fresh|stale|gone` dot must derive from the run clock the demo actually uses, or it will read as always-stale. Keep the two clocks distinct (valid-time vs observed-time) exactly as the bitemporal research prescribes.
- **Steering changes determinism.** Feeding `_messages` into `PlanContext` (obs 2) is the load-bearing two-way change but also makes the plan input operator-dependent. Keep it out of the ScriptedPlanner/golden path; only the live planner consumes it.
- **RPM limits / cost.** Live runs throttle at `min_interval`; the gated live test over 16 use cases × 5-6 phases is many paid calls. Make it opt-in and consider a subset smoke tier for routine CI.
- **`IncidentGraph.tsx` drift.** It's unmounted but still carries old EDGE_COLORS/tier logic and a second `selectedId`. Delete it in step 6a so two badge/lane implementations don't diverge.
- **INC-9001 native-id collision** between network and nochange (disambiguated only by catalog id INC-9100). Adding second scenarios per layer requires fresh unique catalog ids or the collision compounds.
- **`ledger_delta` thinness during live runs.** Until the delta carries supporting/refuting/chain, ledger cards render empty mid-run until a `mergeDetail` snapshot backfills. Either thicken the delta (step 6b) or gate the expander on data-present so cards never render empty.
- **"Inferred vs instrumented" honesty.** If a node is only known from a caller's exit-call (AppD backend / NR agentless DB pattern), the badge must say so (dimmed/dashed) — otherwise iw over-claims first-hand knowledge it doesn't have.