"""Run the live Investigation Workbench demo — the REAL LangGraph + xAI engine driving the EXISTING
mock console (`ux-console.html` / `viewer.html`). Single-use, single-user.

How it fits together (everything is files):
  * capabilities.json  — the registry: each entry is just a tool URL + description (+ the intents it
    backs). Edit it to your real tools; login is on-demand (the agent opens the URL and, if it shows a
    login wall, waits for you to log in in the browser window, then reads).
  * the engine runs an incident and PROJECTS its state into incidents/<INC>.json (the demo schema) as it
    goes; the console polls that file every 2s and animates — so the mock UI shows a real run.
  * the whole demo (console + json + this backend) is served from ONE origin, so there is one URL.

  set -a; source .env; set +a            # XAI_API_KEY (+ optional XAI_MODEL / XAI_BASE_URL)
  python -m engine.api.serve             # → http://127.0.0.1:8088/ux-console.html?incident=INC-2207
"""
from __future__ import annotations

import os
import threading
import time
import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from engine.api import create_app
from engine.api.browser_tool import BrowserManager, CapabilityStore, HybridAdapter
from engine.api.demo import DemoAdapter, build_demo_fold, build_demo_layer
from engine.api.demo_store import IncidentWriter, project_demo
from engine.domain import ProviderKind, SubjectRef
from engine.runtime import Engine, load_playbook
from engine.runtime.llm_planner import LLMPlanner
from engine.session import SessionManager

_HEADED = os.environ.get("BROWSER_HEADED", "1") not in ("0", "false", "False")
# persistent profile -> logins survive across runs + far less bot-flagging (system Chrome channel)
_PROFILE = os.environ.get("BROWSER_PROFILE",
                          str(Path(__file__).resolve().parents[3] / ".browser-profile"))
# how long the agent waits for the human to clear a login/auth wall before reading anyway
_LOGIN_WAIT = float(os.environ.get("BROWSER_LOGIN_WAIT", "45"))

_DEMO_DIR = Path(__file__).resolve().parents[4]                 # the demo/ folder (self-contained)
_CAP_FILE = os.environ.get("CAP_FILE", str(_DEMO_DIR / "capabilities.json"))
_INCIDENTS_DIR = os.environ.get("INCIDENTS_DIR", str(_DEMO_DIR / "incidents"))
_PLAYBOOK_PATH = os.environ.get(
    "PLAYBOOK_PATH", str(Path(__file__).resolve().parents[3] / "playbooks" / "incident-triage.md"))

# module-level singletons (single-user demo)
BROWSER = BrowserManager(headed=_HEADED, profile_dir=_PROFILE)
STORE = CapabilityStore(_CAP_FILE)
WRITER = IncidentWriter(_INCIDENTS_DIR)
RUNS: dict[str, dict] = {}                                      # incident id -> {"eng", "phase"}

# The xAI Grok planner is constructed once at module scope so the operator-chat route can reach it
# (LLMPlanner.chat) without the engine run loop. The engines built in build_app() share this instance.
_PLAYBOOK = load_playbook(_PLAYBOOK_PATH)
PLANNER = LLMPlanner(_PLAYBOOK, live_intents=STORE.live_intents)

DEMO_CAPS = [
    {"name": "Google Search", "intents": ["incident-source"],
     "description": "Web-search the incident symptom for known causes / advisories.",
     "url": "https://www.google.com/search?q=payments-api+p99+latency+spike+storage+RAID+troubleshooting&hl=en&gl=us"},
    {"name": "Google Images", "intents": ["similar-incidents"],
     "description": "Reference images for the suspected failure mode (storage / RAID topology).",
     "url": "https://www.google.com/search?tbm=isch&q=datacenter+storage+RAID+rebuild+latency&hl=en&gl=us"},
]


class CapBody(BaseModel):
    name: str
    url: str
    description: str = ""
    intents: list[str]
    effect: str = "read-only"


class GateDecision(BaseModel):
    decision: str = "approve"          # approve | deny


class ChatBody(BaseModel):
    text: str


def _gate_doc(values: dict) -> dict:
    """The pending write-gate the console renders (the proposal from the remediation phase, if any)."""
    proposal = "Apply the proposed remediation — gated write, nothing changes until you approve."
    for r in values.get("phase_records", []):
        for s in r.get("steps", []):
            if s.get("kind") == "suggestion" and s.get("result"):
                proposal = str(s["result"])[:240]
    return {"phase": "remediation", "proposal": proposal}


