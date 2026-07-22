# Validation Verdict (live-LLM reasoning + domain-model + capability + execution)

# VALIDATION VERDICT — Investigation Workbench Engine

**Bottom line:** The engine's *judgment* is real — a live LLM reasons like a differential diagnostician — but it *cannot yet converge*, and the reasons are structural, not model-quality. The domain model is genuinely good with one core self-inconsistency. Capability integration is over-built for a world that now ships first-party MCP everywhere. Fix the belief/gate loop, the evidence-addressing contradiction, and re-seam capabilities to MCP, and this becomes a real agent.

---

## A. DOES IT REASON / PLAN / EXECUTE WELL?

**Verdict: Reasons and plans genuinely well; does not yet converge. The gap is engine/tool structure, not model judgment.**

**The live evidence (3 scenarios, real Gemini, temp 0):**
- **Convergence to the exact golden root: 0/3.** But all 3 went change-first to the *correct branch*, and the failures are diagnostic, not random.
- **The strongest positive — the closed catalog held under a real model: 0 reducer rejections across all 3 runs.** Nothing off-vocabulary ever materialized; planner-side repairs were minimal (2 malformed edges, 1 off-catalog intent, 0). The offline stub proves the pipeline converges cleanly *given a good plan* — so the machinery is correct.
- **Plan quality is high.** In `code_regression` the model did textbook differential diagnosis: proposed a rival hypothesis (`database:payments-ora`), sought refuting evidence, ruled it out on real telemetry (pool 0.28), then confirmed the leader via `blame` + error signatures. That is exactly the reasoning the system is designed to elicit.

**The top gaps that HURT reasoning (all structural):**

1. **Belief-signal / gate mismatch.** The LLM signals conviction via hypothesis *status* (`confirmed`); the advance gate measures the *confidence band* (≥0.8). A perfect confirmation stalled at 0.6 and looped to `max_steps` with `outcome=open`. Good reasoning literally cannot advance.
2. **Tools return statistics, not content.** `diff_range` returned `lines_added:1`, never the `DROP INDEX` line — so the model *correctly* saw the saturated pool and blamed the mechanism instead of the change. It reasoned right on the evidence it was given; the evidence was gutted.
3. **No gate-failure feedback + temp 0 = deterministic looping.** A failed phase repeats the identical losing move because nothing tells it *why* it failed or *what* the gate needs.
4. **Two intent vocabularies.** Abstract `allowed_intents` (`fetch_metrics`, `correlate_timeline`) ≠ concrete wired adapter intents (`instant_query`, `range_query`). The weaker model emitted the abstract words as tool calls, got nothing back, and stalled for 9 iterations.
5. **Context handoff starves the planner.** `render_slice(focus=anomaly)` collapsed to ~1 node because the FRAME-seeded Anomaly is topologically isolated — the planner was blind to the evidence its own tools produced until it was handed the live graph directly.

---

## B. IS THE DOMAIN MODEL GOOD?

**Verdict: YES — genuinely good, above the reference bar (ServiceNow, OTel, commercial RCA) on the hardest things. One self-inconsistency short of excellent.**

