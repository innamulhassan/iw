---
title:   The Investigation Workbench — code map (change X, touch Y, Z)
scope:   iw
answers: which files move together when you add a node type, an adapter, a phase, a domain, an LLM provider, or an incident fixture
face:    is
status:  ~
anchors: file:engine/src/iw_engine/domain/enums.py file:engine/src/iw_engine/domain/nodes file:engine/src/iw_engine/domain/edges file:engine/src/iw_engine/capability/adapters file:engine/src/iw_engine/capability/mapping.py file:engine/src/iw_engine/domain/catalog.py file:engine/src/iw_engine/playbooks/incident.yaml file:engine/src/iw_engine/runtime/loader.py file:engine/src/iw_engine/runtime/scenarios.py file:engine/src/iw_engine/runtime/llm_client.py file:engine/src/iw_engine/runtime/live_fixtures.py file:engine/scripts/gen_golden.py file:engine/scripts/build_demo.py file:workbench/src/lib/store.ts file:workbench/src/lib/useInvestigation.ts
tags:    code-map touchpoints extension-seams gotchas
supersedes: ~
---

# The Investigation Workbench — Code Map

> Knowledge page (AS-BUILT). "If you change X, touch Y, Z." Keyed by intent, not by file. The surprises a
> contributor would otherwise discover by a failing import-time assertion or a half-wired live run.

## Add a NodeType / EdgeType / predicate

1. `domain/enums.py` — add the closed-enum member.
2. `domain/nodes/<tier>.py` or `domain/edges/<group>.py` — add its **spec** (`NodeSpec`/`EdgeSpec`).
3. An **import-time closure assertion** (`domain/registry.py` + `domain/nodes|edges/__init__.py`, raising `RuntimeError('registry incomplete')`) fails at test collection until step 2 lands. Do both in one change.
- The LLM-facing grammar is auto-rendered from the registry (`domain/catalog.py:render_catalog`/`render_nodes`/`render_edges`) — **no prompt edit needed.**

## Add a capability adapter

1. `capability/adapters/<x>.py` — the descriptor pair (`provider` · `intents` · `effect` · `binding` · `meta` · deterministic `normalize`).
2. `capability/adapters/__init__.py` — register the READ adapter in `ALL_ADAPTERS`, which IS `default_adapters()` (`[cls() for cls in ALL_ADAPTERS]`). A WRITE adapter like `RemediationAdapter` is **not** in `ALL_ADAPTERS`; it's wired inline where a write path is intended (`runtime/scenarios.py`: `[*default_adapters(), RemediationAdapter()]`).
3. `playbooks/incident.yaml` — add its intents to the relevant phase `allowed_intents` **and** the top-level `capabilities` list.
4. `capability/mapping.py` — add a live translator `(provider, intent)` if going live; add fixtures.
5. `engine/tests/unit/test_cap_<x>.py` — a unit test.
- The tool the LLM sees is auto-rendered from `meta` (`domain/catalog.py:render_tools`) — **no prompt edit.**

## Add a phase

1. `playbooks/incident.yaml` — add the phase to `phases[]` (`allowed_intents` · `gate` · `produces_required` · `on_verdict` routes).
- **The engine needs no change** — the phase loop, fold, and routing are contract-driven off the playbook.

## Add a domain (the domain-neutral seam)

1. `playbooks/<domain>.yaml` — a new playbook (`id` · `applies_to` · `entry_phase` · `capabilities` · `phases[]` · one `tunables` block). Model it on `incident.yaml`.
2. `runtime/loader.py:load_playbook` loads it; pass `playbook_path` to `create_server` (default `_default_playbook()` → `incident.yaml`).
- **No engine change.** Same Node/Edge/Fact/PhaseResult shapes, registry, governance, console — only `graph_schema`, phases, outputs differ.

## Add an LLM provider

1. `runtime/llm_client.py` — a class with `.name` + `complete_json(system,user)->dict` (stdlib `urllib`; `XaiClient` is OpenAI-compatible, so an OpenAI/local endpoint is a small base-url subclass).
2. `runtime/llm_client.py:_PROVIDERS` — add the `(ENV_KEY, Class, default_model)` tuple; the selection cascade picks it up.
- Or, no registry edit: pass `client=` directly to `LivePlanner(...)` / `live_build_manager(client=...)`.

## Add a scripted incident fixture

1. `engine/tests/e2e/scenario_<key>.py` — `build() -> (subject, script[, fixtures])` + `test_<key>.py`.
2. `runtime/scenarios.py:_CATALOG` — register (`key`·`id`·`domain`·`title`·`layer`·`remediation`) so `/catalog` lists it and `POST /sessions` opens it.
3. `runtime/live_fixtures.py:LIVE_SCENARIOS` — add a builder `(SubjectRef, fixtures, golden_root)` for the live path.
4. `engine/scripts/gen_golden.py` — regenerate the golden oracle (`tests/e2e/golden/<key>.json`).
5. `engine/scripts/build_demo.py` — refresh the static UI demo if the demo scenario changed.

## The UI event contract

`src/lib/store.ts` folds the engine's ordered event stream; the event list lives in `src/lib/useInvestigation.ts:7-18`.
A new engine event type must be handled in **both** the `src/lib/store.ts` reducer and that list, and stay idempotent by `seq`.
LiveGraph shows creation-order **number badges** (`src/lib/store.ts:449` `nodesWithOrder`).

## Gotchas / fragile coupling

- **The LLM never holds the raw graph** — it is handed a **capped render** (`graph/render.py:render_slice` — the full graph capped at `max_nodes`≈40, focus node pinned first; a semantic focus-slice is a known, unimplemented gap). Widening what the LLM sees is a governance change, not a UI tweak.
- **`export_bundle` is the only UI contract** (`api/bundle.py`) — the UI reads the flattened 3-projection doc, not the internal stores. Changing a projection shape ripples to `bundle.py` → `lib/api.ts` → `lib/store.ts`.
- **`uv.lock` must be committed** — `iw.py init` / README assume it for `uv sync`. At takeover the code repo had `engine/uv.lock` **untracked** + `iw.py`/`README.md`/`TROUBLESHOOTING.md` modified-uncommitted. Commit uv.lock before relying on a clean `init`. *(Code-repo hygiene item — see PULSE debt.)*
- **`engine/data/{graph,journal,fixtures}/` are empty placeholders** — real live fixtures live in `runtime/live_fixtures.py`, hermetic ones in `tests/e2e/`.
