# The Investigation Workbench — Build Plan for an Autonomous Agent

> **Audience.** An autonomous software agent (or an engineer working alone) building this system
> end to end. You are assumed competent at Python and TypeScript and to have a shell, a checkout,
> and the internet for package installs.
>
> **You will run on a machine that has none of this project on it yet.** This folder is therefore
> self-contained: everything the plan tells you to read is in here, and §3 starts from an empty
> machine. You need exactly two things from outside: network access, and read/write access to the
> two Git repositories named in §3.1.
>
> **What is in this folder**
>
> | File | What it is |
> |---|---|
> | `devin-plan.md` | this plan — the ordered stages and how to verify each one |
> | `iw-target-architecture.drawio` | the three architecture pages. Open at app.diagrams.net (File → Open From → Device). Page 1 is the system in its estate, page 2 is the parts we build, page 3 is the runtime flow. **Read these before Stage 0.** |
> | `reference/data-model-invariants.md` | the nine invariants INV-1…INV-9 and where each is enforced — **law** |
> | `reference/per-edit-gate.md` | the STOP conditions and the verify gate — **law** |
> | `reference/incident-playbook.yaml` | the domain configuration: phases, gates, allowed tools, tuning values |
> | `reference/architecture.md` | the layers, the three projections, the capability seam, the runtime |
> | `reference/code-map.md` | "change X → also touch Y and Z" — the multi-file edit sites |
> | `reference/stage1-findings-and-decisions.md` | the audit this plan is built on, plus twelve owner decisions that are still open |
>
> The `reference/` files are **snapshots** taken when this folder was written. The originals live in
> the separate `iw-kb` repository under `ai-session/` and `kb/`. If the two ever disagree, the
> originals win — but you do not need that repository to complete this plan.

---

## 0. The one constraint that shapes everything

**You do not have access to any AI or LLM system.** No OpenAI, no Anthropic, no Gemini, no
Tachyon, no local model. Not for building, not for testing, not for verifying.

This is not a limitation to work around — the system is designed so that it does not matter:

- The product's "judgment" component (choosing the next investigative step, proposing causes) is
  the **only** thing that needs a model, and it sits behind one seam: `runtime/planner.py`'s
  `Planner` protocol. Everything else — the typed model, the graph, the fold, the gates, the
  stores, the API, the UI — is deterministic code.
- A **ScriptedPlanner** already implements that same protocol by replaying a fixed list of
  operations. Twelve end-to-end scenarios drive the whole system through it.
- Therefore: **every stage below is built and verified with zero model access.** Where a stage's
  behaviour would normally involve a model, you verify it with a scripted scenario and a
  byte-for-byte golden comparison instead.

**The rule you will apply hundreds of times:** if a verification step needs a model, it is the
wrong verification step. Rewrite it as a scripted scenario plus an assertion.

**What you may NOT do:** do not sign up for a model API, do not add a model dependency, do not
mark a stage "verified" on reasoning alone, and do not delete or weaken a test to make a stage
pass. If a stage genuinely cannot be verified without a model, stop and escalate (§8).

---

## 1. What you are building

**One sentence.** A governed, human-in-the-loop incident-investigation engine: it gathers
evidence about a production incident, ranks candidate causes, and never changes production
without a human approving that specific action.

**The invariant. Everything you build must preserve it:**

> Every production-changing action is **human-approved**, **reversible**, and **reconstructable
> back to its evidence.**

Three consequences you will meet constantly:

1. **Human-approved** — a write-effect tool call is refused unless a human approval for that phase
   exists on the durable journal. There is exactly one enforcement point.
2. **Reconstructable** — the append-only journal is the source of truth. The graph and the
   hypothesis ledger are *projections* of it. Replaying the journal must rebuild both **exactly**.
3. **Reversible** — every state change is an appended record, never an in-place edit.

