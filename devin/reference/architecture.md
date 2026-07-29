---
title:   The Investigation Workbench — architecture (engine + workbench)
scope:   iw
answers: what are the layers/packages, the three projections + fold, the capability seam, the runtime, and the UI — and where each lives
face:    is
status:  ~
anchors: file:engine/src/iw_engine/domain file:engine/src/iw_engine/domain/registry.py file:engine/src/iw_engine/domain/enums.py file:engine/src/iw_engine/domain/phase_result.py file:engine/src/iw_engine/graph/fold.py file:engine/src/iw_engine/graph/graph.py file:engine/src/iw_engine/graph/reducer.py file:engine/src/iw_engine/hypothesis/store.py file:engine/src/iw_engine/journal/journal.py file:engine/src/iw_engine/runtime/engine.py file:engine/src/iw_engine/runtime/planner.py file:engine/src/iw_engine/runtime/live_planner.py file:engine/src/iw_engine/runtime/llm_client.py file:engine/src/iw_engine/capability/layer.py file:engine/src/iw_engine/capability/sources.py file:engine/src/iw_engine/capability/mapping.py file:engine/src/iw_engine/api/server.py file:engine/src/iw_engine/playbooks/incident.yaml file:workbench/src/lib/api.ts file:workbench/src/lib/store.ts file:workbench/src/lib/useInvestigation.ts file:iw.py
tags:    architecture engine workbench projections capability-layer llm-seam
supersedes: ~
---

# The Investigation Workbench — Architecture

> Knowledge page (AS-BUILT). Drifts against code — code is truth. The governing model:
> **the engine orchestrates; the LLM judges; the playbook configures** (three authors, no overlap).

## Two repos

- **`iw` (code)** — the product: `engine/` (Python core) · `workbench/` (React/TS UI) · `design/` (PRDs) · `iw.py` (controller).
- **`iw-kb` (this repo)** — constitution + KB + ai-session law. No product code.

## Engine — layer map (`engine/src/iw_engine/`)

The package self-documents its layering in `__init__.py`: a **DATA layer** (pure types, zero I/O) under an
**APP layer**.

| Layer | Package | Responsibility |
|---|---|---|
| DATA | `domain/` | Typed vocabulary + rules, **zero I/O**. `enums.py` = closed vocabularies; `node/edge/fact/event/hypothesis.py` = typed graph elements; `operations.py` = the planner's only output channel; `phase_result.py` = the uniform phase contract; `playbook.py` + `spec.py` = declarative config; `subject.py` = domain-neutral `SubjectRef`. |
| DATA | `domain/registry.py` + `domain/{nodes,edges}/` | The typed registry — binds the closed vocabulary to specs; asserts catalog completeness at import (closure). `domain/catalog.py` renders the LLM-facing grammar from the registry so grammar can't drift from what the reducer accepts. |
| APP | `graph/` | Graph blackboard projection. `graph.py` = bi-temporal typed MultiDiGraph; `reducer.py` = layer-2 validator (typed ops → elements); **`fold.py` = the single fold/reduce + `rebuild()` replay**; `persistence.py` = crash-safe backing; `reify.py` · `resolver.py` · `tools.py` = **undocumented here — added after 2026-07-21, no KB coverage yet**. |
| APP | `hypothesis/store.py` | Hypothesis projection (`HypothesisStore`) — ranked, evidence-backed, Popperian promotion gate (`promotion_ok`). |
| APP | `journal/journal.py` | Append-only NDJSON journal — the **source of truth**; each entry holds the full `PhaseResult` delta (enables replay). |
| APP | `runtime/` | The thin deterministic orchestrator. `engine.py` = phase loop; `controller.py` = gate + routing; `planner.py` = judgment Protocol + `ScriptedPlanner`; `live_planner.py` = real-LLM planner; `llm_client.py` = the LLM seam; `session.py` = human-in-the-loop driver + write-gate; `scenarios.py` = incident registry; `loader.py` = YAML→Playbook; `live_fixtures.py`. |
| APP | `capability/` | Governed, mockable tools. `layer.py` = CapabilityLayer (resolve intent → adapter, gate, audit); `sources.py` = the one side-effecting fetch seam (Mock/Scenario/Mcp/Rest/Routed); `mapping.py` = vendor-JSON→adapter-shape; `adapters/` = 9 read + 1 write. |
| APP | `api/` | `bundle.py` = `export_bundle` (flatten 3 projections → one UI doc); `server.py:create_server` = **lazily-imported** FastAPI/SSE app factory (needs the `server` extra; the hermetic test suite never touches it). |

## The three projections + the one fold

