# Incident Triage — Investigation Engine · Product Requirements (PRD)

> v1.0 · the requirements the design set implements. Concise + testable; the *how* is in `03-design.html` (master), `04-data-model.html` (schemas), `02-spec.html` (scope), `05-runbook.html` (ops), `06-delivery-plan.html` (plan). Worked example throughout: **INC-4821** (checkout latency → a failed disk).

---

## 1. Summary
A governed, in-house, **domain-neutral investigation engine** for production operations. One engine runs a versioned **playbook** through gated, audited phases; it builds a per-incident **graph**, reasons over it with an LLM, calls **governed capabilities** (never tools directly), and **a human approves every write**. First domain: **incident triage**. The vendor engine (Amelia) is retired **gradually**, not big-bang.

## 2. Goals / Non-goals
**Goals.** Governed at the boundary (allow/ask/deny · human-approved writes · human close) · no **vendor** lock-in (abstract capabilities) · no **domain** lock-in (everything domain-specific lives in the playbook) · durable + reversible (checkpointed · exactly-once · rollback) · evidence-based, regulator-grade audit · earned, reversible autonomy.
**Non-goals (now).** A persistent estate-wide knowledge graph · domains beyond incident-triage · auto-clustering. (All are *future*, see §12.)

## 3. Users
- **SRE / IRO on-call operator** — primary; drives triage, answers gates, closes.
- **Incident commander / MIM** — coordinates a major incident; joins the shared session.
- **Admin / reviewer** — owns `CapabilityPolicy` and playbooks (the governance data).

## 4. Scope
**In:** the 4-phase incident-triage playbook · the per-incident investigation graph · the capability layer + registry + `govern()` · the approval gate · the live multi-user session · the React console (3 panes + graph) · audit · feedback/learning · related-incident handling.
**Out (now):** the estate KG · non-incident domains · auto-cluster (see §12).

## 5. Functional requirements

