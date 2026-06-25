# The Investigation Workbench — Live Demo

A **self-contained**, no-build demo of a governed, human-in-the-loop investigation engine.
You watch an AI agent work a production incident through guided phases — building a graph, reasoning,
calling real tools in the browser — while a human approves at every checkpoint and **every write is gated**.

> **The split that matters:** the **agent is the brain** (you talk to it in VS Code Copilot or Claude — that's
> where the chat and the approvals happen). The **`viewer.html` is a window** — it only *visualises* the JSON the
> agent writes. The viewer drives nothing.

---

## ⚡ Quick demo (fastest path)

**Double-click `start-demo.command`** — it starts the local server and opens the worked walkthrough:

> ### → http://localhost:8000/viewer.html?incident=INC-2256

That's **`INC-2256`** — a complete, closed investigation (cart-svc 5xx → `rev-118` config flag → gated fix →
recovery): all four phases, the write gate, the human close. Click through the **phases** (left), read the
**conversation** (centre), explore the **graph** + **Phase record** (right). An instant, no-narration demo.

*(Server already running? Just open the URL above, or double-click **`quick-demo (INC-2256).webloc`**.)*

---

## 1. What's in the folder

```
demo/
├─ README.md                 ← you are here
│
│  ── DEFINITION FILES (the process — declarative, nothing hard-coded) ──
├─ playbook.json             the PLAN: phases · effects · gates · capability intents · step kinds
├─ playbook.md               the human-readable twin of playbook.json (+ the checkpoint rules)
├─ graph-schema.json         the TYPE SYSTEM: allowed node / edge / health types (the graph can't invent types)
├─ capabilities.json         the TOOLS: each intent → a tool URL + the fields it returns
│
│  ── THE ENGINE (the intelligence) ──
├─ copilot-instructions.md   makes VS Code Copilot / Claude behave as the engine (read it, then drive)
│
│  ── THE STORE (one JSON per incident — the agent reads & writes these) ──
├─ incidents/
│  ├─ INC-2256.json          ⭐ a FULLY-WORKED walkthrough (study this — every phase, the gate, the close)
│  ├─ INC-4821.json          worked example (storage root-cause)
│  ├─ INC-DEMO.json          worked example (deploy/config root-cause)
│  ├─ INC-2207.json          fresh "new" incident — drive this one LIVE
│  └─ INC-2208.json          fresh "new" incident — drive this one LIVE
│
│  ── THE VIEWER (visualisation only) ──
├─ viewer.html               3-pane console: Phases+Plan · Conversation · Graph / Phase-record
└─ shots/
   └─ status-board.png       a real screenshot used as stand-in evidence in the rehearsal
```

## 2. The two surfaces

- **UI #1 — `viewer.html` (what the room watches).** Three panes:
  - **Left — Phases & Plan:** the phases (from `playbook.json`); the selected phase's plan + output.
  - **Centre — Conversation:** a live mirror of the agent's chat — its reasoning, tool-calls (with the real
    screenshot inline), proposals, and your decisions. *You don't type here — it just reflects.*
  - **Right — Graph / Phase record (tabs):** the investigation graph (schema-validated; click a node for its
    facts · relations · full JSON) and the phase's DB record (fields · colour-coded steps · full JSON).
  - It **polls the incident JSON every 2 s**, so it animates as the agent writes.
- **UI #2 — the agent (VS Code Copilot / Claude).** You chat with it; it reads the definition files, drives the
  real tools in the browser, writes the JSON, and **pauses for your approval**. `copilot-instructions.md` is its
  rulebook.

## 3. The principles it demonstrates

1. **Governed autonomy** — the agent does the legwork; **nothing touches production without your explicit approve**.
2. **A human checkpoint at every boundary** — at the end of each phase the agent **presents its findings and
   waits** for you to approve before advancing (not just at the write). Remediation adds the **write gate**
   (approve · refine · deny). Closing is always a human act.
3. **Schema-driven, nothing hard-coded** — node/edge/health **types** come from `graph-schema.json`, the **plan**
   from `playbook.json`, the **tools + fields** from `capabilities.json`. A type the schema lacks is *never*
   invented — the agent stops and asks you to extend the schema.
4. **Everything captured** — every fact carries **tool · field · timestamp**; every step is timestamped; every
   node and phase has its full record; the conversation logs nothing off-screen.

