# Per-Edit Gate — The Investigation Workbench (LAW)

> Registered at boot as this project's per-edit gate (PE lifecycle Step 6). Fires before any **non-trivial**
> edit to the engine, workbench, or playbooks. A trivial edit (typo, comment, log string) is exempt; anything
> touching the typed model, the fold, a gate, an adapter, a phase, or a playbook contract is NOT trivial.

## Before the edit — consult

1. **`_index/data-model.md`** — does the edit touch any invariant (INV-1…INV-9)? If yes, the invariant and
   its enforcement site are the spec. Do not weaken enforcement to make an edit pass.
2. **`kb/architecture.md`** — which layer/module owns this? (DATA layer `domain/` = zero I/O; APP layer
   `graph`/`ledger`/`journal`/`runtime`/`capability`.) Keep the layering: `domain/` never does I/O.
3. **`kb/code-map.md`** — "change X → touch Y, Z". Multi-touchpoint edits (a new NodeType, a new adapter, a
   new phase) have a fixed set of sites that must move together.

## STOP conditions (a plan that hits one halts and surfaces before any write)

- **Adds a tuning constant to engine code** → violates INV-9. Route it to a playbook `tunables:` block.
- **Adds a NodeType/EdgeType/predicate without its spec** → breaks the import-time closure assertion (INV-3).
- **Adds a write path that bypasses `CapabilityLayer.serve` / `_gate`** → violates INV-1 (human-approval gate).
- **Makes the planner emit evidence edges (INV-6), or lets off-catalog planner output reach the reducer unrepaired (INV-7).**
- **Stores a time-varying attribute as a node prop** → violates INV-5 (must be a Fact).
- **Branches engine behavior on which LLM provider or which source** → violates the LLM-agnostic / domain-neutral goal. The seam absorbs it (`llm_client.py` / `capability/sources.py`), not the engine.

## Verify gate (a change is not done until this passes)

- **Engine tests green:** `cd engine && uv run pytest -q` — all **575** tests pass (baseline re-measured
  2026-07-26; was 123 on 2026-07-21. The number may only grow). A change that reduces the count or leaves a
  red test is not done.
- **A defect fix ships its regression test** — RED on the pre-fix code, green after. Demonstrate the red
  (revert the fix, run the test, quote the failure); a test that never failed proves nothing. If the fix is
  genuinely not automatable — a behavior-preserving deletion or refactor whose guard is structural — record
  the waiver AND its reason in the commit body. An unrecorded waiver reads as an untested fix later
  (measured: the 2026-07-22 fix wave was 5/6 explicit, and the 6th took a git archaeology to defend).
- **Lint:** `cd engine && uv run ruff check` (line-length 120; rules E,F,I,B,UP,RUF).
- **Workbench (if touched):** `cd workbench && npm run build` (`tsc --noEmit && vite build`) and `npm run test`
  (vitest) green.
- **Runs locally (if the change is observable):** `python iw.py init && python iw.py start` → exercise the real
  journey at `http://127.0.0.1:5173`, don't just read code (PE verify discipline).

## Code-review gate

Every code-durability boundary (commit / merge / push / session-close) is reviewed. A commit reaching the code
repo carries a `Reviewed:` trailer (the PE code-review-gate). Constitution/doc commits to `iw-kb` are docs —
review is light (accuracy + boundary), but still gated at close.