### Playbook & engine
| # | Requirement |
|---|---|
| FR1 | The engine runs a **versioned, tool-agnostic playbook**: phases declare `needs` (intents) + `effect` — **never tool names**. Changing the process = editing the playbook; the engine never changes. |
| FR2 | **Four re-enterable phases:** Assess → Root cause → Remediation → Verify & close. Conditional edges: loop Root cause while `confidence < min_confidence` (a playbook field — incident-triage `0.7`); Verify backtracks to Root cause if recovery didn't hold. A cause may be a node, change, zone, or **`time_factor`** (a time-triggered contributor). |
| FR3 | Per phase, the model **plans then loops**: resolve a `need` → governed capability → fold result into the graph → log a `Step` → until the typed **output** is sufficient (validated vs the playbook's `output_schema`). Steps are emergent, not scripted. |
| FR4 | **Every world-changing write (an `effect=write` capability) pauses at the gate**; the operator approves / refines / denies inline. **Closing is a human act** — never auto-closed. (Graph `annotate` is a low-risk write — **evidence-required + audited, not gated**; see FR7.) |
| FR5 | **Durable & resilient:** checkpointed after each step (LangGraph), resumable from any server, **exactly-once** writes (idempotency key). On a capability failure: transient → **retry** (`max:3`, exponential backoff); permanent / blocked → **`error_handler`** (escalate to on-call); `on_failure: run-remaining` — independent steps finish and the phase reports `blocked` / `partial`. |

### The investigation graph
| # | Requirement |
|---|---|
| FR6 | **One per-incident graph** (Node/Edge/Fact). **Exactly one** incident (subject) node; other incidents are `related_to` **references**, not loaded. |
| FR7 | **The engine owns the graph** (in-memory `networkx` working copy + durable in Mongo/checkpoint); the LLM acts via **governed tools** (`get` · `neighbours` · `walk` · `find` · `blast_radius` · `path` · `annotate`) — it **never holds the raw graph**. A tool **never invents a node** (unknown id → `unknown`); `annotate` **requires an `evidence_ref`** or is rejected. |
| FR8 | The LLM sees a **rendered slice** (cause path + frontier + suspects in full; healthy/ruled-out collapsed to a count) — **bounded regardless of graph size**. |
| FR9 | Each capability result is mapped into the graph by a **per-source fold-adapter** — a new source = a new adapter, no engine change. The fold is **idempotent** (keyed by `node id · fact key · source · observed_at`, so a replayed step never duplicates state); conflicting facts are **kept with source + confidence**, never silently overwritten. |

### Capability layer & governance
| # | Requirement |
|---|---|
| FR10 | **Registry:** Provider / DeclaredCapability / CapabilityPolicy. A playbook `need` resolves to capabilities by **intent + effect**; `CapabilityPolicy` gates each (**allow / ask / deny**). A new capability lands `pending_review` + `deny`; an unknown effect falls to the playbook's **`unknown_access` (default `ask`)**. |
| FR11 | Sources bind by **kind** (skill \| mcp_local \| mcp_remote \| a2a_agent \| api), onboarding priority **native MCP → register API/plugin as dynamic MCP → A2A**. |
| FR12 | `govern()` resolves **effect × access** at call time; a **read-only phase provably cannot select a write** (enforced at intent resolution, not just the gate). |
| FR13 | **Earned autonomy:** feedback (outcome/failure/correction) drives an autonomy ladder per *phase × node-type × severity*; `ask → allow` only for proven, low-risk, reversible actions. |

### Sessions (live, multi-user)
| # | Requirement |
|---|---|
| FR14 | **Free chat** (per-user, ephemeral, no run) → on identity + subject `{domain,id}` → the related-incident check → **create or join** a shared session (operator-confirmed; **idempotent on the subject id** — never two sessions for one incident). Promotion **carries the free-chat context** into the session (not a reset). |
| FR15 | **One incident = one session = one run**, shared across operators/devices. **One writer at a time ("the pen")** — only the pen-holder may send or approve; everyone else is a read-only **viewer** (`take-pen`/`release-pen`, one holder at a time). **Single approval**, no dual. |
| FR16 | **Problem 1 — run on stateless servers:** advancing a session is serialized by a **per-session lock** (run-owner) held by a **lease + heartbeat** (a slow owner keeps its lease; a dead owner's lease expires → another server resumes from checkpoint). A 2nd operator's input during an in-flight step is **queued** (never a 2nd run), **drained after the current step or at the next gate**. A headless / idle session still advances or waits at its gate; events persist. **Membership is re-checked per event**, not only at join. |
| FR17 | **Problem 2 — chat sync:** an **append-only per-session event log** — one ordered stream, chat + graph + phase deltas under one `seq`. Clients **poll `since(seq)`** (correctness needs nothing more) and may upgrade to **SSE** (`/stream`, `Last-Event-ID`) for live delivery over the same `seq` API; **no push bus** (no Redis / WebSocket fan-out); **join/reconnect = snapshot + resume-from-seq**. |
| FR18 | **Related incidents:** the engine surfaces a similar/existing session; the **operator** links-as-related or opens a new triage — **human-controlled, no auto-merge, no cluster-scope run**. |

### Console
| # | Requirement |
|---|---|
| FR19 | One **React** workbench, three panes: **Incidents** (left) · **Triage** chat + inline gate (center) · **Phases & Steps ⇄ Graph** (right). The chat renders a **widget registry** (text · tool-call · table · image · graph · **sandboxed-iframe HTML**); live over **SSE** (`/stream`, `Last-Event-ID`) or polling. |
| FR20 | The **Graph** view **focuses** (cause path + impacted + collapsed-healthy + minimap) — handles hundreds of nodes. |

### Audit & feedback
| # | Requirement |
|---|---|
| FR21 | **Every step** (tool-call + evidence deep-link, the suggestion, the gate decision) is logged; the Step journey **is** the regulator-grade audit trail. |
| FR22 | **Feedback** (separate from the run) feeds the **learning loop** (similar-incident suggestions) + the autonomy ladder. |

## 6. Non-functional requirements
| NFR | Target |
|---|---|
| Auditability | every action + decision reconstructable; immutable. |
| Safety | no un-gated write; every write reversible; read-only phases provably cannot write. |
| Durability | checkpointed · exactly-once · resumable across crashes. |
| Concurrency | serialize *within* a session, parallel *across* sessions; shared multi-user sessions. |
| Latency | the gate is the only human wait in the loop; "first assessment in minutes" is a **non-binding design target** — a measurable P95 budget is set during delivery, not fixed here. |
| Scale | concurrent incidents; degrade gracefully when a source is down; engine horizontal. |
| Security / hardening | secrets/KMS · encryption at-rest + in-transit · least-privilege scopes · audit immutability — **in place before any write ships (M2)**. |
| Extensibility | a new source = a registry entry + fold-adapter; a new domain = a new playbook — **no engine change**. |
| Resilience | **correctness lives in the stores** — live delivery (polling, or SSE as an optional liveness layer) is best-effort; a missed poll/stream simply retries and nothing is lost. |

## 7. Data & model *(full in `04-data-model.html`)*
- **Graph:** `Node{id,kind,type,layer,labels,props,facts}` · `Fact{key,value,source,evidence_ref,observed_at,confidence,impact_state}` (no ttl; `impact_state` ≠ health — a `health:ok` node may still be impacted and counts toward blast radius) · `Edge{type,from,to,props}`.
- **Reasoning conventions** (graph labels, not engine code): `policy_block` (a healthy node on the victim's path stays a suspect) · `time_factor` (a time-triggered contributor — cron / expiry / ttl-lapse) · `realized_by` (reconcile logical blast-radius with the physical fault) · `alert_noise` (a storm can't outvote a high-confidence fact).
- **Phase Record:** `PhaseRecord{id,subject,phase,goal,state,plan,steps,output}` · `Step{seq,kind,capability,input,result,touched,evidence}`.
- **Phase outputs:** `AssessResult` · `RootCauseResult` · `RemediationResult` · `VerifyResult` (typed; e.g. `confidence{value,basis}`, cause `path`, `expected_effect/blast_radius/rollback/temporary/revert_when` [`incident_close | action:<id> | problem | change`]; remediation `kind: reverse | mitigate | escalate` + `followups[]` for durable fixes owned elsewhere; `resolution`).
- **Subject:** `SubjectRef {domain, id, kind}` — never a bare `incident_id`.
- **Feedback** (separate from the run): `{subject, run_id?, actor, kind: outcome | failure | correction, verdict, note, at}` → the learning loop + the autonomy ladder.
- **Storage planes:** MongoDB (incident doc — denormalized read-model + the append-only event log) · PostgreSQL (playbook + registry, normalized; LangGraph checkpoint + the run-owner lock). **No Redis** — live sync is client polling of the event log.

## 8. Architecture & tech stack *(full in `03-design.html`)*
Backend **Python** · Frontend **React** · Engine **LangGraph** (+ `PostgresSaver`) · in-memory graph **networkx** · data **MongoDB** · config + run-state **PostgreSQL** · live sync over an append-only event log — **polling** for correctness, **SSE optional** for liveness over the same `seq` API (**no Redis / WebSocket push bus**) · capability transport **MCP / dynamic-MCP / A2A**. Three runtime pieces: the engine (B1–B7), the live session (B8), the graph runtime (B9).

## 9. Key decisions (constraints — must hold)
1. **Per-incident graph** — not a shared/estate KG.
2. **Tool-agnostic playbook** — intents, never tool names.
3. **One playbook (incident)** — `cluster` = operator-confirmed link, **no cluster-scope run**.
4. **Graph is engine-owned + LLM-via-tools** — not LLM-held text, not a graph DB.
5. **Subject = {domain, id}** — never a bare `incident_id`.
6. **No SLO** — impact stated in business terms.
7. **State in the durable stores** — the session is a **lock + an event log** (polled for correctness, SSE optional for liveness; no sticky servers, no Redis/WebSocket push bus).

## 10. Acceptance criteria (testable)
1. A **read-only phase cannot perform a write** (provable at intent resolution).
2. **Every write is gated + carries a rollback**; closing requires a human.
3. A run **resumes correctly from the last checkpoint** after a server kill — no lost or double work.
4. Two operators in one session see the **same live chat + graph**; a 2nd-user input during an in-flight step is **queued, not a second run**.
5. A capability the playbook **never names** is invoked **only** via the registry + `govern()`.
6. The graph renders a **bounded slice** even for a 147-node incident; the LLM never receives the raw graph.
7. **INC-4821 runs end-to-end** — Assess (147 nodes, suggestion from INC-4820) → Root cause (`stor:pay-vol` 0.9, path app→db→storage; rev47 / gemini / network ruled out) → gated failover (temporary) + a `followups` escalation to replace the disk → Verify recovered (p99 4.2s→260ms) → human close.
8. **The graph never invents or silently overwrites** — an unknown id returns `unknown`; a conflicting fact is kept with its source + confidence; an `annotate` without an `evidence_ref` is **rejected**; a replayed step folds **idempotently** (no duplicate nodes / facts).
9. **Session invariants hold** — creating a session for an in-flight subject **joins** it (never a 2nd thread); a crashed owner's lease expires and another server **resumes** mid-run; a user who loses access mid-session **stops receiving events** (membership re-checked per event).

## 11. Delivery *(full in `06-delivery-plan.html`)*
**M0** Foundation → **M1** Assisted (read-only) → **M2** Remediation (gated writes; hardening green) → **M3** Coverage → **M4** Earned autonomy. ~14 two-week sprints; **Amelia runs alongside and is ramped down** per proven slice.

## 12. Out of scope / future
- **Estate knowledge graph** — a persistent, read-mostly topology source the engine pulls *from* (never the mutable investigation surface).
- **Domains beyond incident-triage** — provisioning, capacity… same engine, new playbooks.
- **Auto-clustering** — clustering stays operator-confirmed.

## 13. References
The design set in `design/v2/` (start at `index.html`): `01-presentation` · `02-spec` · `03-design` (master) · `04-data-model` · `05-runbook` · `06-delivery-plan` · `07-capability-layer`.