def build_app(browser: Optional[BrowserManager] = None, store: Optional[CapabilityStore] = None):
    browser = browser or BROWSER
    store = store or STORE
    playbook = _PLAYBOOK
    planner = PLANNER
    # both providers read a live tab when the called intent is backed by a configured tool URL, and fall
    # back to demo data otherwise — sharing one browser + one registry.
    # the login-wait only makes sense when a human can log in (headed); headless skips it (fast)
    _wait = _LOGIN_WAIT if _HEADED else 0.0
    cmdb = HybridAdapter(ProviderKind.mcp_remote, browser, DemoAdapter(ProviderKind.mcp_remote),
                         store, wait_timeout=_wait)
    obs = HybridAdapter(ProviderKind.mcp_remote, browser, DemoAdapter(ProviderKind.mcp_remote),
                        store, wait_timeout=_wait)
    layer = build_demo_layer(cmdb_adapter=cmdb, obs_adapter=obs)
    fold = build_demo_fold()
    factory = lambda subject: Engine(playbook, planner, layer, fold)    # noqa: E731
    app = create_app(SessionManager(), factory)

    from fastapi import HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.staticfiles import StaticFiles
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"], expose_headers=["*"])

    # ── the live run: project the engine into incidents/<INC>.json as it goes ──────────────
    def _watch(inc: str, eng, subject: dict):
        while RUNS.get(inc, {}).get("phase") == "running":
            try:
                st = eng.state(inc)
                WRITER.write(inc, project_demo(subject, st["values"], eng.graph, status="running"))
            except Exception:
                pass
            time.sleep(1.5)

    def _run(inc: str, eng, subject: dict, resume: bool):
        threading.Thread(target=_watch, args=(inc, eng, subject), daemon=True).start()
        try:
            if resume:
                if eng.is_paused(inc):
                    eng.resume(inc, decision={"decision": "approve", "actor": "operator"})
            else:
                eng.start(subject, thread_id=inc)
        except Exception as exc:
            RUNS[inc]["phase"] = "error"
            WRITER.write(inc, {"incident": inc, "subject": subject, "status": "error",
                               "title": f"engine error: {str(exc)[:160]}", "current_phase": "assess",
                               "pending_gate": None, "graph": {"nodes": [], "edges": []}, "phases": []})
            return
        st = eng.state(inc)
        paused = bool(st["next"])
        RUNS[inc]["phase"] = "gate" if paused else "done"
        WRITER.write(inc, project_demo(subject, st["values"], eng.graph,
                     status="waiting_approval" if paused else "done",
                     pending_gate=_gate_doc(st["values"]) if paused else None))

    @app.post("/drive/{inc}")
    def drive(inc: str):
        if RUNS.get(inc, {}).get("phase") == "running":
            return {"ok": True, "already_running": True, "incident": inc}
        subject = {"domain": "app-incident", "id": inc, "kind": "incident"}
        eng = factory(SubjectRef(**subject))
        RUNS[inc] = {"eng": eng, "phase": "running"}
        WRITER.write(inc, {"incident": inc, "subject": subject, "status": "running",
                           "title": inc, "current_phase": "assess", "pending_gate": None,
                           "graph": {"nodes": [{"id": inc, "label": inc, "kind": "incident",
                                                "health": "subject", "phase": "assess", "facts": []}],
                                     "edges": []}, "phases": []})
        threading.Thread(target=_run, args=(inc, eng, subject, False), daemon=True).start()
        return {"ok": True, "incident": inc, "started": True}

    @app.post("/drive/{inc}/gate")
    def drive_gate(inc: str, body: GateDecision):
        run = RUNS.get(inc)
        if not run:
            raise HTTPException(status_code=404, detail="no run for that incident — drive it first")
        eng = run["eng"]
        subject = {"domain": "app-incident", "id": inc, "kind": "incident"}
        if body.decision == "deny":
            st = eng.state(inc)
            RUNS[inc]["phase"] = "done"
            doc = project_demo(subject, st["values"], eng.graph, status="done")
            doc["closed_by"] = "operator (denied write)"
            WRITER.write(inc, doc)
            return {"ok": True, "status": "denied"}
        RUNS[inc]["phase"] = "running"
        threading.Thread(target=_run, args=(inc, eng, subject, True), daemon=True).start()
        return {"ok": True, "status": "approving"}

    # ── operator chat — the free-form conversational side channel (runs ALONGSIDE the ──────────
    #    investigation, not part of the regulator-grade Step audit trail). Your message → xAI Grok
    #    → reply, both appended to incidents/<INC>.json "chat[]" (the console polls + renders them).
    def _load_or_seed(inc: str) -> dict:
        path = Path(_INCIDENTS_DIR) / f"{inc}.json"
        if path.exists():
            return json.loads(path.read_text())
        return {"incident": inc, "subject": {"domain": "app-incident", "id": inc, "kind": "incident"},
                "status": "chat", "current_phase": "", "pending_gate": None,
                "graph": {"nodes": [], "edges": []}, "phases": [], "chat": []}

    def _context_blurb(doc: dict) -> str:
        """Compact current-incident context so Grok knows what it's talking about."""
        bits = [f"incident={doc.get('incident','')}", f"status={doc.get('status','')}",
                f"phase={doc.get('current_phase','') or '—'}"]
        title = doc.get("title") or ""
        if title:
            bits.append(f"symptom={title}")
        nodes = (doc.get("graph") or {}).get("nodes") or []
        if nodes:
            sysn = [f"{n.get('id')}({n.get('kind')}" + (f":{n.get('health')}" if n.get('health') else "") + ")"
                    for n in nodes if n.get("kind") != "incident"]
            bits.append("systems=" + ", ".join(sysn))
        return "Current incident context — " + "; ".join(bits) + "."

    @app.post("/drive/{inc}/chat")
    def drive_chat(inc: str, body: ChatBody):
        doc = _load_or_seed(inc)
        doc.setdefault("chat", [])
        now = time.strftime("%H:%M:%S")
        doc["chat"].append({"role": "operator", "text": body.text, "at": now})
        # build the Grok message list: the incident context first, then the full chat history
        messages = [{"role": "user", "content": _context_blurb(doc)}]
        for m in doc["chat"]:
            role = "assistant" if m.get("role") == "agent" else "user"
            messages.append({"role": role, "content": m.get("text", "")})
        try:
            reply = PLANNER.chat(messages)
        except Exception as exc:
            err = f"(engine error: {type(exc).__name__}: {str(exc)[:120]})"
            doc["chat"].append({"role": "agent", "text": err, "at": time.strftime("%H:%M:%S")})
            WRITER.write(inc, doc)
            return {"ok": False, "error": err.strip("()")}
        doc["chat"].append({"role": "agent", "text": reply, "at": time.strftime("%H:%M:%S")})
        WRITER.write(inc, doc)
        return {"ok": True, "reply": reply}


    @app.get("/runs")
    def runs():
        return {inc: r.get("phase") for inc, r in RUNS.items()}

    # ── capability registry (file-as-DB; the console reads capabilities.json statically) ────
    @app.post("/capabilities")
    def register_capability(body: CapBody):
        cap = store.register(body.name, body.url, body.description, body.intents, body.effect)
        return {"ok": True, "capability": cap.wire()}

    @app.post("/capabilities/demo")
    def register_demo():
        out = [store.register(c["name"], c["url"], c["description"], c["intents"]).wire()
               for c in DEMO_CAPS]
        return {"ok": True, "capabilities": out}

    @app.get("/capabilities")
    def list_capabilities():
        return {"capabilities": [c.wire() for c in store.list()],
                "browser_mode": browser.mode, "file": _CAP_FILE}

    @app.post("/capabilities/{key}/open")
    def open_capability(key: str):
        cap = store.get(key)
        if cap is None:
            raise HTTPException(status_code=404, detail=f"no capability {key!r}")
        try:
            info = browser.open(key, cap.url)
            store.mark_opened(key, True)
            cap.opened = True
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"browser open failed: {type(exc).__name__}: {exc}")
        return {"ok": True, "capability": store.get(key).wire(), "page": info}

    @app.post("/capabilities/{key}/ready")
    def mark_ready(key: str):
        cap = store.mark_ready(key, True)
        if cap is None:
            raise HTTPException(status_code=404, detail=f"no capability {key!r}")
        return {"ok": True, "capability": cap.wire()}

    @app.post("/capabilities/reload")
    def reload_capabilities():
        store.reload()
        return {"ok": True, "capabilities": [c.wire() for c in store.list()]}

    @app.delete("/capabilities/{key}")
    def remove_capability(key: str):
        cap = store.remove(key)
        if cap is None:
            raise HTTPException(status_code=404, detail=f"no capability {key!r}")
        try:
            browser.close(key)
        except Exception:
            pass
        return {"ok": True, "removed": key}

    # serve the demo folder (ux-console.html, the json, the schemas) from this same origin -> ONE URL.
    # Mounted LAST so the API routes above take precedence.
    app.mount("/", StaticFiles(directory=str(_DEMO_DIR), html=True), name="demo")
    return app


app = build_app()   # importable as `engine.api.serve:app` for `uvicorn`


def main() -> None:
    import uvicorn
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8088"))   # the frontend hard-codes 127.0.0.1:8088 (App.tsx)
    if not os.environ.get("XAI_API_KEY"):
        print("⚠  XAI_API_KEY not set — the server runs but model calls will fail. Put it in .env.")
    print(f"Investigation Workbench → http://{host}:{port}/ux-console.html?incident=INC-2207  "
          f"(model={os.environ.get('XAI_MODEL', 'grok-3')}, headed={_HEADED})")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
