## Goal
Make the Investigation Workbench engine **fully live-capable and LLM-agnostic** — so that when you wire in ANY LLM (xAI, Gemini, Anthropic, OpenAI, local) and real capabilities, the live path works end-to-end. Four gaps identified in the review; this plan fixes all four and adds LLM pluggability. The engine core (judgment/fetch seams, reducer, ledger, journal) is already clean — most changes are additive.

## Guiding principles
- **xAI stays the default** (`make_live_client` precedence unchanged) — no interference with your existing live flow.
- **All 104 tests stay green.** New behavior is additive; no existing test assertions change.
- **The engine core stays mock-agnostic.** No branching on which planner/source is wired.

---

## Part A — LLM pluggability (any LLM, not just xAI/Gemini)

**New file: `engine/src/iw_engine/runtime/llm_client.py`**
- Define a `LLMClient` Protocol: `name: str` + `complete_json(system: str, user: str) -> dict`. This is the exact surface `LivePlanner` calls (confirmed: one call site at `live_planner.py:353`).
- Move `GeminiClient` + `XaiClient` here from `live_planner.py` (re-exported for back-compat). They already structurally satisfy the Protocol — no inheritance needed.
- Expose `_loads_salvage` and `_retry_delay` as module helpers reusable by any provider client.
- Add a registry-based factory `make_llm_client(model=None)` that consolidates the duplicated `make_live_client` (scenarios.py:169) and `make_client` (run_live.py:48). Selection precedence stays **xAI-first**, then Gemini, then `None`. Reads env vars: `XAI_API_KEY`, `GEMINI_API_KEY` (new, cleaner than the hardcoded `~/.secrets/stock/` path — but keep the file fallback too), `IW_LIVE_MODEL`, `IW_LIVE_PROVIDER` (new escape hatch to force a provider).

**How a user plugs in ANY LLM (documented in a new section of the engine README / a docstring):**
- **Easiest (existing seam):** pass a custom `client` to `live_build_manager(client=MyClient())` or `LivePlanner(client=...)`. Zero engine changes needed — this works today.
- **Via env:** subclass nothing — implement the 2-member `LLMClient` Protocol and register it in the factory.

**Files touched:** new `llm_client.py`; thin edits to `live_planner.py` (re-imports), `scenarios.py` (call consolidated factory), `scripts/run_live.py` (call consolidated factory).

---

## Part B — Adapter response-mapping layer (real MCP/REST tools, not just fixtures)

**Gap #1 fix.** Today adapters bracket-access required fields and crash on real vendor JSON shapes.

**New file: `engine/src/iw_engine/capability/mapping.py`**
- A `map_response(binding, intent, provider, vendor_raw) -> dict` function that translates vendor tool output into the adapter-expected shape (the de-facto schema documented by `live_fixtures.py`).
- Implemented as a dispatch table keyed by `(provider, intent)` → translator function. Translators are **pure functions** that pull vendor fields and emit the adapter shape. Start with the most common shapes (Prometheus `/api/v1/query` envelope `{status, data: {result: [{metric, value}]}}` → `{service, metrics}`; ServiceNow `result[]` → `changes`/`incident`; etc.).
- **Optional and opt-in**: a `MappingSource` wrapper that composes over any `Source` (McpSource/RestSource/RoutedSource) and maps before returning. Fixtures (`MockSource`/`ScenarioSource`) bypass mapping since they're already adapter-shaped. This keeps the hermetic test net 100% untouched.

**Files touched:** new `mapping.py`; thin edit to `capability/layer.py` to optionally wire mapping. No adapter files change (they keep their contracts).

**Scope note:** I'll ship translators for the highest-impact providers (Prometheus, ServiceNow, Splunk, Git — the 4 most common real-tool integrations) and leave the rest as documented no-ops (pass-through). The framework is in place; translators extend one-per-provider.

---

## Part C — Live fixtures for the 3 new incidents (gap #2)

