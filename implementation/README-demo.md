# Live Investigation Workbench — single-use demo

The agent (**xAI Grok**) investigates an incident **live** through the existing **LangGraph engine**,
with a **human-in-the-loop write-gate**. Single session, single user.

## Run
```bash
# one-time: engine-backend/.env already holds XAI_API_KEY (pulled from AssetOne); set XAI_MODEL if needed
./run-demo.sh          # starts backend :8088 + UI :5180
# open http://localhost:5180  → "Start investigation" → approve the write-gate
```

## Browser capabilities — the agent drives your real UI-only tools (file-as-DB)
The headline of the demo: keep the tools you can only reach in a **browser** (ServiceNow, Datadog,
Splunk, an internal portal…) in a **JSON file** — the registry *is* a file — and the agent drives them
live. The registry defaults to the demo's **`capabilities.json`**; each entry maps one tool URL to the
engine `intents` it backs:

```json
{ "id": "servicenow", "label": "ServiceNow", "intents": ["incident-source", "topology", "similar-incidents"],
  "url": "https://your-instance.service-now.com/…", "effect": "read-only", "ready": false }
```

The office flow:
1. Edit `capabilities.json` (or use the console's register form) so every entry points at *your* real
   tool URL. One URL can back several intents.
2. Click **Start** / **Register & open** — each capability opens **its own browser tab**. **Log in**
   there (in the office you already are), then mark it **Ready** (or set `"ready": true` in the file).
   *The engine waits for Ready* before reading the live page (up to `BROWSER_LOGIN_WAIT`s, else demo data).
3. Click **Start investigation**. The **LIVE TOOLS** strip shows each capability's read count ticking up
   as the agent pulls it; everything persists back to the file, so your registry survives restarts.

`✨ Load Google demo` registers two public examples (Google Search → `incident-source`, Google Images →
`similar-incidents`) so anyone can run it with no office tools (verified: both read live).

Mechanics (`engine/api/browser_tool.py`):
- `BrowserManager` — one **persistent Chrome** context (system `channel="chrome"` +
  `--disable-blink-features=AutomationControlled`), **one tab per capability** so several tools stay
  logged-in at once. Persistent profile (`.browser-profile/`, gitignored) → logins survive across runs
  and bot-hostile sites (Google) return real results even headless. Falls back to bundled Chromium when
  Chrome/persistent context is unavailable (CI). All Playwright work runs on one worker thread (FastAPI
  is multi-threaded). A read that hits a bot/login wall is flagged (`wall`) so the human can solve it —
  we never auto-solve a CAPTCHA.
- `CapabilityStore` — the **file-backed** registry (load on start, save on every change, `reload` to
  pick up external edits); `HybridAdapter` — routes a registered+ready intent to the live tab (after the
  bounded login-wait) and falls back to demo data otherwise. The `topology` intent is kept on demo data
  (a live page is text, not graph nodes), so the incident graph always seeds while the tool is still
  read live via its other intents.
- The planner (`llm_planner.py`) exercises **every** ready browser capability valid for the phase, so
  your real tools are demonstrably used.

Endpoints: `POST /capabilities {name,url,description,intents,effect}` · `POST /capabilities/demo` ·
`POST /capabilities/reload` · `GET /capabilities` (poll for live `reads`/`ready`/`wall` + the `file`) ·
`POST /capabilities/{key}/ready` · `POST /capabilities/{key}/open` · `DELETE /capabilities/{key}`.
Env: `CAP_FILE` (registry file; default `demo/capabilities.json`), `BROWSER_HEADED=0` (headless, for
automated checks; default headed for login), `BROWSER_PROFILE`, `BROWSER_LOGIN_WAIT` (default 20s).
**This swaps only how a capability fetches data — the engine, run loop, and audit trail are identical.**

## What it does (and what's reused)
- **Reused as-is (not reinvented):** the LangGraph `Engine` (4 phases, gates via `interrupt_before`),
  the FastAPI surface (`create_app`, SSE + REST), the capability layer, and the React `workbench-ui`.
- **Added (the "intelligent backend"):** `engine/runtime/llm_planner.py` — the real `Planner`
  (plan / next_action / update_output) on xAI Grok; `engine/api/demo.py` — a capability layer with
  realistic per-intent tool data; `engine/api/serve.py` — the entrypoint wiring it + CORS; the live
  `App.tsx` (create session → advance → approve gate); `engine/api/browser_tool.py` — the optional
  Playwright browser capability (see below).
- The agent maps topology first (seeds the graph), gathers an evidence floor per phase before
  concluding, ranks the cause, and proposes a gated remediation.

## Single-use scope / known limits
- In-memory, one run at a time; each "Start" is a fresh session.
- `/advance` is synchronous (the agent's model calls run server-side, ~30–50s), so step events land
  in a batch when a phase boundary is crossed — not token-by-token.
- The TRIAGE chat renders phase/graph/decision markers (the existing widget set); streaming the
  agent's per-step reasoning as rich chat text is the natural next polish.
- LLM: `XAI_API_KEY` + `XAI_MODEL` (default `grok-3`) + `XAI_BASE_URL` (default `https://api.x.ai/v1`;
  point at a LiteLLM proxy + role name to swap providers).