Three load-bearing decisions are correct and well-executed: the **Property/Fact/Event trichotomy with bi-temporal, reified, retractable facts** (a real temporal-DB foundation most CMDBs lack); **causation as a separate refutable layer** over the structural spine (more principled than ServiceNow's dependency/impact conflation); and **LLM-fit machinery** (per-type discriminators, closed vocab + single escape hatch, typed operations as the only output channel, `NoEvidence` op, hypotheses carrying *refuting* facts + predictions).

**Prioritized data-model fixes:**

**P0 — the single most important fix. Unify evidence addressing.** This is the model's core contradiction. The graph records support at *node* granularity (`node → Hypothesis` via `SUPPORTS`/`EVIDENCE_FOR`) while the ledger records it at *fact* granularity (`Hypothesis.supporting_facts: FactId[]`). The two can disagree about the exact thing the system exists to output, and "show me the precise facts for and against this hypothesis" is unanswerable from the graph. Make the **Fact the one addressable unit of evidence**; collapse `supporting_facts` + `SUPPORTS` + `EVIDENCE_FOR` (and the refuting trio) into a single fact-addressed link. Cheapest to fix now, before reducer/ledger/fixtures harden. *Files:* `edges/causal.py`, `hypothesis.py`.

**P0 — Give Edge and Event a lifecycle.** Retraction is Fact-only today: a **refuted `CAUSED_BY` edge cannot be tombstoned** (the hypothesis flips to REFUTED but the inferred causal edge persists) and a flaky-exporter telemetry Event can't be retracted — an asymmetry in a system whose premise is that observations can be wrong. Add `state`/`valid_to`/`invalidated_by` symmetric with `FactState`. *Files:* `edge.py`, `event.py`, `enums.py`.

**P0 — Enforce the belief-channel invariant.** "Exactly one of `confidence`/`source_reliability` is meaningful" is prose only; nothing stops an inferred fact carrying neither, defeating R-C4. Add a `model_validator` on `Fact` keyed off `Source`/`Origin`. *File:* `fact.py`.

**P1:** structured impact/severity on Anomaly/Incident (affected-count, error-budget burn, SLA breach) replacing the severity string; a **recurrence relation** (`RECURRENCE_OF`/`SIMILAR_TO`, Incident→Incident) — a top real-world RCA accelerator, entirely absent; promote **Actor** to a node (change authorship / incident command are un-joinable strings today); first-class **saturation/ResourcePool** (thread-pool exhaustion has no home); a **baseline/expected-value** concept for anomalies; canonicalize direction in `edge_id` for symmetric/reversible edges (dedup hole).

**P2:** extend predicate governance to edge-subject facts; add per-edge discriminators or merge near-synonym clusters (`AFFECTS`/`IMPACTS`, `REALIZES`/`INSTANCE_OF` — edge selection is the LLM's weakest surface); Trace/Span substrate if trace-diagnosis ever in scope; fix `GENERIC_CI` tier mis-tag.

---

## C. SIMPLER CAPABILITY INTEGRATION

**Recommendation: MCP is now the default binding; A2A is a narrow later case (writes only); REST is the fallback. Don't redesign — RE-SEAM.**

**The market reality that invalidates the current design:** 7 of 9 tools now ship **first-party** MCP servers (ServiceNow Zurich, Splunk GA, ThousandEyes, OpenShift, JFrog, GitHub, PagerDuty); AppDynamics is covered via Splunk-Obs convergence; only Prometheus lacks one (and it's trivial raw REST). The "8 tools as 8 bespoke API clients" premise is dead — for MCP tools the live fetch is *one* generic `tools/call(name, args) → result`. Tellingly, PagerDuty/OpenShift/JFrog/GitHub servers all **default read-only and gate writes behind a flag** — the exact READ/WRITE invariant already in `CapabilityLayer.invoke`. **A2A is agent↔agent, not a tool binding** — reserve it only for delegating a whole *remediation* sub-task to a vendor's autonomous agent, always on the write side, behind the existing gate.

**Keep:** the pure `normalize(raw) → Operation[]` fold (your domain value-add and golden-test guardrail — MCP returns vendor JSON, not your closed ops, so it must NOT collapse into `query`); the effect gate + `Invocation` audit.

**The concrete simpler design:**
1. **`Binding` enum as per-tool data** (§E.2's `Provider.kind` column made real): `MCP` (default, 8/9), `REST` (Prometheus, local git), `A2A` (remediation, later). Each adapter declares `binding = Binding.MCP` — a data field, not a code fork.
2. **Three `Source` transports behind one `fetch(binding, intent, params) → raw` interface**, with `normalize()` identical across all three: `MockSource` (fixtures — hermetic suite untouched), `McpSource` (one generic MCP client; a new vendor = a config line, zero new code), `RestSource` (~30-line shim, two clients total not eight).
3. **Collapse the two call sites into the layer, gate-first** — which also fixes a latent live bug (see D).

**Migration (each step green first):** rename `query`→`fetch` (no behavior change) → move fetch into layer + gate-first → add `Binding` field (mock ignores it) → ship `McpSource` behind a flag, prove one live path (GitHub/PagerDuty are cleanest) → ship `RestSource` Prometheus shim → optionally derive-and-filter the catalog from `tools/list` (turns the hand-maintained `intents` frozenset from a drifting copy into a validated allowlist). Nothing forks the graph fold, reducer, or fixtures.

---

## D. EXECUTION SOUNDNESS

**Verdict: The deterministic plumbing is sound and well-tested (52 green). The governance/completeness layer is thin — several advertised invariants are described but not enforced or exported.**

**What WORKS:** journal replay is **losslessly bit-identical** for graph *and* ledger, surviving NDJSON round-trip with seq continuity and bi-temporal supersession intact; the **write-gate blocks before any side-effect** and records `Invocation(blocked=True)`; **REPEAT routing** is correct both ways (explicit + default re-entry); **BACKTRACK** works where a transition is defined; `max_steps` bounds every loop cleanly; partial-accept reducer rejects illegal ops while applying legal ones with provenance.

**What's FRAGILE:** **BLOCKED is a silent dead-end** — no `blocked` transition exists, so it returns `None` → `close_outcome=None`, indistinguishable from normal completion; there is no distinct "gave-up" terminal state. **DONE is an ungated escape** — `check_gate` only guards ADVANCE, so a planner emitting DONE from any phase bypasses every gate. **No config-integrity validation** — a dangling `on_verdict` target or bad `entry_phase` crashes mid-run with `KeyError`; a typo in `produces_required` stalls the phase in REPEAT forever. **The write path is unwired and unimplemented** — the only WRITE adapter is excluded from defaults, `normalize()` raises `NotImplementedError`, and `_run_phase` doesn't wrap `layer.invoke`, so an approved remediation would crash the run.

**Concrete BUGS (file:line):**
1. **Ledger `CREATE` overwrites accumulated belief** — `ledger/ledger.py:29-32`. A re-CREATE of an existing hid (a live planner re-entering HYPOTHESIZE on REPEAT) resets status + evidence to freshly-proposed — silently losing REFUTED status and all facts, violating "belief moves only via HypDelta" and "refuted hypotheses are KEPT." **This directly compounds the Track-1 looping** and is not caught by tests.
2. **`promotion_ok` is dead code** — `ledger/ledger.py:76-88`. The Popperian rule (beat runner-up by delta, no unrefuted rival above gate) exists but nothing calls it; the gate enforces only "leader ≥ gate" + "some refutation exists." An 0.88 unrefuted rival passes ADVANCE beside a 0.9 leader. The differential-diagnosis discipline the design advertises is **not enforced at runtime**.
3. **`require_refutation` satisfiable by a stale/unrelated refutation** — `controller.py:35-39`. It scans the whole ledger for *any* REFUTED hypothesis, so once anything was ever refuted the gate passes even though the current leader was never challenged.
4. **Confirmed root cause ships with empty `causal_chain`** — all 5 scenarios confirm with `causal_chain_links=0`; confirmation isn't gated on a populated chain, so `postmortem.root_cause.chain` is always `[]`. The typed causal timeline is never captured.
5. **`export_bundle` drops the audit trails** — `api/bundle.py:15-50` omits `invocations` and `rejections`; worse, invocations are never journaled (only `append_phase`, never `append_step`), so they vanish on persist/reload — contradicting "records every invocation for the audit trail."
6. **`op_ceiling` truncation is dependency-blind** — `runtime/engine.py:119-122`. `(data_ops + plan.ops)[:ceiling]` puts planner-direct ops (often the `produces_required` outputs) at the tail where they're dropped; a truncated node causes surviving ops referencing it to be rejected as unknown subject.

---

## E. PRIORITIZED FIX LIST

Ordered to make the engine genuinely reason well first (these are what blocked live convergence), then the model, then capabilities.

**Tier 1 — Unblock convergence (the live runs failed here):**
1. **Close the belief/gate loop.** Derive confidence from hypothesis `status`, OR require the model to bump `confidence_level` *and feed it the exact failing gate* via `ctx` ("leader at 0.6, need 0.8; rival h2 still unrefuted"). Without this, perfect reasoning stalls at 0.6. *(Track 1 gap #2.)* **Do this with fix #2 below** — they are the same loop.
2. **Fix ledger `CREATE` belief-overwrite** — `ledger/ledger.py:29-32`. Make re-CREATE a no-op or merge; never reset REFUTED status/evidence. This silently destroys evidence on every REPEAT and compounds the looping. *(Track 4 bug #1.)*
3. **Give tools semantic payload, not counts.** `diff_range`/log/trace tools must return the actual changed lines, hunks, and stack frames — without the `DROP INDEX` line the model provably blames the mechanism. *(Track 1 gap #1.)*
4. **Add gate-failure feedback into the plan context** so a failed phase gets told *why* — kills the temp-0 identical-loser loop. *(Track 1 gap #3.)*
5. **Unify the two intent vocabularies** — collapse abstract `allowed_intents` and concrete adapter intents into one resolvable vocabulary; make FRAME attach the Anomaly to discovered topology (or widen the slice to recently-touched nodes) so the planner can see the evidence its own tools produced. *(Track 1 gaps #4, #5.)*
6. **Enforce differential-diagnosis governance at the gate.** Wire the dead `promotion_ok` (Track 4 #2) and scope `require_refutation` to the *current leader* (Track 4 #3). This makes the Popperian discipline the engine reasons toward actually load-bearing.

**Tier 2 — Domain model integrity:**
7. **Unify evidence addressing** — make the Fact the one addressable evidence unit; collapse `supporting_facts` + `SUPPORTS` + `EVIDENCE_FOR` (and refuting trio) into a single fact-addressed link. *(Track 2 P0 #1 — the model's core contradiction.)*
8. **Give Edge and Event a lifecycle/tombstone** symmetric with `FactState`. *(Track 2 P0 #2.)*
9. **Enforce the belief-channel invariant** with a `Fact` validator keyed off `Source`/`Origin`. *(Track 2 P0 #3.)*
10. **Gate confirmation on a populated `causal_chain`** so the postmortem timeline is real, not `[]`. *(Track 4 bug #4 — sits between model and execution.)*
11. P1 model additions: structured impact/severity, recurrence relation, Actor node, first-class saturation/ResourcePool, baseline concept, canonical `edge_id` direction. *(Track 2 P1.)*

**Tier 3 — Capability integration re-seam:**
12. **Re-seam to `Source`/`layer` + gate-first**, which also fixes the **live write-before-gate bug** — `engine.py:115` calls `source.query()` before the write gate checks, so a live `ocp__restart` would execute *then* be told "blocked." Move fetch inside the layer, gate first. *(Track 3 + Track 4 write-path finding.)*
13. **Add the `Binding` enum as per-tool data**; default MCP, REST for Prometheus/local-git. *(Track 3.)*
14. **Ship one generic `McpSource`** + prove one live path (GitHub/PagerDuty); the other 7 tools become config, not code.

**Tier 4 — Robustness / audit completeness:**
15. **Add config-integrity validation at load** (dangling transitions, bad `entry_phase`, `produces_required` field typos) — fail fast, not mid-run KeyError or infinite REPEAT. *(Track 4.)*
16. **Add a distinct BLOCKED/gave-up terminal state** and gate DONE like ADVANCE. *(Track 4.)*
17. **Export `invocations` + `rejections` and journal invocations** (`append_step`) so the audit trail survives persist/reload. *(Track 4 bug #5.)*
18. **Make `op_ceiling` truncation dependency-aware** (or order planner ops before data ops). *(Track 4 bug #6.)*

**Relevant files:** `/Users/innamul/Project/iw/engine/src/iw_engine/ledger/ledger.py`, `/Users/innamul/Project/iw/engine/src/iw_engine/runtime/{engine,controller}.py`, `/Users/innamul/Project/iw/engine/src/iw_engine/domain/{fact,edge,event,enums,hypothesis}.py`, `/Users/innamul/Project/iw/engine/src/iw_engine/domain/edges/causal.py`, `/Users/innamul/Project/iw/engine/src/iw_engine/capability/layer.py`, `/Users/innamul/Project/iw/engine/src/iw_engine/api/bundle.py`, plus the two live deliverables at `.../domain/catalog.py` and `.../runtime/live_planner.py`.