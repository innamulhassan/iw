"""Run the live Investigation Workbench backend — single-use, single-user demo.

Wires the LLM-backed planner (xAI Grok) into the EXISTING LangGraph engine + FastAPI surface, with
the demo capability layer (realistic mock tool data) and CORS open for the Vite UI. On top, it adds a
BROWSER CAPABILITY REGISTRY: the operator registers UI-only tools (ServiceNow, Google, an internal
portal, …) by {name, url, description, intent}; each opens a real Chrome tab to log into; and the
agent then reads those live pages as the engine intents they back. The engine, the run loop, and the
audit trail are unchanged — only how a capability fetches data changes.

  set -a; source .env; set +a   # XAI_API_KEY (+ optional XAI_MODEL / XAI_BASE_URL)
  python -m engine.api.serve    # → http://127.0.0.1:8088
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from engine.api import create_app
from engine.api.browser_tool import BrowserManager, CapabilityStore, HybridAdapter
from engine.api.demo import DemoAdapter, build_demo_fold, build_demo_layer
from engine.domain import ProviderKind
from engine.runtime import Engine, load_playbook
from engine.runtime.llm_planner import LLMPlanner
from engine.session import SessionManager

# a headed browser by default (so the human can log in); headless for automated checks
_HEADED = os.environ.get("BROWSER_HEADED", "1") not in ("0", "false", "False")
# persistent profile -> logins survive across runs + far less bot-flagging (system Chrome channel)
_PROFILE = os.environ.get("BROWSER_PROFILE",
                          str(Path(__file__).resolve().parents[3] / ".browser-profile"))
# how long the agent waits (per capability) for the human to finish logging in before degrading to demo
_LOGIN_WAIT = float(os.environ.get("BROWSER_LOGIN_WAIT", "20"))
# the capability registry is a JSON FILE (file-as-DB) — defaults to the demo's curated capabilities.json
_CAP_FILE = os.environ.get(
    "CAP_FILE", str(Path(__file__).resolve().parents[5] / "demo" / "capabilities.json"))

# module-level singletons (single-user demo): the shared browser + the file-backed capability registry
BROWSER = BrowserManager(headed=_HEADED, profile_dir=_PROFILE)
STORE = CapabilityStore(_CAP_FILE)

_PLAYBOOK_PATH = os.environ.get(
    "PLAYBOOK_PATH",
    str(Path(__file__).resolve().parents[3] / "playbooks" / "incident-triage.md"),
)

# example capabilities the demo registers in one click — public Google pages so anyone can run it
# without office tools; they back real ASSESS-phase intents.
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


class ReadyBody(BaseModel):
    ready: bool = True


def build_app(browser: Optional[BrowserManager] = None, store: Optional[CapabilityStore] = None):
    browser = browser or BROWSER
    store = store or STORE
    playbook = load_playbook(_PLAYBOOK_PATH)
    # both providers read a live tab when the called intent is backed by a registered+ready capability,
    # and fall back to demo data otherwise — sharing one browser + one registry.
    cmdb = HybridAdapter(ProviderKind.mcp_remote, browser, DemoAdapter(ProviderKind.mcp_remote),
                         store, wait_timeout=_LOGIN_WAIT)
    obs = HybridAdapter(ProviderKind.mcp_remote, browser, DemoAdapter(ProviderKind.mcp_remote),
                        store, wait_timeout=_LOGIN_WAIT)
    layer = build_demo_layer(cmdb_adapter=cmdb, obs_adapter=obs)
    fold = build_demo_fold()
    planner = LLMPlanner(playbook, live_intents=store.ready_intents)   # exercise live tools in-phase
    factory = lambda subject: Engine(playbook, planner, layer, fold)   # noqa: E731
    app = create_app(SessionManager(), factory)

    from fastapi import HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"], expose_headers=["*"])

    def _open(cap):
        try:
            browser.open(cap.key, cap.url)
            cap.opened = True
        except Exception as exc:        # registration must not fail if the browser can't launch here
            cap.opened = False
            return {"open_error": str(exc)[:160]}
        return {}

    @app.post("/capabilities")
    def register_capability(body: CapBody):
        cap = store.register(body.name, body.url, body.description, body.intents, body.effect)
        extra = _open(cap)               # launch the tab so the human can log in
        return {"ok": True, "capability": cap.wire(), **extra}

    @app.post("/capabilities/demo")
    def register_demo():
        out = []
        for c in DEMO_CAPS:
            cap = store.register(c["name"], c["url"], c["description"], c["intents"])
            _open(cap)
            out.append(cap.wire())
        return {"ok": True, "capabilities": out}

    @app.get("/capabilities")
    def list_capabilities():
        return {"capabilities": [c.wire() for c in store.list()],
                "browser_mode": browser.mode, "file": _CAP_FILE}

    @app.post("/capabilities/reload")
    def reload_capabilities():
        store.reload()                   # re-read the file (operator edited it directly)
        return {"ok": True, "capabilities": [c.wire() for c in store.list()]}

    @app.post("/capabilities/{key}/ready")
    def set_ready(key: str, body: ReadyBody):
        cap = store.set_ready(key, body.ready)
        if cap is None:
            raise HTTPException(status_code=404, detail=f"no capability {key!r}")
        return {"ok": True, "capability": cap.wire()}

    @app.post("/capabilities/{key}/open")
    def open_capability(key: str):
        cap = store.get(key)
        if cap is None:
            raise HTTPException(status_code=404, detail=f"no capability {key!r}")
        extra = _open(cap)
        return {"ok": True, "capability": cap.wire(), **extra}

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

    return app


app = build_app()   # importable as `engine.api.serve:app` for `uvicorn`


def main() -> None:
    import uvicorn
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    if not os.environ.get("XAI_API_KEY"):
        print("⚠  XAI_API_KEY not set — the server runs but model calls will fail. Put it in .env.")
    print(f"Investigation Workbench backend → http://{host}:{port}  "
          f"(model={os.environ.get('XAI_MODEL', 'grok-3')}, "
          f"base={os.environ.get('XAI_BASE_URL', 'https://api.x.ai/v1')}, headed={_HEADED})")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
