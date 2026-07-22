# Graph-Model Refinement — app topology (market-validated)

> Networking/comprehensive-domain refinements appended when that track lands.

# GRAPH-MODEL REFINEMENT PLAN — Incident Topology

## A. VERDICT

**Our topology model is correct and market-aligned. The ingress spine is right; the one real defect is on the egress/downstream side, plus edge-fact governance and a couple of trims.**

Across AppDynamics, Dynatrace, Datadog, ServiceNow CMDB, and OpenTelemetry, the market converges on one shape: a **logical service node → endpoint sub-node → directed dependency edge → downstream backend inferred from the outbound call**, with declared and discovered topology reconciled into one graph. Our model already implements this spine with primitives most vendors lack (endpoint-level cross-service dependency, origin-tagged edges, RED-as-Facts, causation as a separate refutable layer).

**Already right — KEEP as-is:**
- `APPLICATION —OWNS→ SERVICE —MEMBER_OF/EXPOSES→ COMPONENT/API_ENDPOINT` ingress spine. Maps cleanly to AppD Application→Tier→Node, Dynatrace Service, Datadog Service, CSDM Business App→App Service→App.
- `API_ENDPOINT` identity (`service_name, env, method, route_template`) = OTel `http.route` + `http.method` exactly. First-class symptom target with its own RED. This is a genuine strength — finer than most CMDBs, on par with Datadog Resources / AppD Service Endpoints.
- Endpoint-level inbound dependency: `DEPENDS_ON (SERVICE, API_ENDPOINT)` and `CALLS (SERVICE, API_ENDPOINT)` — lets us say "orders-svc calls payments-svc `POST /charge`". Only AppD BTs and Datadog resources reach this granularity.
- The `Origin` axis (`declared` / `discovered` / `inferred`) — this is precisely the CMDB-vs-OTel reconciliation seam the whole market is groping toward (Datadog's "auto-detected + YAML-declared", ServiceNow's "curated rows vs Service-Mapping discovered", OTel's Entities layer). We already have it; we just under-use it on egress.
- Application/Service/Component discriminator pivoting on "has its own Deployment?" — clean, machine-checkable, maps to AppD tiers and CSDM app-vs-technical service.

**Wrong / missing — FIX:**
1. **Discovered downstream backends have no clean home.** A trace-detected call to an uninstrumented third-party/SaaS backend (the AppD "Backend", OTel `peer.service`, Datadog "inferred service") can only be shoehorned into `DEPENDS_ON (SERVICE, EXTERNAL_SERVICE)`, whose `default_origin=DECLARED` and whose docstring calls it "the durable CMDB-backed structural spine." A discovered exit-call is thus forced to violate the `DEPENDS_ON = declared` invariant. **This is the one architecturally significant gap.**
2. **Edge-borne RED is ungoverned.** `CALLS`/`READS_FROM` claim to "carry RED facts", but `EdgeSpec` has no `fact_predicates` field and `predicate_allowed()` only dispatches on `NodeType`. The central "discovered call carries RED" story has no registry backing.
3. **Endpoint downstream asymmetry.** `API_ENDPOINT` can receive calls but cannot fan out — no `API_ENDPOINT → DATABASE/QUEUE/EXTERNAL_SERVICE`. "`GET /checkout` is slow because *its* call to payments-db is slow" degrades to Service-level attribution, losing the endpoint granularity AppD BTs and OTel per-route traces preserve.

**Trims (elegance, not correctness):** `INSTANCE_OF` is a near-duplicate of `REALIZES`; `CONTAINS`/`MEMBER_OF` and permissive both-direction pairs want reducer-side canonicalization rather than two live edges.

---

## B. CANONICAL TOPOLOGY THE MARKET CONVERGES ON

Strip the branding and all five sources implement the same graph:

```
APPLICATION / BUSINESS APP            (grouping; no runtime edges)
      │ owns / contains
      ▼
SERVICE  (logical node — the center of gravity)     AppD Tier · DT Service · DD Service · CMDB App Service · OTel service.name+namespace
      │ runs-on (vertical)          │ exposes
      ▼                             ▼
PROCESS / POD / HOST          API_ENDPOINT (per-service sub-node)   AppD Service Endpoint · DT Endpoint · DD Resource · CMDB cmdb_ci_endpoint · OTel http.route
                                    │
                    ┌───────────────┴──── request/trace path (horizontal "calls" axis) ────┐
                    ▼                                                                        ▼
             SERVICE (downstream)                                        BACKEND, typed + inferred from the OUTBOUND call
             (instrumented peer)                                        ├─ DATABASE   (db.system.name + server.address + db.namespace)
                                                                        ├─ QUEUE      (messaging.system + destination.name)
                                                                        ├─ CACHE      (turbo exit point)
                                                                        └─ EXTERNAL   (peer.service / net.peer.name — uninstrumented/SaaS)
```

**The two universal invariants:**
- **Backends are never instrumented — they are materialized from the outbound (client/exit) span**, typed by connection metadata (host:port, db name+vendor, queue destination), and classified as DB / queue / cache / HTTP-external / remote-service. (AppD exit call→Backend; DT outbound→database service; DD outbound span→inferred service; OTel virtual node from `[peer.service, db.name, db.system]`.)
- **Impact propagates provider → dependent** — from the failing downstream up to its callers, along the reversed request edge. ServiceNow walks `child(provider)→parent(dependent)`; OTel walks `server/db/broker→clients`. Our `DEPENDS_ON`/`CALLS` direction already matches this.

**The one meaningful divergence** — how the downstream is *typed* — is where we should copy Dynatrace/Datadog, **not** AppD: promote discovered backends to first-class nodes in one uniform graph (reusing `EXTERNAL_SERVICE` + typed data nodes), rather than a second-class "Backend" class with a manual "resolve to tier" promotion step.

---

## C. REFINEMENTS TO NODES/EDGES — minimal, elegant, no bloat

**No new node types.** Reuse `EXTERNAL_SERVICE` as the detected-backend catch-all — its "third-party/SaaS dependency outside CMDB" meaning already *is* an uninstrumented peer. Everything below is edge-list widening + one spec field.

### C1. ADD — widen `CALLS` to reach non-Service backends *(the gap-closer)*
**EdgeType `CALLS`** — extend the allow-list:
```
CALLS: add (SERVICE, EXTERNAL_SERVICE),
           (COMPONENT, EXTERNAL_SERVICE),
           (API_ENDPOINT, EXTERNAL_SERVICE)
       default_origin = DISCOVERED   (already correct for CALLS)
```
This single change gives every trace-discovered vendor/SaaS/uninstrumented peer a *discovered* call edge carrying RED — the AppD-Backend / OTel-`peer.service` / DD-inferred-service pattern — without touching the declared `DEPENDS_ON` spine. `CALLS (Service→Service)` (instrumented peer) and the data edges (`READS_FROM`/`WRITES_TO`/`PRODUCES_TO`/`CONSUMES_FROM`, already `default_origin=DISCOVERED`) already cover the known-DB/queue/cache cases; this closes the uninstrumented-external case.

### C2. FIX — add `fact_predicates` to `EdgeSpec` *(highest-leverage; makes edge-RED enforceable)*
**`domain/spec.py` `EdgeSpec`** — mirror `NodeSpec`:
```
EdgeSpec.fact_predicates: frozenset[str]   # e.g. {call_rate, call_error_rate, call_latency_p99}
```
**`domain/registry.py` `predicate_allowed()`** — dispatch on edge subject (EdgeType) in addition to NodeType. Then the exit-call RED on `CALLS`/`READS_FROM`/`WRITES_TO`/`PRODUCES_TO`/`CONSUMES_FROM` is registry-governed instead of free-form. This is what turns "the discovered call carries RED" from a docstring claim into an invariant — and a `Fact.subject_ref` can already be an `EdgeId`, so the plumbing exists.

### C3. ADD — give `EXTERNAL_SERVICE` exit-call facts
**NodeType `EXTERNAL_SERVICE`** (`nodes/change.py`) — add `call_rate`, `error_rate` alongside existing `availability`, `latency_p99`. So a discovered backend surfaces the same RED shape as a Service node — one uniform golden-signal surface across instrumented and inferred nodes (the Dynatrace/Datadog "first-class-ish node" convergence).

### C4. FIX (medium) — let `API_ENDPOINT` be a call *source*
**EdgeTypes `READS_FROM`, `WRITES_TO`, `PRODUCES_TO`, `CONSUMES_FROM`, `CALLS`** — add `API_ENDPOINT` as a legal source to the same targets `SERVICE` already reaches (`DATABASE`, `CACHE`, `MESSAGE_QUEUE`, `EXTERNAL_SERVICE`, `SERVICE`). Restores egress symmetry so per-endpoint downstream localization ("`GET /checkout` → payments-db") is expressible — the AppD-BT / OTel-per-route capability. Minor: add `red_latency_p50` to `API_ENDPOINT` to match Service.

### C5. TRIM — collapse `INSTANCE_OF` into `REALIZES`
Drop `INSTANCE_OF`; map the OCP adapter's `ownerReference` → `REALIZES`. One fewer edge type, no lost expressiveness (Pod→Deployment is already legal under both). Canonicalize `CONTAINS`/`MEMBER_OF` and the permissive both-direction pairs (`DEPLOYED_AS`, endpoint↔service `DEPENDS_ON`/`CALLS`) to a single stored direction **in the reducer**, keeping the ingest allow-lists permissive but the stored graph disciplined.

### LEAVE (do not touch)
- The ingress spine (`OWNS`/`MEMBER_OF`/`EXPOSES`) and `API_ENDPOINT` identity keys.
- `DEPENDS_ON` staying `default_origin=DECLARED` — that is *correct*; it's the declared spine. The fix is giving discovered egress its own edge (C1), **not** loosening `DEPENDS_ON`.
- Causation-as-separate-layer, RED-as-Facts. No storage-volume/PVC node — park as future signal, out of RCA scope.

**Net catalog delta:** −1 edge type (`INSTANCE_OF`), 0 new node types, +1 `EdgeSpec` field, allow-list widenings only. Closed catalog and composability intact — every change reuses an existing primitive before adding.

---

## D. DECLARED vs DISCOVERED COEXISTENCE — via the `Origin` axis we already have

The market's central reconciliation problem — ServiceNow curated rows vs OTel span-derived edges, Datadog YAML-declared vs auto-detected — we solve with **one graph, edges discriminated by `Origin`, not two subsystems.** The clean contract:

| Layer | Edge + Origin | Source | Role in incident RCA |
|---|---|---|---|
| **Declared** (CMDB spine) | `DEPENDS_ON`, `OWNS`, `EXPOSES`, `MEMBER_OF` · `origin=declared` | ServiceNow `cmdb_rel_ci` `Depends on`, CSDM, human curation | Durable structural backbone; blast-radius scaffolding; the "what *should* talk to what". Stable across restarts. |
| **Discovered** (runtime traces) | `CALLS`, `READS_FROM`, `WRITES_TO`, `PRODUCES_TO`, `CONSUMES_FROM` · `origin=discovered` | OTel `CLIENT/SERVER` + `PRODUCER/CONSUMER` span pairs, AppD exit calls, servicegraphconnector | The *actual* runtime dependency, RED-bearing. The "what *is* talking, right now, and how hot the edge is". |
| **Inferred** (gap-fill) | same edge types · `origin=inferred` | Reducer minting a virtual peer from `[peer.service, db.name, db.system]` when a client span has no matching server span | Uninstrumented backend materialized as `EXTERNAL_SERVICE`; lowest trust, pending identity resolution. |

**Coexistence rules:**
1. **Same node pair, different-origin edges coexist — never overwrite.** A declared `DEPENDS_ON(orders-svc, payments-svc)` and a discovered `CALLS(orders-svc, payments-svc, POST /charge)` are *both* stored. Declared gives the sanctioned topology; discovered gives the live, RED-bearing reality. This is exactly Datadog's "manually declared augments auto-detected."
2. **Reconciliation, not merge.** When a discovered `EXTERNAL_SERVICE` (inferred from `peer.service`) is later matched to a declared CI, upgrade the *node's* `origin` and re-key it — the ServiceNow "resolve backend to tier" / AppD "resolve to tier" promotion, but automated via identity keys rather than a manual UI step. The edge keeps `origin=discovered` (it *was* discovered); only identity is reconciled.
3. **Origin drives trust in RCA.** Causation/blast-radius traversal weights `declared` edges as structural certainty and `discovered` edges as live evidence (they carry the RED that actually fired the anomaly); `inferred` edges are corroboration-pending. This is what lets one query answer both "what is the sanctioned dependency?" and "what actually called payments-db at 14:03?" off the same graph.
4. **Discovery never mutates the declared spine.** C1 exists precisely so a trace-discovered external backend lands on `CALLS(origin=discovered)`, never on `DEPENDS_ON(origin=declared)` — preserving the invariant that the declared layer is CMDB-authored and the discovered layer is trace-authored. The two are joinable on node identity, divergences (declared-but-never-observed, or observed-but-undeclared) become first-class RCA signals rather than modeling errors.
---

# Networking / Infra + Security Layer — actionable delta (elegance-guarded)

The L4 tier already has 4 families (path-device · transport · policy · naming); extend families, don't open a new axis. Net: +3-4 node types, 0 new edge types, +2 events, widened allowed-pairs, a few discriminator edits.

## ADD first-class L4 NodeTypes (nodes/network.py + enums.py)
- **proxy** (reverse/forward/sidecar — Envoy/nginx/HAProxy; the homeless mesh-sidecar class). id=(proxy_id); props(proxy_id,name,kind[reverse|forward|sidecar|egress]); facts(upstream_5xx_rate,active_connections,request_rate,p99_latency,upstream_healthy); events(config_reloaded,upstream_timeout,mtls_handshake_failed,connection_pool_exhausted).
- **api_gateway** (Kong/Apigee/AWS-APIGW/Istio-GW/APIM; auth/rate-limit/quota/route-map). id=(gateway_id); props(gateway_id,name,provider); facts(request_rate,5xx_rate,rejected_rate,auth_failure_rate,throttle_rate,p99_latency); events(route_remapped,rate_limit_tripped,auth_reject_spike,config_deployed).
- **cdn** (CloudFront/Akamai/Fastly/Cloudflare; origin-vs-edge fork). id=(cdn_id); props(cdn_id,name,provider); facts(cache_hit_ratio,origin_5xx_rate,edge_5xx_rate,request_rate,origin_latency_p99); events(origin_fetch_error,cache_purged,config_deployed,pop_outage).
- **waf** (Tier-1.5, optional; L7 app firewall). id=(waf_id); props(waf_id,name,provider); facts(blocked_request_rate,false_positive_rate,rule_matches); events(rule_set_updated,block_spike,rule_disabled).

## FOLD (no new type)
- security_group / NACL -> firewall_rule (same identity/facts/events/edge; record cloud class via class_hint; widen discriminator).
- vpn / tunnel / Transit-GW -> network_segment (+events tunnel_down, rekey_failed; widen discriminator).

## DEFER
- router/switch -> config_item (class_hint). network_interface(ENI) -> defer (promote if a per-NIC scenario is named). BGP/AS/route-table -> generic_ci (model a withdrawal as change_event/anomaly on the affected network_segment).

## EDGES — zero new types; widen `allowed` pairs (edges/structural.py + causal.py)
- ROUTES_TO: +(dns->cdn),(dns->api_gateway),(cdn->load_balancer|api_gateway|service),(api_gateway->service|api_endpoint|load_balancer|proxy),(proxy->service|api_endpoint|load_balancer),(load_balancer->proxy).
- SECURED_BY: +dst=waf for (api_gateway|cdn|load_balancer|proxy|service->waf); +dst=certificate for (load_balancer|proxy|api_gateway|cdn|service->certificate)  [gives TLS-expiry a structural home].
- EXPOSES: +(api_gateway->api_endpoint|route). CONNECTS_TO: +(proxy|api_gateway|cdn->network_segment).
- FIRED_ON: +(alert->proxy|api_gateway|cdn|waf|dns).
- CHANGED_BY: +(proxy|api_gateway|cdn|waf|load_balancer|dns|route->change_event).
- CAUSED_BY: +(hypothesis|anomaly->proxy|api_gateway|cdn|waf|load_balancer|dns).

## PRE-EXISTING FIXES (cheap, high-value)
1. load_balancer, dns, route were NOT legal CAUSED_BY/CHANGED_BY targets — add them (else an LB health-check fail / DNS misconfig can't be named a root cause). (covered in the CAUSED_BY/CHANGED_BY widenings above)
2. certificate had no structural edge — reuse SECURED_BY (above).

## Elegance: keep the 4 L4 families visible (proxy/api_gateway/cdn are siblings of load_balancer; waf sibling of firewall_rule). Registry-closure test stays green (all catalog members). Watch generic_ci/config_item class_hint frequency as the promote-later signal.
