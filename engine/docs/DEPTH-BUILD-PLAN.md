# Depth + Interactivity Build Plan (research synthesis)

Grounding confirmed against the actual tree (`Phase`, `NodeType`, `Origin`, `Source.HUMAN`, the 8 adapters, `engine.py:88/106/133`, `PlanContext`, the 5 workbench components all exist as the briefs describe). Producing the reconciled plan.

---

# BUILD PLAN — Investigation Workbench: Depth + Interactivity Pass

**Scope:** enrichment over the existing engine at `/Users/innamul/Project/iw/engine/`, not a rewrite. The typed closed registry, uniform `PhaseResult`, single `fold()`, journal-as-truth (R-J1), Popperian ledger, and the 8 `provider/intents/effect/normalize` adapters are load-bearing and stay. Five workstreams land in dependency order: **(1)** declarative domains (unblocks everything), **(2)** capability depth incl. ThousandEyes, **(3)** natural node-expansion, **(4)** interactive chat+approval, **(5)** live-LLM validation.

The four briefs are mutually reinforcing and non-conflicting. Two reconciliation calls up front:

- **Registry authority moves once, early.** Brief 1 needs ~7 new node/edge types; Brief 4 needs the registry to become a loaded per-domain object. **Do Brief 4's `DomainRegistry`-objectification *first*** so the new ThousandEyes/DB/PagerDuty types are authored as folder YAML in `domains/incident/`, never as new Python enum members. This collapses two "add types" efforts into one.
- **Three seams are shared infrastructure, not per-feature.** The write-gate (`engine.py:88`), the `Planner` Protocol/`PlanContext`, and `Journal.append_step` are each used by node-expansion, interactivity, AND the live planner. Build them once as the spine (Phase 2), then all three consuming features attach.

---

## A. ENRICHED CAPABILITY CATALOG

### A.1 Layer → tool matrix (the fold-shape principle)

APM, logs, and metrics are each **one fold-shape with interchangeable vendors** — you need one strong adapter per shape, not all vendors. The genuinely missing fold-shapes are four: **network-path, DB-internals, on-call, owned-network-device.**

| Layer (engine tier) | Primary tool | Fold-shape status |
|---|---|---|
| Business-txn / APM (L0/L6) | **AppDynamics** ✅ | have — reference APM fold |
| **Synthetic + network-path / BGP / internet (L4)** | **ThousandEyes ➕P0** | **NEW fold-shape — zero coverage today** |
| Infra metrics USE/RED (L1/L2) | **Prometheus** ✅ | have — reference metrics fold |
| Container / orchestration (L2) | **OpenShift/K8s** ✅ | have |
| **Database internals (L3)** | **postgres/oracle exporter ➕P1** | indirect only today (AppD exit-calls hint) |
| Logs (L6) | **Splunk** ✅ | have — reference log fold |
| ITSM + CMDB/topology (L0–L2) | **ServiceNow ITSM ✅ + CMDB ✅** | have — declared spine |
| Artifact / supply chain (L5) | **Artifactory** ✅ | have (build/test gap) |
| Source (L5) | **Git/GitHub** ✅ | have |
| **Alerting / on-call / incident command (L6)** | **PagerDuty ➕P1** | **NEW — no coverage; also the interactive-approval anchor** |
| **Owned network devices (L4)** | **SolarWinds NPM ➕P2** | **NEW — SNMP interface errors/discards** |
| CI-run + tests (L5) | **Jenkins ➕P2** | test-result evidence Artifactory lacks |
| Topology+causation cross-check | **Dynatrace ➕P3** | second declared-topology + causal-hint source |

`≈alt` (same fold, different `provider=`, no new depth, build only on demand): **Catchpoint** (=ThousandEyes fold), **ELK** (=Splunk fold), **Datadog/Dynatrace-APM** (=AppD fold), **CloudWatch/Azure/GCP** (=Prometheus fold). **Grafana is not an adapter** — it is a query surface over Prometheus/Loki/Tempo.

### A.2 New capabilities, ranked — each with intents · data · fold · nodes/edges revealed

Every capability call is **one graph-expansion move**: it answers exactly one join that grows the subgraph toward the root cause. That is what makes node-expansion (§B) natural and what makes each step human-approvable (§C).