## 4. Run it (2 minutes)

The viewer fetches JSON, so it needs a local web server (not `file://`):

```bash
cd demo
python3 -m http.server 8000
```

Then open in a browser:

| To… | Open |
|---|---|
| see a **finished story** (cold open) | `http://localhost:8000/viewer.html?incident=INC-4821` |
| study the **full worked walkthrough** | `http://localhost:8000/viewer.html?incident=INC-2256` |
| another finished example | `http://localhost:8000/viewer.html?incident=INC-DEMO` |
| drive one **live** | `http://localhost:8000/viewer.html?incident=INC-2207` |

URL params: `?incident=<ID>` · `&phase=<assess|root-cause|remediation|verify-close>` (deep-link a phase) ·
`&node=<id>` (deep-link a node) · `&tab=<graph|record>`.

## 5. Drive it live (the demo)

1. Open the viewer on a fresh incident: `…/viewer.html?incident=INC-2207`.
2. In the **agent chat** (VS Code Copilot or Claude), paste `copilot-instructions.md`, then say:
   > *You are the Investigation Workbench engine. Investigate **INC-2207**: orders-api 5xx is climbing.*
3. The agent works the phases. For each one it:
   - **presents the plan** (the tools it'll use) → you **approve** or modify;
   - **asks to launch the browser** → on OK it opens the real tool (you log in if prompted), screenshots, and
     **writes findings into `incidents/INC-2207.json`** → the viewer animates;
   - at the **end of the phase** it **presents findings and waits** for your approve to advance.
4. At **Remediation** it proposes the fix and stops at the **write gate** — you **approve · refine · deny**.
   The write happens *only* on approve.
5. **Verify** confirms recovery (graph goes green); **you close** it.

> Every line the agent "says" it also **writes as a step**, so the Conversation pane mirrors it. If something
> isn't in the viewer, it didn't get written.

## 6. Office checklist

- [ ] **Python 3** (for `python3 -m http.server`) and a **Chromium browser**.
- [ ] **For the real browse:** the **Claude-in-Chrome extension** (or a Playwright MCP) connected, so the agent
      can open tools live. *Without it, narrate the browse and reuse `shots/status-board.png` as the stand-in
      (that's exactly the rehearsal mode).*
- [ ] **Real tool URLs:** edit `capabilities.json` — replace every `your-*.example.com` / `your-org` / `your-instance`
      placeholder host with at least one real dashboard (a metrics/status page with a visible error panel). This is
      the single biggest live-fragility lever; pre-authenticate it before you present. *(The two Datadog URLs and the
      GitHub host are already real — they just need your auth.)*
- [ ] **Pick the incident:** drive `INC-2207` (or `INC-2208`); keep `INC-4821` / `INC-2256` as finished examples.

## 7. 5-minute script

1. **Cold open** — viewer on `INC-4821`: "a finished investigation — one graph, four phases, every step has
   evidence, the fix was human-approved." (10 s.)
2. **The inputs are files** — show `playbook.json` (the phases) + `capabilities.json` (the tools). "Process and
   tools are declarative."
3. **Go live** on `INC-2207` — kick off the agent. Watch the graph sprout in **Assess**; send it back to **dig
   more** if you like.
4. **Root cause** — it walks the graph, rules out the deps, draws the red **cause edge**.
5. **The gate** — it proposes the fix; **deny once** to prove the write is blocked, then **approve**.
6. **Verify & close** — graph goes green; **you close it**. Done.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| Viewer blank / "no server" | you opened `file://` — serve it: `python3 -m http.server 8000` |
| Graph not updating | it polls every 2 s; confirm the agent is writing to the **right** `incidents/<ID>.json` |
| A node shows **⚠ not in schema** | the agent used a type not in `graph-schema.json` — add it there (don't hand-edit the node) |
| Browser won't open live | the Chrome/Playwright MCP isn't connected — use rehearsal mode (narrate + `shots/` screenshot) |
| Right pane blank | a bad `?tab=` value — it falls back to the Graph tab automatically |
| No ✓/⚠ schema badge on any node | `graph-schema.json` didn't load — confirm it's being served |

---

*The agent is the brain; the HTML is the window; the files are the truth. To change the process, edit the
definition files — never code.*
