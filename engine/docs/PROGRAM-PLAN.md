# Investigation Workbench — Master Program Checklist

> Durable state. Survives interrupts (which kill background workflows) + compaction. On resume:
> read this, run `pytest` to confirm green, then continue the first unchecked item.
> Rule: keep GREEN + goldens consistent at every step; elegant + composable; verify completeness.
> Report to owner ONLY when everything below is checked and verified.

## DONE (verified green — 67 tests, ruff clean)
- [x] Core engine (typed registry, uniform PhaseResult, fold monopoly, journal-as-truth, ledger, 8 adapters, 6 scenarios)
- [x] Refactor A1 apply_delta / A2 role-bindings / A3 resumable stepper
- [x] Reasoning gap 1 belief/gate · gap 3 gate-feedback · gap 5 full-graph context
- [x] Domain P0s: unify evidence addressing (Fact = one evidence unit; SUPPORTS/REFUTES derived; dropped EVIDENCE_FOR/AGAINST) · edge+event lifecycle (state/valid_to/invalidated_by) · belief-channel validator
- [x] Capability MCP re-seam: Binding enum + Mock/Mcp/Rest/Routed sources + gate-first serve() — **MOCK PRESERVED (do NOT replace with real vendor)**
- [x] Golden oracle (equivalence net) + live_planner.py + catalog.py + validation verdict
- [x] Graph-model refinement PLAN written (app-topology + networking) — docs/GRAPH-MODEL-REFINEMENT.md

## TODO — complete the KILLED in-flight work FIRST (owner: "complete the previous properly")
- [x] **Live convergence** (reasoning gaps 2+4) ✓: live-only fixtures with real content (guarded GitAdapter content fold — diff `DROP INDEX` line + blame file:line as facts; hermetic goldens byte-identical) + `ScenarioSource` intent→provider resolution + LivePlanner reject/repair guards (belief-channel, illegal-predicate, hyp-subject fact, id-canon). scripts/run_live.py runs Gemini flash-lite → **2/3 converge** (code_regression, database: golden root · 0 rejections · refuted rival, reproducible); network reaches the golden root at 0 rejections but refutation is flaky under flash-lite. 81 green, ruff clean.
- [x] **Interactive session backend** ✓: runtime/session.py (InvestigationSession + SessionManager; gate-suspend BEFORE apply via _GatePlanner composition — no engine edits; approve/refine/deny; event stream w/ created_by; snapshot; list incl closed) + api/server.py (POST /sessions,/advance,/gate,/messages; GET /events poll, /stream SSE, /sessions list, /sessions/{id}) + test_session.py (4 tests). 71 green, ruff clean.
- [x] live-converge: confirmed ≥2/3 via a real run of scripts/run_live.py (gemini-flash-lite-latest), reproducible across consecutive runs.

## graph-model refinements (into the shared CORE registry)
- [x] App-topology: CALLS→EXTERNAL_SERVICE (discovered) ✓; EdgeSpec.fact_predicates field + CALLS edge-RED ✓; EXTERNAL_SERVICE call_rate/error_rate facts ✓ (67 green). [remaining nicety: reducer enforce edge-predicate dispatch — optional]
- [x] Networking/security ✓: added L4 proxy·api_gateway·cdn·waf (43 nodes); widened ROUTES_TO/SECURED_BY(+waf,+certificate)/EXPOSES/CONNECTS_TO/FIRED_ON/CHANGED_BY/CAUSED_BY (0 new edge types, still 34); folded SG→firewall_rule + vpn→network_segment(+2 events); fixed LB/DNS/route→CAUSED_BY/CHANGED_BY; deferred router/switch/eni/bgp. 67 green, goldens unchanged.

## TODO — node-expansion + playbook-per-domain (needed by the UI spec)
- [ ] Node-expansion frontier (planner picks the next node to expand — ENGINE-driven, not human).
- [ ] **Playbook-per-domain (owner revision f1d24d6a + update):** BOTH the node/edge/entity **registry** AND the **capabilities** stay **SHARED IN THE CORE** (defined once, used by every domain). A domain is ONLY a **simple playbook markdown** — `playbooks/<domain>.md`, authored like a `SKILL.md`: lean, human-readable, naming the phases/goals/gates/allowed-intents. Domain selector = pick a playbook `.md`. **NO** folder-externalized registry, **NO** registry-as-instance, **NO** per-domain capabilities. **Simplicity + elegance is the bar for the whole design.** Graph-model refinements go INTO the shared core registry.