**P0 — ThousandEyes** (`provider="thousandeyes"`, `effect=read`)
- **Intents:** `net_path_test` (agent-to-server / agent-to-agent), `bgp_route_test`, `dns_test`, `http_server_test`, `internet_insights`.
- **Data:** loss %, RTT, jitter, per-hop forwarding-loss, MPLS awareness; BGP reachability / path-changes / withdrawals / origin-AS; DNS resolution time per resolver; HTTP phase split (DNS·connect·SSL·**wait/TTFB**·receive); Internet Insights cross-customer provider/SaaS outage.
- **Localizes:** the **network-vs-app split** (HTTP wait high + connect/SSL fine ⇒ app, hand to AppD; connect/SSL/DNS high ⇒ network); hop attribution; AS attribution; multi-vantage triangulation; direction isolation.
- **Fold:** `nodes[NetworkPath, NetworkNode(hop), AutonomousSystem, Dns, ExternalService, ApiEndpoint]` · `facts[loss_pct, jitter_ms, hop_forwarding_loss, as_reachability, as_path_changes, http_connect_time, http_ssl_time, http_wait_ttfb, dns_resolution_time]` · `events[bgp_path_change, route_withdrawn, provider_outage]` · `edges TRAVERSES(discovered path→hop), ROUTED_THROUGH(discovered path→AS), ANNOUNCES(AS→prefix), AFFECTS(Anomaly→NetworkPath)`.
- **Reveals / drives expansion:** `Anomaly/Service → NetworkPath →(TRAVERSES) NetworkNode hops →(ROUTED_THROUGH) AutonomousSystem →` BGP events. HTTP test **splits the frontier** toward network vs app.

**P1 — Database-deep** (`provider="db_deep"`, postgres/oracle exporter, `effect=read`)
- **Intents:** `pg_stat_activity` / `session_stats`, `lock_waits`, `top_sql`, `replication_status`.
- **Data:** connections_used/max (pool exhaustion), blocked_sessions, lock_wait_ms, deadlock_rate, replication_lag_s, active_sessions, top-SQL by elapsed (ASH).
- **Fold:** `nodes[Database, Schema, SqlStatement(top-K by sql_id — DB analogue of ErrorSignature)]` · `facts[connections_used, connections_max, active_sessions, blocked_sessions, lock_wait_ms, replication_lag_s, deadlock_rate]` · `edges EXECUTED_ON(SqlStatement→Database)`.
- **Reveals:** `Database → SqlStatement + saturation facts`; a blocked-session chain expands into a lock-wait tree; **pool-saturation + empty change-set seeds the no-change root_candidate**.

**P1 — PagerDuty** (`provider="pagerduty"`, `effect=read`)
- **Intents:** `incident_timeline`, `alert_grouping`, `on_call_lookup`, `escalation_policy`.
- **Data:** trigger→ack→escalate→resolve timeline, which raw alerts grouped (dedup keys), responders/schedules.
- **Fold:** `nodes[Incident, Alert, Service, Responder]` · `events[triggered, acknowledged, escalated, resolved]` · `edges FIRED_ON(Alert→Service), TRIGGERED_BY(Incident→Alert), ASSIGNED_TO(Incident→Responder)`.
- **Reveals:** `Incident → grouped Alert nodes → co-firing Services` (temporal ordering = cheap causal prior); `Responder` for the human loop. **PagerDuty is the natural home of the interactive approval seam (§C).**