**Five domain goals** (from the project's operating frame):

- *Govern, don't automate* — the engine proposes and reconstructs; it never acts unattended.
- *Stay domain-neutral* — a new domain is a new playbook + entity registry, never an engine change.
- *Live-capable and model-agnostic* — no engine code branches on which model or which data source.
- *Keep the core thin* — one typed graph, one uniform `PhaseResult`, one fold into three projections.
- *Make governance legible* — the UI shows the graph, the ranked causes, the record, and the gates.

---

## 2. Repositories, layout, and what already exists

This is a **two-repository** project.

| Repo | Holds |
|---|---|
| `iw` (code) | `engine/` (Python), `workbench/` (React + TypeScript), `iw.py` (controller), `design/` |
| `iw-kb` (knowledge) | `ai-session/` (project state, law, research), `kb/` (architecture docs, diagrams) |

**Engine package layout** — `engine/src/iw_engine/`:

```
domain/       the typed model. ZERO I/O. enums, registry, specs, the Assertion atom,
              Fact/Event view records, operations (the closed op grammar), PhaseResult,
              the semantic dictionary, the playbook schema, the LLM-facing catalog renderer
graph/        reducer (validate + materialise ops), fold (THE single write seam),
              graph (bi-temporal store), resolver + reify (identity), tools (governed queries)
hypothesis/   store (the ranked ledger + promotion gate), belief (evidence arithmetic)
journal/      the append-only NDJSON journal, schema-versioned
runtime/      engine (the phase loop), controller (gates + routing), session (interactive
              driver), planner (the Planner protocol + ScriptedPlanner), live_planner,
              llm_client, loader, scenarios (composition root), store (disk persistence)
capability/   layer (the one governed seam out), registry + policy, sources (transports),
              adapters/ (ten vendor adapters), mapping
api/          server (FastAPI + SSE), bundle (the projection served to the UI)
playbooks/    incident.yaml — phases, gates, allowed intents, tunables. DATA, not code.
```

**Baseline you inherit** (measure it yourself in Stage 0, do not trust this table):

| Thing | Count |
|---|---|
| Engine tests | 581, hermetic, ~1.5 s |
| Workbench tests | 85 |
| End-to-end scenarios | 12 |
| Golden bundles | 12 files (**only 9 are asserted — a real gap you fix in Stage 1**) |
| Capability adapters | 10 |

---

## 3. Environment setup — starting from an empty machine

### 3.1 Get the code

**This folder ships inside the code repository**, so one clone gives you the plan and the code
together. The repository is **private**: if `git clone` prompts for credentials or fails with a
permission error, stop and escalate (§8) — access is a human action and retrying will never
produce it.

```bash
git clone https://github.com/innamulhassan/iw.git
cd iw
ls devin/            # this plan and its reference material are already here
```

Everything you build lives under `engine/` and `workbench/`; `iw.py` at the root is the
controller. You do **not** need any other repository to complete this plan.

*Optional, only if you want the originals behind this folder's `reference/` snapshots:*
`git clone https://github.com/innamulhassan/iw-kb.git` — also private, same escalation rule.

### 3.2 Prerequisites

| Tool | Version | Why |
|---|---|---|
| Python | 3.12 or later | the engine |
| `uv` | current | the engine's package manager — **not** pip |
| Node.js | 20 or later | the workbench |
| Git | any current | both repositories |
| PostgreSQL | 16 | configuration store — **only from Stage 6** |
| MongoDB | 7 | investigation store — **only from Stage 7** |

Nothing else is assumed about the machine: no particular operating system, no pre-existing
database, no preinstalled toolchain, no configuration inherited from anywhere. Choose your own
ports and connection strings and write them down; nothing in the build depends on a specific one.

### 3.3 Install and take a baseline

Run these in order. Each must succeed before the next.

```bash
# Python toolchain — the engine uses uv, not pip
curl -LsSf https://astral.sh/uv/install.sh | sh

# Node toolchain — v20 or later
node --version && npm --version

# Engine dependencies + a first green run
cd engine && uv sync && uv run pytest -q

# Workbench dependencies + a first green build
cd ../workbench && npm ci && npm run build && npm run test
```

**Do not proceed if either suite is red.** A red baseline makes every later verification
meaningless, because you cannot tell your breakage from inherited breakage. If the baseline is
red, stop and escalate (§8) with the failing test names.

**Datastores** (needed from Stage 6 onward, not before):

```bash
# PostgreSQL 16 — the CONFIGURATION store
# MongoDB 7    — the INVESTIGATION store
# Local installs or containers are both fine; record the connection strings you used.
```

---

## 4. How you verify anything, ever

This section is the heart of the plan. Read it twice.

### 4.1 The verify gate — run this at the end of every stage

```bash
cd engine    && uv run pytest -q          # ALL tests pass; count may only GROW, never shrink
cd engine    && uv run ruff check         # clean; line-length 120, rules E,F,I,B,UP,RUF
cd workbench && npm run build             # tsc --noEmit && vite build
cd workbench && npm run test              # vitest
```

A stage is **not done** until all four are green and the engine test count is greater than or
equal to where it started.

### 4.2 Deterministic end-to-end verification, with no model

The scripted path replaces the model entirely:

```bash
# Force the test-only scripted planner regardless of any other configuration
IW_SCRIPTED=1 uv run pytest -q tests/e2e
```

Twelve scenarios drive full investigations through the real engine, the real reducer, the real
fold, the real journal and the real gates. The only substituted part is judgment.

### 4.3 The golden bundles — your strongest tool

Each scenario has a committed golden bundle: the exact JSON the system produces. Any behaviour
change shows up as a byte diff.

```bash
uv run pytest -q tests/e2e/test_golden.py     # compare against committed goldens
uv run python scripts/gen_golden.py           # REGENERATE — only when a change is intended
```

**How to use goldens correctly:**

- Making a change you believe is behaviour-preserving? Run the golden test **without**
  regenerating. It must pass untouched. That is your proof.
- Making a change you intend to alter behaviour? Regenerate, then **read the diff line by line**
  and confirm every changed line is a change you meant. Commit the diff review in the message.
- Never regenerate goldens to make a red test go green. That converts a caught bug into a
  committed bug.

### 4.4 The replay proof

The single most important invariant test: fold ≡ replay. Rebuilding the graph and hypothesis
store by replaying the journal must produce byte-identical state. If you touch the reducer, the
fold, the journal, or any store, this test is your gate:

```bash
uv run pytest -q tests/unit/test_projection.py
```

### 4.5 Defect fixes need a demonstrated-RED test

When you fix a bug: write the test first, **run it and watch it fail**, then fix, then watch it
pass. Quote the failure in the commit body. A regression test whose red you never saw proves
nothing. If a fix is genuinely not automatable (a behaviour-preserving deletion, a pure refactor
whose guard is structural), record the waiver **and the reason** in the commit body.

### 4.6 Running the product

```bash
python iw.py init      # install both sides
python iw.py start     # backend + frontend
python iw.py status    # health
python iw.py stop
```

Without model credentials the backend falls back to the scripted planner and says so loudly.
That is expected and is enough to exercise the UI, the graph, the gates and the record.

---

## 5. The build stages

Each stage below states its **goal**, the **files** it touches, the **steps**, and — the part that
matters — **how you verify it without a model**. Do them in order. Do not start a stage until the
previous one's verification is green and committed.

### Stage 0 — Understand and baseline (no code changes)

**Goal.** Know what exists before changing anything, and record a trustworthy baseline.

**Steps.**
1. Read the three diagram pages in `iw-target-architecture.drawio`.
2. Read `reference/data-model-invariants.md` (in this folder) — the nine invariants INV-1…INV-9 and
   where each is enforced. These are law; a change that weakens one is wrong by definition, not a trade-off.
3. Read `reference/per-edit-gate.md` (in this folder) — the STOP conditions before any engine edit.
4. Read `reference/incident-playbook.yaml` (in this folder) end to end — it is a snapshot of
   `engine/src/iw_engine/playbooks/incident.yaml`. It is small and it is the entire domain
   configuration.
5. Run the four gate commands from §4.1 and **write the numbers down**. That is your baseline.

**Verification.** Both suites green; numbers recorded.

**Done when.** You can state, without looking: the five phases, what each gate checks, and which
module is the single write seam.

---

### Stage 1 — Protect the substrate

**Goal.** Make the safety nets whole *before* you rely on them. Nothing else lands until this does.

**1a. Complete the golden net.** `scripts/gen_golden.py` generates 12 goldens;
`tests/e2e/test_golden.py` asserts only 9. Three scenarios (cache, featureflag, certificate) are
regenerated but compared against nothing.

- Derive **one** scenario list used by both the generator and the assertion (e.g. a shared
  constant in `tests/e2e/__init__.py`), so they can never drift apart again.
- Add a meta-assertion: every file in the golden directory appears in the list.
- Fix the stale "11 goldens" comments in `test_reject_repair_journal.py` and `test_dictionary.py`.

**Verification.** `uv run pytest -q tests/e2e/test_golden.py` shows **12** parametrised cases
passing. If one of the three newly-asserted goldens fails, you have found a real latent defect —
investigate it, do not regenerate over it.

**1b. Protect the committed demo estate.** There is a committed demo investigation under
`engine/data/investigations/`. Creating a session with the same subject id calls `store.reset`,
which **deletes it**.

- Add a protection marker (e.g. a `.protected` file) beside committed snapshots.
- Make `InvestigationStore.reset` refuse to delete a marked snapshot and surface a clear error.
- Make the session layer return "this id is a committed demo — choose another" rather than
  silently destroying it.

**Verification.** A unit test: `reset` on a marked key raises and the files still exist afterwards
(assert on the filesystem). Then, manually: `git status` must show no deletions after running the
scripted suite.

**Done when.** Both suites green, 12/12 goldens asserted, demo provably undeletable.

---

### Stage 2 — Correctness fixes with regression tests

**Goal.** Fix the defects that are known and cheap, each with a demonstrated-RED test. All of
these are behaviour-narrowing: they make wrong things impossible, they do not add features.

Work through them one commit at a time:

1. **Tuning constants must live in the playbook, not in code.** `runtime/live_planner.py` fills a
   missing reliability with a hardcoded `0.9` and an unknown confidence with `0.6`, overriding the
   per-source values the playbook declares. Return `None` and let the reducer apply the playbook's
   value; fall back to the declared confidence band, not a literal. *(Invariant INV-9.)*
2. **Evidence citations must be sanitised.** The hypothesis store inserts first-CREATE evidence
   lists unsorted and un-deduplicated, so a duplicated id double-counts in scoring and an id in
   both the supporting and refuting lists counts twice. Normalise at the store's single entry
   point, matching what the merge path already does.
3. **Unknown references must be rejected, not silently skipped.** An update naming an unknown
   hypothesis id is currently dropped without a trace. Emit a journaled rejection.
4. **Retracted evidence must stop supporting a conclusion.** The fold still projects an active
   evidence edge from a retracted assertion. Skip retracted assertions when reconciling.
5. **Spans must be retractable.** The retract path covers facts, events and edges but not spans,
   while the query layer already filters for retracted spans that can never exist.
6. **A malformed event must not crash the run.** One materialisation lane lacks the
   reject-and-continue guard its siblings have; a model-valid-but-incomplete event raises out of
   the fold. Wrap it and journal a rejection.
7. **The gate must not consume the step budget.** Suspending for human approval currently burns a
   step from the maximum, so an investigation can exhaust itself waiting for a human.
8. **The client must survive a mangled response.** The default provider client parses with a bare
   JSON load rather than the salvage helper the module already exports, and ignores the server's
   retry hint. *(You can and must test this with a stub client — no network, no model.)*

**Verification per item.** Write the test, run it, **watch it fail**, fix, watch it pass, run the
full suite, run the golden test **without regenerating**. Goldens must stay byte-identical for all
eight — every one of these is either on a path the scripted scenarios do not traverse, or narrows
an error case the goldens never hit. If a golden *does* change, stop: either your fix is broader
than you think, or you found a second defect.

**Done when.** Eight commits, eight demonstrated-RED tests, suites green, goldens untouched.

---

### Stage 3 — Close the test gaps

**Goal.** Cover the parts that currently have no net. All additive; no behaviour changes.

1. **The HTTP and SSE surface has zero tests.** Nine endpoints, the error-to-status mapping, the
   event-stream framing, the cursor semantics, and the restart-reopen path are all unverified. Add
   a test module using the framework's test client, driven by the **scripted** backend.
2. **The write-approval UI has no test**, while its read-only sibling has four. Cover: approve,
   refine-with-edited-parameters, deny-with-reason, the busy state, and the decided state.
3. **The playbook validator has no test.** It promises that a typo in a phase reference is a loud
   load error. Prove it: duplicate ids, an undeclared entry phase, a bad verdict route, a bad
   ceiling key.
4. **Concurrency is covered by a single test.** Add one that drives two investigations
   concurrently through a shared manager and asserts no cross-talk in their event streams.
5. **The shipped fixture data is never exercised.** Add one parametrised test that folds every
   shipped fixture through its adapter and asserts zero reducer rejections.

**Verification.** Engine and workbench counts both increase; everything green. For each new test,
prove it can fail: break the thing it covers locally, see red, revert.

---

### Stage 4 — Clean the structure

**Goal.** Remove what is dead and fix what lies. Every item is behaviour-preserving; the suites
plus the goldens are the proof.

- Delete verified-dead exports and helpers (grep for callers first, across `src`, `tests` and
  `scripts` — if there are none, delete; if the only callers are tests, decide deliberately).
- Delete the dead demo-bundle script and its generated asset, and correct the README, which still
  documents a seven-phase flow that no longer exists and an adapter count that is wrong.
- Publish two underscore-private helpers that are imported across module boundaries (a private
  name used by another package is public API wearing a disguise).
- Consolidate the test fixture layout: move the shared helper module out of the end-to-end folder
  (sixteen unit tests import from it), add a `conftest.py` for the fixed clock and playbook path,
  and stop one test file doubling as a fixture module for others.
- Correct the project's law file where it cites paths that no longer exist.

**Verification.** Suites green, **goldens byte-identical**. For deletions, record the waiver and
the grep evidence in the commit body.

---

### Stage 5 — Finish the domain model on the wire

**Goal.** The typed model has one atom — an assertion carrying its own provenance — but the
delta that crosses the fold still carries two older record shapes, so a measurement's statistic
and time window are discarded at materialisation. Finish it.

**Steps.**
1. Replace the three per-shape lists on the phase result with one assertions list.
2. Carry the declared species, statistic and window from the operation into the store instead of
   re-deriving them from a name lookup.
3. Delete the converter seam and its hardcoded name allowlist.
4. Bump the journal schema version, and add a read path that lifts an older journal on load.
5. Add the symmetric validator: qualifiers that only make sense for a measurement are forbidden
   elsewhere.

**One decision you must not make alone:** ids. Today an assertion's id is derived from subject,
name and validity start. Once measurements survive the fold, two samples of the same metric with
the same window can collide. The id scheme per species is **unspecified** and is a one-way door.
**Escalate (§8) before implementing.**

**Verification.** This stage regenerates all twelve goldens — expected and acceptable. Therefore:
run the golden test *before* regenerating and capture the failures; regenerate; then **read every
changed line** and confirm each is intended. Add a test that a measurement with a statistic and
window survives the round trip, and a replay-compatibility test that an older journal still loads.

---

### Stage 6 — Move configuration into PostgreSQL

**Goal.** What the system is *configured to know* moves from files into the configuration store:
playbooks and their phases, the closed vocabulary and naming dictionary, the tool registry with
its allow/ask/deny policy, and the tuning values.

**Steps.**
1. Design the schema. Keep it boring: one table per concept, a version column on each.
2. Write a loader that reads the configuration at session start and produces exactly the same
   in-memory objects the file loader produces today.
3. Write a one-time importer that reads today's `incident.yaml` and the in-code dictionary and
   writes them into the database.
4. Keep the file loader working behind a flag, for tests and for local runs without a database.

**Verification — this is the good part.** You have a byte-exact equivalence oracle. Run the full
scripted suite against the file loader, then against the database loader, and require **identical
goldens**. If a single byte differs, the migration is wrong. No model needed, no judgement calls.

Also add: a loader test that a missing or malformed configuration row fails **loudly** at load
time, never silently at run time.

---

### Stage 7 — Move investigation data into MongoDB

**Goal.** What actually *happened* moves into the investigation store: the append-only journal
(authoritative), plus the graph and hypothesis snapshots rebuilt from it.

**Steps.**
1. Model the journal as an append-only collection keyed by investigation and sequence, with a
   unique index on that pair. Appends must be strictly ordered and must never overwrite.
2. Store the graph and hypothesis snapshots as caches with a journal-sequence watermark, exactly
   as the file store does today. On load, if the watermark is stale or the cache is corrupt,
   **rebuild from the journal** rather than trusting the cache.
3. Keep the file store behind a flag for hermetic tests.

**Verification.**
- **The replay proof is your gate.** For every scenario: run it, then rebuild graph and hypothesis
  store purely by replaying the journal from the database, and assert byte-identical state.
- **A restart test.** Persist an investigation, drop the process, rebuild from the store, and
  assert the reopened state equals the pre-restart state.
- **A tamper test.** Corrupt a cached snapshot deliberately, reopen, and assert the system detects
  the stale watermark and rebuilds rather than serving corrupt state.
- **Append-only enforcement.** Assert that writing a duplicate sequence number is rejected by the
  database, not merely by application code.

---

### Stage 8 — Governance: make approval enforceable at the boundary

**Goal.** Today the capability layer refuses a write based on a boolean its caller hands it. The
interactive driver sets that boolean only after a human approves — but a different driver could
simply pass `true`. The invariant is upheld by convention, not by construction. Fix that.

**This is the highest-risk stage in the plan. Read all of it before writing code.**

**The change.** The layer must *verify* evidence of approval rather than *be told* about it: an
approval token bound to one specific call, minted by the human gate decision and recorded on the
journal.

**Design questions that must be answered before implementation** — escalate (§8):
- Who mints the token, and what does it bind to (intent alone? intent plus exact parameters)?
- Does editing a parameter during approval invalidate the token? (It should.)
- Is it single-use? Does it expire?
- What does a batch or scripted run present at the gate — or are write-capable batch runs simply
  refused? Note: today the write phase deliberately declares no approval gate precisely because
  scripted runs have no human; declaring one naively stalls every scripted run.

**Verification.** A test that a write is refused when the token is absent, when it is for a
different intent, and when the parameters were edited after minting. A test that the interactive
approve path still works end to end. And a scripted-run test proving the chosen batch policy —
whichever way the escalation resolves.

---

### Stage 9 — Controller reliability and the final sweep

**Goal.** Make the launcher trustworthy, and finish.

1. **Do not trust a stored process id alone.** Record a process fingerprint at start and verify it
   before signalling; a recycled id can otherwise cause the controller to kill an unrelated
   process. Prune stale records.
2. **Fix the platform-specific process handling** — the liveness check depends on an
   English-language prefix in command output and will report a dead process as alive on a
   localised system; the graceful stop uses a flag combination that fails for console processes.
3. **Report start failures.** Today a child that dies immediately still records its id and exits
   zero after a long wait. Poll liveness during the wait, print the last lines of the log, and
   return non-zero.
4. **Fix argument precedence on restart** — explicit flags must beat stored state, not the reverse.
5. **Propagate configuration to the child process**, including the data-root setting.

**Verification.** Unit-test the platform seams with captured command output for both an English
and a localised system (these are pure string functions once extracted — no platform needed). For
the process behaviour, use a fake process that dies on cue and assert the exit code, the absence
of a recorded id, and the log tail.

**Final sweep.** Run everything: both suites, lint, build, the full scripted end-to-end set, and a
manual `init` / `start` / exercise the UI / `stop` cycle. Record the numbers against the Stage 0
baseline.

---

## 6. Standing decisions you must not reverse

These were decided deliberately. If your instinct says to change one, you have misread something —
escalate instead.

| Decision | Why it looks wrong, and why it is not |
|---|---|
| The chat **is** the record | A separate timeline panel was deliberately deleted. Do not rebuild one. |
| One loop, one shared context, no sub-agents | A delegation hook exists and is deliberately left unwired. Everything enters through the one capability seam; that seam is the anti-divergence invariant. |
| The scripted planner is for tests only | It is the CI net, not a product mode. The product's default is a live planner. |
| The committed demo snapshot is sacred | It is the deterministic reference. Live runs use a separate data root. |
| Domain neutrality is a one-way door | A new domain is a new playbook plus registry entries — never a branch in engine code. |
| No engine branch on provider or source | Provider differences live behind the client seam; source differences behind the transport seam. |
| Every tuning value lives in the playbook | A constant in engine code is a defect, not a convenience. |

---

## 7. Definition of done for the whole build

- Both suites green, with counts **at or above** the Stage 0 baseline.
- Lint clean; workbench build clean.
- All twelve scenarios pass and all twelve goldens are asserted.
- Replay equals fold for every scenario, from the database-backed stores.
- An investigation survives a process restart.
- A write is provably impossible without a human approval bound to that call.
- `python iw.py init && python iw.py start` yields a working UI with no model configured.
- Every defect fixed during the build has a regression test whose red you personally observed.

---

## 8. When to stop and ask a human

Stop and escalate — do not guess — if any of these occur:

1. **The Stage 0 baseline is red.** Report the failing test names.
2. **The assertion id scheme (Stage 5).** A one-way door; it must be decided, not inferred.
3. **The approval token mechanism (Stage 8).** Who mints it, what it binds to, whether editing a
   parameter invalidates it, and what a batch run presents. The invariant depends on this.
4. **A golden changes when you expected it not to.** Do not regenerate. Report the diff.
5. **The replay proof fails and you cannot see why within a bounded effort.** This invariant is the
   product's core promise; a wrong "fix" here is worse than a delay.
6. **Any change that would weaken one of the nine invariants** to make something pass.
7. **Anything that would require a model** to build or verify.

When escalating, include: what you were doing, the exact command, the exact output, and the two or
three options you see with your recommendation. Then wait.

---

## 9. Command reference

```bash
# Gate — run after every stage
cd engine    && uv run pytest -q
cd engine    && uv run ruff check
cd workbench && npm run build
cd workbench && npm run test

# Deterministic end-to-end, no model
cd engine && IW_SCRIPTED=1 uv run pytest -q tests/e2e

# Goldens
cd engine && uv run pytest -q tests/e2e/test_golden.py    # compare
cd engine && uv run python scripts/gen_golden.py          # regenerate (deliberate changes only)

# The replay proof
cd engine && uv run pytest -q tests/unit/test_projection.py

# Product
python iw.py init | start | status | logs | stop | restart
```

**Glossary.**
*Phase* — one step of the investigation (frame, investigate, act, verify, close), declared as data.
*Gate* — a predicate that must hold before a phase may end; failing it repeats the phase and feeds
the reason into the next plan.
*Fold* — the single function permitted to write into the three projections.
*Projection* — graph, hypothesis ledger, journal. The journal is authoritative; the other two are
rebuilt from it.
*Capability* — a governed call to the outside world. All of them pass one gate-first seam.
*Playbook* — the domain configuration: phases, gates, allowed tools, tuning values.