## Interactive UI to workbench/UI-SPEC.md — BUILT + browser-verified
- [x] Start screen (6 layer cards), chat pane w/ collapsible tool-call cards, approve/refine/deny gate card, live graph w/ created_by badges + click-detail + zoom, journal, incident list incl closed, SSE transport. scenarios.py registry + remediation WRITE adapter + /catalog. 89 pytest + 7 npm green. Drove INC-4821 → resolved in browser. [Depth refinements below: numbering-correctness, PAN, journal-depth display, phase-greying — handled in DEPTH pass.]

## TODO — capability registry (design; MOCK STAYS)
- [ ] A clean capability registry declaring each capability: provider · intents · binding(MCP/REST/A2A) · effect · integration-needs (keys/endpoints/config). Real-vendor implementation DEFERRED (mock preserved).

## DEPTH pass (owner feedback e84b0e48 — owner REVIEWS before docs) — DONE + browser-verified
- [x] **Rich, realistic mock data:** each node gets MANY facts like a real tool pull — full RED/USE (rate/errors/latency p50/p95/p99, cpu/mem/disk/net util+saturation), availability, throughput, performance-degradation, error signatures, connection-pool, queue depth. 6 scenario fixtures enriched. VERIFIED: INC-7734 FRAME alone = 12 facts across 5 sources (prometheus/servicenow/appd/git/cmdb).
- [x] **Related incidents:** SIMILAR_TO / RECURRENCE_OF (Incident→Incident) in the core registry; `list_related_incidents` (ServiceNow) surfaces similar incidents; fed into HYPOTHESIZE as a prior. VERIFIED: INC-7735/7736/7737 (Similar 90/90/60%) in-graph + panel + HYPOTHESIZE reasoning.
- [x] **Journal depth:** step-level entries during a phase + **who approved** (Source.HUMAN + approver) on gate decisions; UI journal shows the sequence. VERIFIED at data layer: journal[4] actor='operator' source='human'; postmortem/timeline folded at CLOSE.
- [x] **Graph fixes (UI):** dense creation-order badges (VERIFIED 1..14, anomaly=#1 marked ⭑ENTRY·SYMPTOM); zoom + pan/scroll ("drag to pan · scroll to zoom" + −/+/fit); tier layers (Change/Logical/Data/Runtime/Signal) shown.
- [x] **Verify depth end-to-end** — drove INC-7734 to CLOSED/RESOLVED in the browser (rich facts, related incidents, deep journal, CONFIRMED root cause). All 6 incidents drive 7-phase to terminal via backend (5 resolved, INC-9100 correctly *mitigated*). 91 pytest green, ruff clean. **← OWNER REVIEWS HERE (before docs).**

## DEEPEN pass (owner obs 2026-07-19/20 — FULL AUTONOMOUS, no checkpoints; design in docs/DEEPEN-DESIGN.md)
> Grounding workflow verdict: **the demo depth was 100% SCRIPTED — no LLM ran behind the workbench.** `LivePlanner` (Gemini/xAI) is real but wired only to `scripts/run_live.py`. Spine = make the LIVE path the product, demote mock to CI-only. Owner obs 1-11 mapped in docs/DEEPEN-DESIGN.md. Build order (each step keeps pytest+goldens green; regen goldens intentionally when export_bundle changes):
- [x] **E1 data-model envelope (additive):** Edge.source/valid_from/observed_at; Fact.where; Invocation.started_at/duration_ms/kind. Optional/defaulted → goldens unchanged. ✓ 91 green.
- [x] **E2 per-step tool timing (obs 9):** engine._run_phase wraps serve() w/ perf_counter (wall-clock, ephemeral); `capability_call` SSE carries kind/started_at/duration_ms; ToolCallCard renders kind (tool/workflow) + when + duration. ✓
- [x] **E3 provenance projection (obs 5/6/7):** `_node_provenance` in bundle.py (source/first_source/first_seen from earliest fact) + origin flag; edge source/established; SSE graph_delta facts carry source/observed_at/unit/where, nodes carry origin. Goldens regenerated. ✓
- [x] **E4 origin = node #1 (obs 1):** export_bundle + SSE emit `origin` on the subject incident (verified: 7734=True, related 7735/36/37=False); frontend nodesWithOrder ranks origin #1. ✓
- [x] **E5 two-way chat (obs 2):** add_message → `user_message` event (Source.HUMAN) + journal step; _GatePlanner injects _messages into PlanContext.messages → LIVE prompt "OPERATOR STEERING"; ChatPane composer + interleaved user bubbles; api.sendMessage + hook.send. ✓
- [x] **E6 LLM-live path = the product (obs 10):** `live_build_manager()` (LivePlanner + ScenarioSource over shared live_fixtures.py) + IW_LIVE env switch in create_server; session sets planner.graph + phase-scopes source.phase; background-drive thread (non-blocking SSE) + session_error path. Mock build_manager stays default CI net. **Repeat-cap wired (max_retries) — fixed the TRIAGE infinite-loop; 429 retry honors Retry-After; default model flash-lite.** ✓ (live convergence proof: pending fresh quota — see verify item)
- [x] **UI surfaces (obs 3/4/8):** (a) architectural layer lanes (tiers.ts LAYER taxonomy Case→Signal→Service→Messaging→Database→Infra→Network→Change) + on-node LAYER/SOURCE badges + provenance drawer; (b) HypothesisLedger expand → supporting/refuting facts + causal_chain, each clickable → shared selection cross-highlights node+fact in graph; (c) ApprovalCard decision-prompt (ask · RECOMMENDED · refine · deny · free-text "do something else"). Workbench build+9 tests green. ✓
- [~] **Use-cases (obs 11): ≥2 per layer** — coverage expanded 6→**8 architectural layers**: added scenario_messaging (INC-8801, consumer-deploy lag, H1 confirmed/H2 refuted) + scenario_infra (INC-8900, noisy-neighbor host eviction) — the 2 genuinely-missing layers. Both fold clean (0 rejections, resolved), goldened, catalog+tests updated. 95 green. **REMAINING for strict ≥2/layer:** author the 2nd case for each of the 8 layers (matrix designed in DEEPEN-DESIGN.md § Use-case matrix — DB lock-contention, LB target-health, cert-expiry, OOMKilled, JVM-leak, thread-pool saturation, DLQ-poison, host-disk-full). Pattern proven + fast; live fixtures for messaging/infra pending too.
- [x] **Verify with the LLM (PROOF of obs 10) — DONE via xAI grok-4.5 (latest model):**
  - Batch (`scripts/run_live.py --scenario all --model grok-4.5`): **3/3 CONVERGED** — code_regression→code_commit:abc123, database→change_event:chg-9, network→network_segment:seg-edge-12; each = golden root MATCH, **0 rejections, 0 repairs, rival refuted**. (Gemini free-tier was quota-exhausted; xAI key from AssetOne .env.)
  - Interactive (IW_LIVE=1, IW_LIVE_MODEL=grok-4.5, through HTTP/SSE + browser): grok-4.5 drove INC-7734 FRAME→…→REMEDIATE, wrote its OWN hypotheses + tool choices + a concrete SQL remediation (`CREATE INDEX … -- reverse CHG-9`), **opened the human write-gate**, suspended; operator Approve → applied + verified + closed resolved. Fully LLM-driven, ScriptedPlanner absent.
  - **Live-gate fix landed:** render_tools/tool_intents gained `include_writes` so the live LLM sees `apply_remediation [WRITE — human-gated]` and can propose it (mock injects it; without this the live path silently self-remediated, skipping the human gate). Default model updated grok-3→grok-4.5 (grok-3 retired); IW_LIVE_MODEL env override. 95 green, ruff clean.
- [ ] FINAL: pytest green · ruff clean · goldens consistent · workbench build+test · browser-verified live (Gemini) end-to-end · report to owner.

## TODO — 3 end docs (HTML, under docs/, produced LAST after all above + owner review)
- [ ] Doc 1: Design — Architecture + Data Model.
- [ ] Doc 2: Incident Use Cases.
- [ ] Doc 3: Capability Integration Guide (each capability + its integration + what to request from the external team).

## FINAL
- [ ] Full pytest green · ruff clean · goldens consistent · workbench build+test · UI verified in browser · completeness re-checked against every line here.