**P2 — SolarWinds NPM** (`provider="solarwinds"`): `if_util`, `if_errors`, `node_status`, `netflow_top_talkers` → `nodes[Host, NetworkInterface, NetworkSegment]` · `facts[if_utilization_pct, if_in_errors, if_out_discards]` · `events[node_down, interface_down]`. Localizes loss to owned devices (below ThousandEyes' internet path).

**P2 — Jenkins** (`provider="jenkins"`): `build_info`, `test_results`, `trigger_cause` → `nodes[CiBuild, PullRequest]` · `facts[test_pass, test_fail, build_result]` · `edges TRIGGERED_BY, INTRODUCED_BY`. Ties a bad artifact to its commit range + failing test.

**P3 — Dynatrace** (topology+causation, not just APM): Smartscape → `DEPENDS_ON(declared)` overlay; Davis → `CAUSED_BY(inferred)` hypothesis edge to reconcile against the ledger. No new node types.

### A.3 Registry delta (authored as folder YAML, not enum edits — see §E)

- **+nodes:** `network_path`, `network_node`, `autonomous_system`, `sql_statement`, `responder`; optional `network_interface`, `ci_build`.
- **+edges:** `traverses`, `routed_through`, `announces`, `executed_on`, `assigned_to`.
- **+fact predicates / event types** per capability above.

This keeps R-G1 closure intact (`⋃ types any adapter.normalize() emits ⊆ registry` still holds), because the closure authority becomes the loaded `DomainRegistry` and the reducer's runtime membership checks (§E), which already reject unknown types.

---

## B. NATURAL NODE-EXPANSION INVESTIGATION

**Diagnosis:** today the planner is traversal-blind (`render_slice` is a flat 3-hop BFS, unranked), INVESTIGATE is one monolithic hand-authored `PhaseResult`, and `Hypothesis.prediction` ("if true we'd also see X") is defined but unused. The fix keeps the three-authors law: **engine computes a ranked frontier (mechanics), playbook tunes the weights (WHAT/WHEN), planner picks-and-justifies (judgment).**

### B.1 New files & primitives

- `graph/graph.py` — add `dependency_neighbors(nid)` (OUT along `DEP_EDGES` = localize the fault) and `impact_neighbors(nid)` (IN along `DEP_EDGES` = blast radius). `DEP_EDGES = {depends_on, calls, reads_from, writes_to, routes_to, runs_on, hosted_on, connects_to, consumes_from, secured_by, traverses, routed_through, executed_on}`.
- `graph/frontier.py` **(new)** — the heart:
  - `redness(node, facts, tun) -> float`: saturation (active/max ≥ floor) → 1.0; `red_*` fact beyond baseline band → scaled 0..1; `NoEvidence(looked_and_clean)` present → 0.0 (proven cold).
  - `build_frontier(graph, ledger, tunables, mode) -> list[FrontierCandidate]` where
    ```
    score(n) = w_red*redness(n) + w_change*change_adjacency(n) + w_temporal*temporal_proximity(n,onset)
             + w_topo*(1/hops_from_anomaly) + w_hyp*hypothesis_pull(n)   # reads Hypothesis.predictions
             + w_blast*norm(blast_radius(n)) + w_disc*discriminates(n, leader, rival)
             - cost(n)   # expensive-intent + already-expanded penalty (reads journal/NoEvidence)
             ; each edge followed × origin_trust[edge.origin]   # declared>discovered>inferred
    ```
  - `FrontierCandidate{node_id, node_type, score, suggested_intents, why}`.

### B.2 Mode switch (breadth → hypothesis-driven, the human progression)

Engine derives `mode` from ledger state and passes it in the context:

| mode | trigger | frontier emphasis |
|---|---|---|
| `localize` | no hypothesis above `theta` | `w_red, w_change, w_topo` — walk the red dependency path |
| `test` | one clear leader, no rival | `w_hyp` — expand the leader's top **unchecked prediction** (try to refute) |
| `differential` | ≥2 hypotheses alive above floor | `w_disc` — expand the node that best splits them (the "crucial experiment") |

### B.3 The loop (reuses the existing REPEAT re-entry — `controller.py`)

Each INVESTIGATE re-entry = **one expansion step**:
```
ctx.frontier = build_frontier(graph, ledger, tunables, mode)   # engine mechanics
plan = planner.plan(ctx)     # PICK one candidate + emit typed expansion ops (AddFact/AddEdge/UpdateHypothesis/NoEvidence)
fold(plan)                   # one fold; mark predictions checked/held
verdict: advance  if promotion_ok(ledger)                       # → REMEDIATE
         repeat   if frontier non-empty AND budget>0
         backtrack if leader refuted AND rivals exhausted        # → HYPOTHESIZE
         blocked  if budget/time-box exhausted                   # → escalate/human
```

### B.4 Static-vs-dynamic CI expansion (via existing `Origin`)

Expansion policy is a function of origin — no new node/edge types needed:
- **declared** (CMDB/IaC): expand early & cheap (FRAME/TRIAGE seed 1–2 hops for fast blast radius). Trusted as *identity*, not *currency*.
- **discovered** (traces/telemetry): expand on demand. **Trigger:** red node `R` whose declared providers are all clean but `R` still red ⇒ emit new intent `discover_runtime_deps(R, window)` on appd/prometheus → fold `discovered` edges; a new provider jumps to the frontier top (redness × novelty).
- **inferred** (LLM/causal): never expanded as fact, only as a *test target* for the differential.

**Drift = first-class signal** (two reconciliation facts): `declared_no_runtime_traffic` (stale edge → down-weight) and `undeclared_runtime_dependency` (shadow dependency → surface high; classic incident cause).

### B.5 Stop conditions (conditions on graph+ledger, not script length)

Extend `controller.check_gate` + a `stop:` tunables block:
1. **Cause confirmed & differentiated** → `promotion_ok` (exists) → `advance` to REMEDIATE.
2. **Follow-the-red dead-ends** → frontier best < `stop.score_floor` and best candidate is proven-cold → fault boundary.
3. **Differential exhausted** → all rivals REFUTED, one leader SUPPORTED.
4. **Budget** → `expand_budget[investigate]` spent → `blocked`.
5. **Severity time-box** → wall/step past `stop.time_box_s[severity]` → mitigate-first.
6. **Actionable cause (5-Whys terminal)** → `root_candidate` ∈ `terminal_kinds` (change/config/flag, never a person).
7. **Impact mitigated, cause unproven** → `CloseOutcome.MITIGATED` (exists) — legitimate close.
8. **Needs a human** → approval gate (§C).

### B.6 Works identically for Scripted and Live planners

`PlanContext` gains `frontier: list[FrontierCandidate]` and `mode: str`; the `Planner` Protocol signature is **unchanged**. The `ScriptedPlanner` (tests) picks by a deterministic rule ("top candidate matching a scripted node_id") → all 12 e2e scenarios still run hermetically. The Live LLM planner picks-and-justifies from the same frontier (§D). The scripts in `tests/e2e/scenario_*.py` change from "pre-baked path" to "pick from frontier"; add a **shadow-dependency** scenario to exercise B.4.

---

## C. INTERACTIVE CHAT + APPROVAL

The engine is batch today (`Engine.run()` synchronous while-loop → `export_bundle` → one static JSON → read-only `App.tsx`). Four seams the interactive layer needs **already exist**: the write-gate branch (`engine.py:88` + `layer.invoke` block), the `Planner`/`PlanContext` (operator-intent home), `Journal.append_step` (defined, unused — the stream payload), and journal replay/crash-safe persistence (the durable session, no new truth store). This is pulling DESIGN §2.6's deferred "sessions/the pen/earned-autonomy" forward as a **driver + transport around the same deterministic fold.**

### C.1 Interaction model

- **Session lifecycle:** `CREATED → RUNNING → SUSPENDED{gate|ask|idle} → RUNNING → … → CLOSED`. **Session id = investigation identity; state reconstructable by journal replay** (journal *is* the checkpointer, session id *is* the thread).
- **Three ways a human participates in a turn:**
  1. **Ask/command (read):** "why H1?", "did you check the DB?" — answered *from the board* (graph/ledger/journal), a read not a mutation.
  2. **Steer:** operator message → steering buffer → injected at the next **phase boundary** (v1) into the next `PlanContext.operator_directives`. `queue` vs `steer` surfaced in UI.
  3. **Answer a gate:** approve / refine / deny a proposed write.
- **Write-gate approval (centerpiece, maps 1:1 to LangChain approve/edit/reject):** when a phase's plan contains a WRITE-effect call, the engine does **not** execute — it materializes a `PendingAction`, SUSPENDS, emits `gate_opened` carrying {proposed action + human-readable summary, serving hypothesis + evidence chain, reversibility/blast-radius}. Operator: **approve** → `layer.invoke(..., allow_write=True)`, fold, resume · **refine** → edit params, then execute · **deny** → journal denial as synthetic result that re-enters the planner as feedback (replan). Every decision journaled with `Source.HUMAN` + approver.
- **"Why?" is a deterministic read over recorded provenance** (cannot hallucinate): hypothesis → `supporting_facts`/`refuting_facts`/`causal_chain` → each fact's `source`+`source_reliability`+`evidence[]` → journal seq → the `capability_call` that produced it.

### C.2 Backend session API

New `runtime/session.py` (`InvestigationSession` state machine + steering buffer + event bus) and `api/server.py` (FastAPI). Refactor the `run()` while-loop into a re-enterable `Engine.step()` the session drives.

| Endpoint | Purpose |
|---|---|
| `POST /sessions` | start — `{subject, domain_id, autonomy}` → `session_id` + snapshot; runs to first pause |
| `POST /sessions/{id}/messages` | send-message — classify → question(read) / steer(buffer) / command |
| `POST /sessions/{id}/advance` | step forward one phase / until next pause |
| `POST /sessions/{id}/gate` | answer-gate — `{gate_id, decision: approve\|refine\|deny, edited_params?, reason?}` |
| `POST /sessions/{id}/answer` | answer a clarifying `ask_human` |
| `GET /sessions/{id}/events` | **stream** — SSE, resumable via `Last-Event-ID` = journal seq |
| `GET /sessions/{id}` | snapshot (existing `export_bundle`) for cold-load / reconnect |

**Event stream (every event is a delta the engine already recorded):** `phase_started`, `phase_verdict`, `reasoning_delta` (`PlanOutput.narrative`, token-streamed with a live planner), `capability_call`/`capability_result` (= `append_step` shape, now live), `graph_delta`, `ledger_delta`, `gate_opened`/`ask_human`, `session_state`. Transport: **SSE-down + POST-up** for v1 (proxy-friendly, journal-resumable); upgrade to WebSocket only for sub-phase token steering.

**Autonomy setting:** `read_only` (propose only) · `gate_all` (default) · `gate_prod_only` — the earned-autonomy dial from §2.6.

**Determinism kept:** `ScriptedPlanner` + a scripted operator that approves-everything ⇒ session-run journal == today's batch-run journal. New interactive e2e: reach REMEDIATE → suspend → scripted **deny** → planner branch → different journal (asserts the gate re-enters feedback).

### C.3 React chat-workbench

Reuse the 4 existing panes (`IncidentGraph`, `HypothesisLedger`, `JournalTimeline`, `PhaseController`) — now live, delta-updating. Layout: **chat pane** (primary) + **live board** + **phase/approval rail**.
- **Chat pane:** operator messages + streamed agent turn (reasoning text, collapsible capability-call cards, hypothesis updates); phase-adaptive suggested prompts.
- **Approval card** (inline + rail banner on `gate_opened`): proposed action, **editable params (refine)**, serving hypothesis + evidence chain, reversibility → Approve/Refine/Deny + reason box.
- **"Why?" drill-down:** click any hypothesis/fact/edge → provenance panel (pure read over the bundle).
- **Live transport:** replace one-shot `fetch` in `App.tsx` with `GET /sessions/{id}` snapshot + `EventSource('/sessions/{id}/events')` into a **client store whose reducer mirrors the engine's stores**; reconnect from `Last-Event-ID`.
- **Replay/scrub:** because the journal is truth, scrub to any seq and re-project the board (time-travel).
- `types.ts` extends `InvestigationBundle` (now "snapshot") with a `SessionEvent` union + `PendingGate`/`AskHuman`.

---

## D. LIVE LLM PLANNER (xAI / Gemini)

The live planner sits behind the **unchanged `Planner` Protocol** — `plan(ctx: PlanContext) -> PlanOutput`. Same seam as `ScriptedPlanner`, so nothing downstream (fold, ledger, controller) knows the difference.

### D.1 What it emits (typed ops only, never prose-as-action)

- **Pick + justify:** one `FrontierCandidate` (normally top-ranked; a lower pick requires a stated discrimination reason).
- **Intents:** only from `allowed_intents`.
- **Typed operations** against the closed registry: `AddFact`/`AddEdge` (evidence), `UpdateHypothesis(add_supporting|add_refuting, basis=…)`, mark `Prediction` checked/held, or `NoEvidence(looked_and_clean{intent, scope})`.
- **Narrative:** `PlanOutput.narrative` (streamed as `reasoning_delta`).
- **Verdict:** `{advance|repeat|backtrack|blocked}` + one-line basis; `advance` only when the leader crosses the confidence gate and no rival survives.

### D.2 Prompt shape (implementable now)

Stable SYSTEM: "You are the SME reasoner. You do NOT invent topology or traversal. Each turn the engine gives a RANKED FRONTIER with WHY each scored high. (1) Pick ONE candidate; name your pick + reason. (2) Choose intents only from allowed_intents. (3) Emit only typed ops against the closed registry. (4) In `differential` mode prefer the expansion that could REFUTE the leader or a rival. (5) Emit a verdict + basis." Per-turn TURN block carries `{incident, mode, leading_hypothesis + unchecked_predictions, rivals, FRONTIER (ranked, with why + suggested_intents), allowed_intents, budget_remaining}`.

**Provider:** put xAI (grok) and Gemini behind one `LlmPlanner` class with a pluggable `LlmClient`. Both support structured/JSON output → constrain ops to the registry-derived JSON schema (§E `render_allowed_types` emits the `enum` for `AddNode.type`/`AddEdge.type`). A malformed/off-registry op is rejected by the reducer (existing membership checks) and fed back as a repair turn.

### D.3 Validating the reasoning from all angles (assert on live runs)

Because the fold is deterministic and the registry closed, a live run is verifiable even though the tokens are not:
1. **Closure:** every emitted op's node/edge/predicate ∈ loaded registry (reducer rejects otherwise — assert zero rejections on a good run).
2. **Grounded pick:** the chosen candidate exists in the frontier the engine handed it (no invented nodes).
3. **Discrimination discipline:** in `differential` mode, the expansion attaches evidence bearing on leader or a named rival (not an unrelated node).
4. **Refutation happened:** a CONFIRMED verdict has ≥1 rival REFUTED with `refuting_facts` (R — `require_refutation` gate).
5. **Provenance completeness:** every `UpdateHypothesis` carries a `basis`; every AddFact carries `source`+`evidence`.
6. **Prediction hygiene:** predictions the planner claimed to test are marked checked/held.
7. **Verdict legality:** `advance` only when `promotion_ok`; `backtrack` only when leader refuted.
8. **Path convergence (behavioral oracle):** on the 6 seeded scenarios the live planner reaches the same `root_candidate` as the golden bundle (allow different intermediate paths — assert the *conclusion*, and that the causal_chain terminates on the seeded change/CI). This is the "reasoning path" test philosophy already in `scenario_code_regression.py`.
9. **No-hallucinated-topology:** no `AddEdge` whose endpoints weren't already nodes (or freshly discovered via a capability call in the same step).

Run these as a `tests/live/` suite gated behind an API-key env flag (skipped in hermetic CI); the deterministic e2e suite stays the primary gate.

---

## E. DECLARATIVE DOMAIN ONBOARDING

**Diagnosis:** the engine is **4 constants away from generic.** Only four domain constants are baked into engine *mechanics* — `engine.py:88` (`phase==REMEDIATE`), `engine.py:106` (`NodeType.ANOMALY`), `engine.py:133` (`Phase.CLOSE`), `reducer.py:80` (`NodeType.HYPOTHESIS`). Everything else referencing `NodeType`/`EdgeType` is adapter/spec *data*. Only three enums are domain vocabulary (`NodeType`, `EdgeType`, `Phase`); the rest (`Source`, `Origin`, `ConfidenceLevel`, `FactState`, `VerdictStatus`, `GateResult`, `HypothesisStatus`, `ChainRole`…) are mechanics vocab and **stay engine-owned**.

### E.1 Folder schema — `domains/<domain>/` (sibling to `src/`)

```
domains/incident/
  domain.yaml            # MANIFEST: id, version, schema_version, extends, role_bindings, includes
  playbook.md            # PLAYBOOK: prose + fenced ```yaml (phases, gates, tunables)
  nodes/*.yaml           # NodeSpec-shaped: type, identity_keys, static_props, fact_predicates,
                         #   event_types, discriminator (per tier file)
  edges/*.yaml           # EdgeSpec-shaped: type, allowed[src->dst], default_origin,
                         #   requires_confidence, semantics
  capabilities/*.yaml    # per provider: adapter code-ref, per-intent {phases, effect, params,
                         #   emits, requires_approval}
  adapters/*.py          # existing normalize() code, bound by reference (or *.map.yaml DSL)
  fixtures/<scenario>/<intent>.json
```

The **`role_bindings`** block in `domain.yaml` is the only domain semantics the engine needs:
```yaml
role_bindings:
  symptom_node:     anomaly        # retires engine.py:106
  hypothesis_node:  hypothesis     # retires reducer.py:80
  write_gate_phases: [remediate]   # retires engine.py:88
  terminal_phases:  [close]        # retires engine.py:133
```
`requires_approval: true` on a capability intent (e.g. `apply_mitigation`) is the **declarative** approval gate (§C) — replacing `phase==REMEDIATE` in Python, grounded in the same policy-as-code move as OPA/Backstage/OCSF/Weaver/XSOAR.

### E.2 Loader — `runtime/domain_loader.py`

`load_domain(path) -> LoadedDomain{id, version, registry: DomainRegistry, playbook, capabilities: CapabilityLayer, role_bindings, fixtures_root}`. Pipeline (Weaver Model→Reference→Documentation, each step a validation gate naming file+line):
1. **Manifest** — schema_version check, resolve `extends` (deep-merge base vocab) + `includes` globs.
2. **Registry build** — parse `nodes/*`+`edges/*` into a **`DomainRegistry` instance** carrying `node_specs`/`edge_specs` + the methods that are today `registry.py` free functions (`node_id`, `edge_allowed`, `predicate_allowed`, …).
3. **Registry validation** — edge pairs reference declared nodes (closure); role_bindings reference declared types/phases; `identity_keys ⊆ static_props`; `requires_confidence` only on `inferred` edges.
4. **Playbook parse** — extract fenced yaml from `playbook.md`, `Playbook.model_validate`; cross-check `allowed_intents ⊆ declared capability intents`, `on_verdict` targets ⊆ declared phases.
5. **Capability bindings** — resolve `adapter: module::Class` refs or compile `mapping:` DSL; build intent→binding table with phase/effect/approval metadata `CapabilityLayer` lacks today.
6. **Documentation** — `render_allowed_types(registry) -> str` emits the LLM system-prompt "allowed types" section + JSON-schema enums for `AddNode.type`/`AddEdge.type` — so a new domain's vocab reaches the model with **zero code change** (this is what makes "drop a folder" real, and feeds §D).

Engine construction: `Engine(dom.playbook, planner, registry=dom.registry, roles=dom.role_bindings, layer=dom.capabilities, source=…)`. The three engine constants become `phase in roles.write_gate_phases`, `roles.symptom_node`, `phase in roles.terminal_phases`; reducer's `NodeType.HYPOTHESIS` → `roles.hypothesis_node`.

**Crux:** closed-vocabulary authority moves from the Python type system to the loaded registry + reducer's existing runtime membership checks. `Node.type`/`Edge.type`/`AddNode.type`/`AddEdge.type`: `NodeType`/`EdgeType` → `str` (enforcement already lives in the reducer). Keep the enums as string constants for existing adapters/tests (`NodeType.SERVICE == "service"`) and as the optional single-domain codegen target.

### E.3 Staged refactor (12 hermetic e2e tests green at every step)

- **Stage 0 — Characterize:** snapshot `export_bundle` for all 6 scenarios (happy+refuted) as golden JSON — the equivalence oracle.
- **Stage 1 — `DomainRegistry` object** (wrap module globals; thread through `materialize`/`Engine`). Low risk.
- **Stage 2 — Relax type binding** (`NodeType`/`EdgeType` → `str`; reducer enforces). Medium.
- **Stage 3 — Externalize the 4 role bindings + Phase→str.** Populate incident's bindings to current values; golden bundles unchanged. Medium — "engine becomes generic."
- **Stage 4 — Build loader + author `domains/incident/`** by mechanical 1:1 translation of the Python catalogs + `incident.yaml`→`playbook.md` + adapter intents→`capabilities/*.yaml`. Add equivalence test: loaded registry spec-equal to Python-built. Low.
- **Stage 5 — Flip source of truth:** `_helpers.py` uses `load_domain("domains/incident")`; move Python catalogs to `legacy/`. Golden bundles must match Stage 0.
- **Stage 6 — Prove multi-domain:** add a thin second domain (`domains/data-quality/` with `symptom_node: rule_violation`, `hypothesis_node: root_candidate`) + one e2e, loaded by the same engine binary. Green here = "onboard by dropping a folder" demonstrated.

---

## F. BUILD ORDER

Phased so each is independently verifiable and the 12 e2e tests stay green throughout.

**Phase 0 — Golden oracle** (½ day). Stage 0 snapshots. Nothing else proceeds without this safety net.

**Phase 1 — Declarative domains** (Stages 1–5 of §E). *Do this first* — it is the prerequisite that turns "add 7 node types" from enum surgery into folder authoring, and gives §D its registry-derived JSON schema. **Verify:** golden bundles byte-identical; `load_domain("domains/incident")` equivalence test passes; all e2e green folder-loaded.

**Phase 2 — Node-expansion frontier** (§B). `graph/frontier.py`, directed neighbors, `render_slice` returns `frontier`+`mode`, `PlanContext` extension, INVESTIGATE-as-per-step-expansion, stop-condition gates, `discover_runtime_deps` intent + drift facts, `frontier_weights`/`origin_trust`/`expand_budget`/`stop` tunables in `playbook.md`. Scripts become "pick from frontier"; add shadow-dep scenario. **Verify:** e2e reach same conclusions via the loop, not a pre-baked path; shadow-dep test exercises §B.4.

**Phase 3 — Capability depth** (§A). Author the new fold types in `domains/incident/nodes|edges/*.yaml`; add adapters **ThousandEyes (P0) → DB-deep (P1) → PagerDuty (P1) → SolarWinds/Jenkins (P2)**, each with fixtures + a scenario that exercises its join. **Verify:** a network scenario localizes to a hop/AS; a DB scenario reaches an offending `SqlStatement`/lock; PagerDuty timeline seeds co-firing services.

**Phase 4 — Interactive session + approval** (§C). `Engine.run` → `Engine.step`; suspend at the WRITE branch; `runtime/session.py`; `api/server.py` (7 endpoints, SSE); emit `append_step`+store deltas on the bus; autonomy setting; record human approver. **Verify:** scripted-approve session journal == batch journal; scripted-deny produces a divergent journal with planner feedback.

**Phase 5 — React chat-workbench** (§C.3). SSE client + mirrored reducer, chat pane, approval card (approve/refine/deny), why-drill-down, live board, replay scrub. **Verify:** drive a full gated investigation in-browser; deny→replan visible; "why?" renders recorded provenance.

**Phase 6 — Live LLM planner + validation** (§D). `LlmPlanner` behind the Protocol (xAI + Gemini clients), registry-schema-constrained ops, repair loop; `tests/live/` assertion suite (D.3, key-gated). **Verify:** on the 6 scenarios the live planner converges to the golden `root_candidate` with clean closure/refutation/provenance assertions.

**Phase 7 — Second domain** (Stage 6). `domains/data-quality/` + one e2e on the same binary. **Verify:** multi-domain claim demonstrated, not asserted.

---

## G. KEEP vs CHANGE vs ADD

**KEEP unchanged (load-bearing, designed for exactly this):**
- Closed typed registry as a *concept* + the reducer's runtime membership checks (`reducer.py:73–149`) — become the closure authority.
- Single `fold()`, uniform `PhaseResult`, journal-as-truth (R-J1), full-delta replay, crash-safe persistence (R-J4).
- Popperian ledger (`promotion_ok`, `require_confidence_gate`, `require_refutation`).
- The re-enterable phase machine + `controller.py` routing authority (R-C1).
- `Origin{declared,discovered,inferred}`, `Prediction`, `NoEvidence`, `CloseOutcome.MITIGATED`, `Source.HUMAN` — all already present and used by the new features.
- The 8 adapters' `provider/intents/effect/normalize` shape and `CapabilityLayer.invoke` write-block.
- The 4 workbench panes (reused live).
- `Planner` Protocol signature — unchanged; both new planners fit it.

**CHANGE (surgical):**
- `NodeType`/`EdgeType`/`Phase` enums → string vocab authored in folders; enforcement authority relocates to the loaded `DomainRegistry` + reducer (enums survive as string constants / codegen target).
- The **4 hardcoded engine constants** → `role_bindings` lookups (`engine.py:88/106/133`, `reducer.py:80`).
- Module-global `NODE_SPECS`/`EDGE_SPECS` + `registry.py` free functions → a per-domain `DomainRegistry` instance.
- `Engine.run()` batch while-loop → re-enterable `Engine.step()` driven by a session.
- INVESTIGATE: one monolithic authored `PhaseResult` → per-step expansion over a scored frontier.
- `render_slice` flat BFS → ranked `frontier` + `mode` + full cause-path.
- `PlanContext` gains `frontier`, `mode`, `operator_directives`.
- WRITE branch: auto-execute → suspend + `gate_opened` + `PendingAction` (interactive mode).
- `App.tsx` one-shot `fetch` → SSE + mirrored client reducer.

**ADD (new files):**
- `graph/frontier.py`, directed neighbors in `graph/graph.py`.
- `runtime/domain_loader.py`, `runtime/session.py`, `api/server.py`.
- `domains/incident/` (folder-authored port) + `domains/data-quality/` (proof).
- Capability adapters: `thousandeyes.py`, `db_deep.py`, `pagerduty.py`, `solarwinds.py`, `jenkins.py` (+ `dynatrace.py` P3), each with fixtures.
- `LlmPlanner` (+ xAI/Gemini clients), `render_allowed_types` generator.
- Workbench: chat pane, approval card, why-drill-down, SSE client store, replay scrub; `types.ts` `SessionEvent`/`PendingGate`/`AskHuman`.
- Tests: `tests/live/` (live-planner assertions), interactive deny-branch e2e, shadow-dependency scenario, per-capability scenarios, domain-equivalence test.

**Net:** the depth was latent in the model. The engine stops *containing* the incident domain and starts *loading* it; computes a ranked frontier and lets the planner pick-and-justify; puts a human in the write-gate that already existed; and validates a real LLM's reasoning against a deterministic fold. Every heavier real-world system (Backstage kinds, OCSF classes, XSOAR packs, Weaver models, LangGraph HITL, PagerDuty/Datadog approval gates) is built on exactly these moves.