Every phase emits the identical **`PhaseResult`** (`domain/phase_result.py`). A single **`fold()`**
(`graph/fold.py`) writes it into three stores: **graph** (facts/events/nodes/edges) · **hypothesis store**
(`hypotheses_updated`) · **journal** (`narrative`); `verdict` routes the controller. `rebuild(journal)`
replays the deltas to reconstruct graph + hypotheses — proven equivalent by
`tests/unit/test_projection.py:test_fold_and_replay_equivalence`. Evidence edges (SUPPORTS/REFUTES) are
**derived inside the fold** (`fold.py:_project_evidence_edges`), never planner-emitted.

## The capability seam (governed tools)

The engine calls abstract capability verbs, never tools. `CapabilityLayer.serve` is **gate-first**:
resolve → gate → `Source.fetch` → `normalize` (`layer.py:168-177`), so a blocked write never reaches the
transport. The single side-effect boundary is `Source.fetch` with five transports (`capability/sources.py`):
`MockSource` (hermetic tests), `ScenarioSource` (live-run fixtures, provider→phase→blob), `McpSource`,
`RestSource`, `RoutedSource`. `mapping.py:map_response` translates vendor JSON → adapter shape (translators
for Prometheus/ServiceNow/Splunk/Git; rest pass-through). Adapters (`capability/adapters/`): 9 read
(Prometheus, Splunk, AppD, ServiceNow, Cmdb, Ocp, Artifactory, Git, **BigPanda**) + 1 write
(`RemediationAdapter`, **not** in `ALL_ADAPTERS`/`default_adapters()` — wired inline in `runtime/scenarios.py`).

## The live path + LLM seam

`runtime/llm_client.py` is the swappable judgment seam: `Protocol LLMClient` = `name` + `complete_json(system,
user) -> dict`; **all HTTP is stdlib `urllib` — no provider SDK**. Two providers wired: `XaiClient`
(OpenAI-compatible, default model `grok-4.5` — the default live path) and `GeminiClient` (default
`gemini-2.5-flash-lite`). Anthropic / OpenAI / local are **documented as "how to plug in," not wired** — a
~30-line class + a `_PROVIDERS` tuple. Selection precedence (`make_llm_client`): `IW_LIVE_PROVIDER` →
`XAI_API_KEY` → `GEMINI_API_KEY` → legacy key file → `None` (falls back to the scripted mock). The live
backend engages only when `IW_LIVE ∈ {1,true,yes}` and a key resolves (`api/server.py`).

## The workbench (`workbench/`)

React 18.3 + TypeScript 5.6 (strict) + Vite 5.4 + Vitest 2.1. Talks to the engine over a same-origin `/api`
proxy (`vite.config.ts` → `:8099`, gzip disabled so SSE isn't buffered) — REST + resumable SSE, never a
cross-origin call. `src/lib/api.ts` calls `/catalog`, `/sessions*`, `/sessions/{id}/{gate,advance,messages}`,
and the SSE `stream?after=N`. `src/lib/useInvestigation.ts` owns one investigation; `src/lib/store.ts` folds
the ordered event stream (`phase_started · reasoning · capability_call · graph_delta · hypotheses_delta · gate_opened ·
gate_decision · user_message · session_error · session_state`), idempotent by `seq`. Surfaces as SHIPPED:
StartScreen, Workbench, PhaseStepper, ChatPane (+ ApprovalCard write-gate), LiveGraph, HypothesisPanel,
ToolCallCard, ReviewCard, PostmortemCard, DiscoveryPanel, PanelControls. A static, server-free demo runs
from `workbench/public/demo-code-regression.json`.

## Tech stack (exact)

Python ≥3.11 (checked-in `.venv` = CPython 3.12.10) · **uv** (committed `uv.lock`) · setuptools/src-layout ·
ruff (line-length 120). Runtime deps: pydantic 2.13.4, networkx 3.6.1, pyyaml 6.0.3. `server` extra: FastAPI
0.139.2 + uvicorn 0.51.0 (lazy-imported). `dev` extra: pytest 9.1.1, ruff 0.15.22, hypothesis 6.158.0.
**123 tests, all passing** (`uv run pytest -q`).

## Cross-cutting concerns

- **Governance at the boundary** — `runtime/engine.py` passes the phase's `writes_allowed` to `CapabilityLayer.serve` → `_gate` (`capability/layer.py:141`); the gate is the single write-control point (no separate `govern()` in the shipped engine).
- **Journal-as-truth + bi-temporality** — `journal/journal.py` + `graph/graph.py:facts_valid_at`; reconstructable as of incident-start.
- **Zero engine tuning constants** — every knob in `playbooks/*.yaml` `tunables:`.
- **Controller `iw.py`** — stdlib-only cross-platform launcher; backend `:8099`, frontend `:5173`; pids/logs in gitignored `.iw/`.