**Edit: `engine/src/iw_engine/runtime/live_fixtures.py`**
- Add 3 builder functions matching the existing pattern (`subject, fx, golden_root`):
  - `cache()` → INC-5500, providers: `servicenow` (the deploy CHG-22), `prometheus` (collapsed hit-rate + recovery), `appd` (p50 flat, cache exit calls), `git` (the singleflight-disabled diff). `golden_root = "code_commit:9f8e7d6"`.
  - `featureflag()` → INC-5600, providers: `servicenow` (CHG-77 flag flip + last-deploy 3-days-old), `prometheus` (5xx + recovery), `splunk` (TaxEngineException), `git` (blame showing the gated branch). `golden_root = "feature_flag:new-tax-engine|prod"` (or the change_event — confirmed with a live run).
  - `certificate()` → INC-5700, providers: `servicenow` (incident), `prometheus` (partial 5xx + recovery), `splunk` (PKIX errors), `artifactory` (cert expiry — new provider use). `golden_root = "certificate:auth-tls-intermediate"`.
- Register all 3 in `LIVE_SCENARIOS` keyed by the catalog `key`.
- **Verify by running `run_live.py` for all 3** with a key present (or assert they at least load + the scripted twin confirms the shape). `live_wired_ids()` then returns all 11.

**Note on `feature_flag`/`certificate` node types:** they're edge-isolated (no typed edges) — confirmed earlier. The fixtures must convey the causal link via facts + the LLM's hypothesis `root_candidate`, not via fixture-driven edges. The scripted twins already proved this works (0 rejections).

---

## Part D — Batch live resilience (gap #3)

**Edit: `engine/scripts/run_live.py`**
- Wrap the per-scenario loop body (run_live.py:88-92) in try/except: on any exception, record the scenario as `converged=False` with the error message, emit a clear failure line, and **continue to the next scenario** instead of crashing the whole batch.
- Keep per-scenario isolation: a JSON-parse failure or MCP error in one scenario doesn't kill the others. Final summary still reports all scenarios' convergence.
- No behavior change when nothing errors.

**Files touched:** `run_live.py` only.

---

## Part E — Belief-channel soft rejection in the reducer (gap #4)

**Gap #4 fix.** Today a malformed model-authored fact crashes the run via `Fact._belief_channel` (fact.py:57-78) raising inside `reducer.py:106-111`. The LivePlanner pre-repairs, but one miss = full crash.

**Edit: `engine/src/iw_engine/graph/reducer.py`**
- Wrap the `Fact(...)` construction at reducer.py:106-111 in try/except `ValidationError`. On a belief-channel (or any Fact-validation) violation, append a `Rejection(i, "add_fact", reason)` and `continue` — exactly like the existing predicate/subject rejections above it. The op is dropped; the run continues.
- **The `Fact` model invariant stays intact** — `Fact()` still raises for direct construction (test_projection.py:117-128 stays green). Only the *reducer's call site* softens to a rejection, consistent with how the reducer already handles every other malformed op. This is the architecturally-correct boundary: the model enforces the invariant; the engine tolerates bad input by recording it.

**Files touched:** `reducer.py` only (~5 lines).

---

## Verification (all must pass before commit)
1. `cd engine && .venv/bin/pytest -q` — all 104 existing tests green (plus any new ones).
2. `cd engine && .venv/bin/ruff check .` — clean.
3. New unit tests: `LLMClient` Protocol structural check (xAI/Gemini satisfy it); a custom stub client drives `LivePlanner`; reducer soft-rejects a belief-channel-violating fact; `MappingSource` pass-through for fixture-shaped input.
4. `run_live.py --scenario all` loads all 11 (convergence only where a key is available; the 3 new ones at least run without `KeyError`).
5. `python iw.py start` → `/catalog` returns 11 incidents; spot-check that opening one of the 3 new incidents in live mode doesn't 500.

## Commit strategy
One focused commit per part (A→E) on `main`, each green, so the history reads cleanly and any single part can be reverted if needed. Push to `origin/main` at the end.

## What I will NOT do
- Touch the engine core orchestration (`engine.py`, `controller.py`, `fold.py`, `ledger.py`, `journal.py`) — it's clean and composable already.
- Change the `_SYSTEM` prompt or how the LLM reasons — "don't interfere" with the live judgment path.
- Weaken the `Fact` model — its invariant stays a hard raise; only the reducer softens.
- Change xAI-first precedence — your existing live flow is untouched.