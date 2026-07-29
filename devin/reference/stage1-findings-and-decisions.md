# Stage-1 Plan — Understand · Refactor · Test, reconciled with the redesign

> **Session:** `45a875cb` (walk, `development`), 2026-07-29. Work row `14533aeb`; ratification row `102d7b31`.
> **Inputs:** 8 subsystem readers over the full codebase (engine 76 src + 78 test files, workbench 35 TS files,
> `iw.py`, scripts, configs) + cross-cut critic; 8 door analysts over the design set + shipped code; ~2.9M tokens
> of agent evidence, every load-bearing claim cited file:line. Owner directive: understand code, understand the
> redesign, filter the generic refactoring checklist, produce a staged plan — **no diagrams, no execution before
> owner approval.**

---

## 0. The headline discovery — the redesign is substantially SHIPPED

The book row `9bdfbbaf` ("EXECUTE redesign P1a–P8 post-ratification") and PULSE's "nothing downstream
should start before ratification" are **stale**. Verified against the tree (both workflows independently):

| Phase | Status in the shipped engine |
|---|---|
| P0 law-refresh | **NOT done** — `_index/data-model.md` last refreshed 07-21; INV-8 cites deleted `ledger/ledger.py`; INV-1/INV-4 line numbers wrong; theta note stale |
| P1a/P1b envelope | **Store half done** (Assertion atom, 6 species, one collection, compat shims deleted) — **wire half NOT done**: `PhaseResult` still carries `facts_added`/`events_added`; READING species dead end-to-end (`projection.py:39`) |
| P2 dictionary | **Shipped** (152 entries, reducer's name authority) — census gap open: 40/101 NodeSpec predicates diverge from dictionary |
| P3 airlock | **Shipped + green** (quarantine, rejections journaled, generic_ci structural, Merge/Retype) — asymmetry `8f9bcc00` open |
| P4 belief arithmetic | **Shipped** (`b47a905`) — but live_planner hardcodes 0.9/0.6 over tunables (INV-9 violation live today) |
| P5 identity/aliases | **Shipped** (`927d0fb`, `96f4fe0`) — ALIAS_SCHEMES still engine constants |
| P6 journal v2 + data/ | **Mostly shipped** (schema v2, durable `InvestigationStore`, restart reopen) — relayout/typed-payloads/renames open |
| P7 phase-as-data | **Shipped** (no Phase enum; 5-phase algebra in yaml marked "owner-ratified"; doctrine as data) — doctrine wiring gap; **approval-in-the-layer NOT built** |
| P8 capability registry | **Written but wired NOWHERE** — `CapabilityRegistry` + policy exist, zero production call sites; real transports absent |
| P9 iw.py + live re-test | **Open** (booked rows `eccba87a`, `4e9eb480`) |

**Consequence.** Ratification is largely retroactive; the walkthrough's real content is the ~12 live decisions
in §4. EXECUTION-LOG.md's "ALL ENGINE PHASES DONE (P0–P8)" overstates (P0 not done, P8 dormant); the book
understates. Both surfaces need reconciling at close — that mismatch is itself a finding.

## 1. What the checklist filter removed (the "nonsense")

Database code/migrations (files-no-DB ruling — nearest real thing is journal schema versioning = P6);
MCP/A2A tests (no such code yet = P8); public-API compat beyond the engine↔workbench wire (both consumers
in-repo); style-only rewrites (ruff owns style); diagrams/architecture docs (owner-excluded; already exist).
Standing decisions honored throughout: chat-is-journal, ONE loop/no delegation, ScriptedPlanner test-only,
INC-4821 demo preserved, domain-neutral, LLM-agnostic.

## 2. Findings inventory

106 raw findings → ~90 after cross-reader dedup. **2 critical, 15 high, 43 medium, 46 low.** Full details:
workflow journals `wf_3be45194` (subsystems) + `wf_12384436` (doors); condensed digests in the session scratchpad.

The two criticals:

1. **The committed INC-4821 demo estate is unprotected AND internally broken.** `iw.py start` never sets
   `IW_DATA_ROOT` (and `.iw/live.env` is read by nothing — grep-verified); the backend defaults its store to
   `engine/data/investigations/` — the committed demo directory; the catalog reuses id `INC-4821`; and
   `session.py:218` calls `store.reset` on create, which **unlinks the committed snapshot**. Separately the
   scripted-demo directory name breaks the store's key→dir bijection (its meta key resolves to the OTHER
   snapshot's directory). One user click can destroy the demo today.
2. **`live_planner.py:850/:827` hardcode belief constants** (0.9 reliability fill, 0.6 verdict fallback) that
   override the playbook tunables the reducer would apply — INV-9 violated on the live path now, not hypothetically.

## 3. The plan — three safe-now waves + a HOLD ledger

**The discriminator (one rule):** if a fix changes what the store/journal/prompt *holds*, it waits for its
phase; if it only guards, dedupes, tests, or deletes, it is safe now. Each wave = its own session(s) on
`development`, per-edit gate consulted, every defect fix ships a demonstrated-RED regression test, code-review
at every commit, engine 581 + workbench 85 green as the exit gate (both counts may only grow).

### Wave 0 — protect the substrate (~2 commits, do FIRST)
1. **INC-4821 protection as one unit:** `.protected` marker guard in `InvestigationStore.reset` (refusal
   surfaced to the UI), regenerate the scripted-demo snapshot under a distinct id via a real scripted run,
   add `dir == safe_key(meta.key)` + unique-id invariant tests.
2. **Complete the golden net:** ONE shared SCENARIOS list for `gen_golden.py` + `test_golden.py` (12/12 —
   today only 9 of 12 goldens are asserted; cache, featureflag, certificate regenerate unchecked), plus the
   first-divergence walker for readable failures; fix the two stale "11 goldens" comments.

*Why first: every later change leans on the goldens as its equivalence net, and the demo estate is one
misclick from gone. Nothing else lands until this does.*

### Wave 1 — behavior-narrowing guards (all golden-safe or golden-checked, each with a RED test)
Engine: live_planner belief constants → None/band-med (restores INV-9; P4 re-verifies); the evidence-citation
cluster (store first-CREATE dedup, unknown-hid Rejection, RETRACTED-skip in `_project_evidence_edges`, span
retraction lane, EVENT-lane try/except, non-dict-event notice); live-parser `add_edge` rejection (enforces the
prompt's own ban; adapters unaffected — their path never traverses `_parse_op`); XaiClient `loads_salvage` +
Retry-After + forced-provider loudness (a fenced grok completion kills the session today); `Engine.step()`
gate-suspend budget burn; GeminiClient throttle lock; SSE tick reset-on-activity.
Controller: `VITE_API_TARGET` built from args (non-default ports are broken today).
Workbench: decide/review optimistic-state rollback on non-409; ToolCallCard outcome ordering (error/blocked
must not render as "data"); `awaiting_review` badge; StartScreen session-id-from-list.

### Wave 2 — test infrastructure + hygiene (mechanical; waivers recorded per the per-edit gate)
The API TestClient suite (the one untested layer — 9 endpoints, SSE framing, 409s, restart-reopen) + typed
`mkSnap`; ApprovalCard component tests (the write-gate UI has zero, its sibling ReviewCard has four);
`Playbook._validate_phase_refs` error-path tests; `tests/conftest.py` + `_helpers` relocation (16 unit files
import from `e2e/`, fixtures live inside a test file); dead-code deletions (registry dead exports +
DESCRIPTOR alias, `Graph.reachable_from`, `build_demo.py` + its orphan JSON + README demo-pipeline rewrite,
`Journal.read`, live_planner underscore aliases, iw.py cosmetics, redundant TS casts); the "ledger" test-name
sweep; workbench format-helper consolidation; README 7-phase→5-phase + adapter-count fixes; decide the unused
`hypothesis` dev-dependency (delete or spend on a property test).
Plus the two audits the fan-out missed: a line-audit of `api/bundle.py` + `runtime/postmortem.py` (the un-owned
projection module P6 will move), and the injection/`dangerouslySetInnerHTML` one-grep + a session-internal
thread-safety read (HTTP thread vs `_drive` daemon).

### HOLD ledger — findings that sharpen phases (do NOT patch in stage-1)
- **P0 (law-refresh, now urgent):** INV-8 path/mechanism/theta, INV-1 citations, INV-2 fold-seam wording,
  "ledger" wording sweep, DESIGN.md phantom paths, tunables-enumeration story, INV-3 restated as two
  disciplines (hard type closure / soft name closure), candidate **INV-10: outcome honesty** (no invariant
  covers error≠empty today), delta-margin ruling (see D6).
- **P1b completion:** native assertion materialization (READING reachable; the one high finding stage-1 must
  not touch), atom validator symmetry, journal v3 with v2 read-and-lift (= Door 1's decision D1).
- **P2 completion:** dictionary census (40 divergent predicates), `fact_predicates`/`static_props` retirement,
  registry⇄dictionary coherence assertion, the served **lane** map (Door 8 amended form) + tiers.ts derivation.
- **P4 residue:** promotion_verdict reason-carrying; delta-margin removal per D6.
- **P5 residue:** ALIAS_SCHEMES → registry data; `_slug`/`_atomic_write` publication.
- **P6 (the big one):** projections/ layer extraction (runtime↔api cycle), `materialize()` decomposition,
  ONE typed journal-payload deliverable spanning python+TS (incl. SSE event shapes), refs extension, fold-seam
  resolution (production runs journal-LAST; the tested `fold()` order has no production caller), schema-window
  renames (`entity_ref`→`subject_ref`, `supporting_facts`→`supporting_evidence`), `Snapshot.messages`
  retirement, restart-endpoint coherence, **engine-gated confirmation** (Door 5b, decision D4).
- **P7 residue:** doctrine-travels-with-playbook wiring (one line, P7's opening commit); **approval-token
  design doc before any code** (Door 6, decision D5).
- **P8:** wire `CapabilityRegistry` into production, `unavailable` outcome (decision D7), served_by provenance,
  Outcome StrEnum, effects-on-Protocol + single `effect_of`, adapter mint/fold helpers, remediation source
  attribution, `MappingSource.phase` forwarding, `default_at` clock, live-fixtures fold test.
- **P9:** full iw.py reliability set (pidfile fingerprinting, the Windows trio, restart precedence,
  start-failure surfacing, env propagation + the `.iw/live.env` convention ruling), TROUBLESHOOTING uv rewrite,
  `live_build_manager` hermetic smoke, final live re-test.

Net effect: **~30 of the ~90 findings execute pre-ratification** with the net whole and the demo protected;
the rest arrive as phase requirements with evidence attached, so no phase re-discovers them.

## 4. The live decisions (the walkthrough, distilled)

The doors' retroactive halves need only acknowledgement. These are the decisions that change what gets built:

- **D1 (Door 1).** Complete the envelope on the wire: `assertions_added` replaces the Fact/Event delta,
  journal v3 + v2 lift, converter seam deleted — or accept the half-state where READING is permanently dead.
  If yes: ratify the SIX-species model (the shipped one), not Part I's five.
- **D2 (Door 2).** Close "enum becomes validated-str" as REJECTED or DEFERRED-with-trigger (it is the one
  unphased one-way door); require registry⇄dictionary reconciliation as a ratification condition.
- **D3 (Door 2/8).** Authorize wiring `CapabilityRegistry` into production; mirror-derived or authored table.
- **D4 (Door 5b).** Make `confirmed` an engine decision (promotion_ok + playbook-marked phase; off-enum status
  → journaled rejection, today it crashes the run). The LLM currently writes the root-cause verdict directly.
- **D5 (Door 6).** Approval-in-the-layer: approve the DIRECTION, require the token mechanism spec'd as its own
  decision first. The shipped `act` phase declares **no `gate:` block** — batch path can serve a write with no
  human gate (only the interactive driver saves it). **Verifier correction (2026-07-29):** this is NOT a free
  YAML fix — EXECUTION-LOG P7 deferral #3 records it as deliberate (batch/golden runs have no human, so
  declaring the gate would stall every scripted run into REPEAT). The act-gate fix therefore folds INTO the
  token design: the mechanism must define what a batch run presents at the gate (or that batch write-capable
  runs are refused outright). Decide it as one thing, not two.
- **D6 (Door 5/P4).** The promotion `delta` margin is dead code post-strengthening — confirm it dies (like
  theta) or re-justify it.
- **D7 (Door 7).** Add the 4th outcome `unavailable` (tool never reached ≠ clean empty) + stop the workbench
  overriding engine outcomes — accepting the demo honestly degrades (ten "data" cards become "not connected").
- **D8 (Door 4).** Flip `derive_transitions` on only with the triple (retraction cascade, batch ordering,
  provenance marker)? And do you want the authorship BAN enforced INV-6-style?
- **D9 (Door 3).** The airlock asymmetry (`8f9bcc00`): does a KNOWN name on an unexpected subject quarantine
  like every other surprise (own phase; ~97% of subject/name pairs are illegal today) or stay rejected?
  Plus: may LLM-authored unknown names enter quarantine (today: yes; red-team: no)?
- **D10 (Door 8).** Lane as its OWN registry field served via the label dictionary (zero golden churn), not
  §2.7's literal derive-from-tier (unimplementable; red-team A18/A21/A49 stand unresolved).
- **D11 (program).** Reconcile the program record: book rows `9bdfbbaf`/`c39c0f37` vs EXECUTION-LOG vs PULSE.
- **D12 (stage-1).** Approve Waves 0–2 for execution (each wave its own session, gates as in §3).

## 4b. Doors-verifier addendum (landed 2026-07-29, after the plan's first commit)

The adversarial verifier executed the eight door accounts' load-bearing claims against the shipped code;
nearly all reproduced exactly (the 40/101 predicate divergence to the digit, the boolean `error_rate` on the
flagship journal, the model-authored `confirmed` at seq 16, the dead promotion-margin clause, the phantom
tiers.ts entries). Corrections and additions the plan now carries:

- **Count fixes (cosmetic):** `_PROPERTY_PREDICATES` has 16 names (not 21); EdgeType 37 (not 38); NINE
  phantom `allowed_intents` (not eight); several red-team attack numbers were misattributed in the accounts
  (A34 not A33 for promotion; A18/A49 for tier) — substance unaffected.
- **D5 correction:** the missing act gate is a recorded deliberate deferral, not an oversight — see D5.
- **Additions to the P1b build spec (D1):** red-team **A6 is unresolved by anyone** — the per-species
  assertion-id scheme (today `subject|name|valid_from` hashing) must be settled before READING survives the
  fold, or same-metric samples collide; plus the reading-survival knock-ons A30 (render-cap starvation of
  the current value) and A15/A46 (gate currency still counts `len(facts_added)` with no species rule).
- **Golden-regeneration sequencing rule:** D1-wire, D4-flip and D5b-gate each force a full golden
  regeneration — they must ride **ONE wave**, and **D4 strictly after D1** (flipping `derive_transitions`
  first would bake Fact/Event-shaped engine-minted records into durable journals and force a second wave).
- **Order forced by substrate:** a yes on D5 (approval token) **forces wiring the CapabilityRegistry (D3)
  first or together** — the registry is the declared substrate for approval state; the program's P7-before-P8
  ordering is inverted now that the rest of P7 has shipped.
- **Ratification order (verifier's synthesis):** Step 0 — the P0 law refresh is a BLOCKING precondition, not
  a door (every per-edit gate routes through law that is wrong at HEAD). Step 1 — the acknowledge-as-delivered
  votes (5a, 5c, 6's three shipped parts, 3's philosophy, 7's three-value taxonomy, journal v2), stated
  plainly as retroactive: a "no" means unwind. Step 2 — the live builds in dependency order: D2-reconcile →
  D3 registry wiring → D5 token design → the D1+D4+D5b single regeneration wave → D7 → D8/D10.
- **New acknowledgement item (fold into D11):** P4 and P5 shipped with NO ratification door at all — in
  particular §9.2's governed Merge relaxed the owner-facing "conflict = rejection" rule without a vote; and
  the "v1 journals load read-only" claim is verified by nobody.

## 5. Suite/tooling facts worth knowing (from the read)

Engine suite: **581 tests, 1.47s, hermetic**; workbench 85, 1.5s. INV-1..9 each have dedicated enforcement
tests; fold≡replay proven per scenario; gate journeys (approve/deny/refine) fully tested engine-side.
The untested layer is the HTTP/SSE wire (both sides). `reify.py`'s reaper is built but wired nowhere
(a lost span close reads as a live outage forever — flagged, needs an owner call on wiring vs booking).
Port law: MACHINE.md says iw = **5183**; PULSE/SESSION.md still say 5173 — fix at close.
