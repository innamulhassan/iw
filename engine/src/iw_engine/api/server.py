"""FastAPI session backend — the HTTP/SSE surface the Vite workbench drives (DEPTH-BUILD-PLAN
§C.2). A thin transport over `InvestigationSession`: every endpoint reads or nudges a session
in the `SessionManager` registry; the deterministic fold and the write-gate live in
`runtime/session.py`, not here.

    from iw_engine.api.server import create_server
    from iw_engine.runtime.session import SessionManager

    app = create_server(planner_factory=my_planner_factory)   # incident playbook auto-loaded
    # uvicorn iw_engine.api.server:app   (after `app = create_server(...)`)

Endpoints:
    GET  /catalog                      the runnable incidents for the start selector (id/title/layer)
    POST /sessions                     start an investigation (incident playbook + planner) → id + snapshot
    POST /sessions/{id}/advance        step to the next pause / open gate → new events
    POST /sessions/{id}/gate           answer an open write-gate: approve | refine | deny
    POST /sessions/{id}/messages       operator message (steering / answer)
    GET  /sessions/{id}/events?after=N poll events since seq N
    GET  /sessions/{id}/stream         Server-Sent-Events stream (resumable via ?after=N)
    GET  /sessions                     list every session (incl. CLOSED)
    GET  /sessions/{id}                full export_bundle-shaped snapshot

With no `manager`/`planner_factory`, the app is built from the six-scenario registry
(`runtime/scenarios.py`), so `uvicorn iw_engine.api.server:create_server --factory` boots a
fully runnable workbench backend with every use case listed on `/catalog`.

FastAPI is imported lazily inside `create_server`, so importing this module never requires the
`server` extra to be installed (the hermetic test suite never touches it).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
from collections.abc import Callable

from pydantic import BaseModel

import iw_engine

from ..domain.subject import SubjectRef
from ..runtime.loader import load_playbook
from ..runtime.logging_setup import setup_logging
from ..runtime.planner import Planner
from ..runtime.session import GateDecision, ReviewDecision, SessionManager, SessionState
from ..runtime.store import InvestigationStore

log = logging.getLogger(__name__)


# Request bodies live at MODULE scope on purpose: with `from __future__ import annotations`
# every annotation is a string, and FastAPI resolves an endpoint's body model via
# `get_type_hints` against the module globals — a model defined *inside* create_server can't be
# resolved there and would be misread as a query parameter. (pydantic is a core dependency, so
# this needs no server extra; only FastAPI itself stays lazily imported.)
class SubjectBody(BaseModel):
    domain: str
    id: str
    kind: str = "incident"


class CreateBody(BaseModel):
    subject: SubjectBody


class GateBody(BaseModel):
    decision: str
    params: dict | None = None
    reason: str = ""


class ReviewBody(BaseModel):
    decision: str          # approve | refine | deny
    text: str = ""         # the operator steer (refine) or note


class MessageBody(BaseModel):
    text: str


def _default_playbook() -> pathlib.Path:
    return pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"


def create_server(manager: SessionManager | None = None, *,
                  planner_factory: Callable[[SubjectRef], Planner] | None = None,
                  layer_factory: Callable[[SubjectRef], object] | None = None,
                  playbook_path: pathlib.Path | None = None,
                  cors_origins: list[str] | None = None,
                  store: InvestigationStore | None = None):
    """Build the FastAPI app. Pass a preconfigured `manager`, or a `planner_factory` and the
    incident playbook is loaded for you. CORS is open by default for the Vite dev server.

    Investigations are file-backed by default (`store`, defaulting to `engine/data/investigations/`)
    so a live run survives a backend restart: `GET /sessions/{id}` reopens it read-only from disk,
    and `GET /sessions` merges the on-disk investigations with the in-memory ones."""
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import StreamingResponse

    # REAL logging FIRST — stdout + a rolling file — so building the manager, and every drive
    # after it, is traceable end-to-end (and a live crash lands in the file with a full stack).
    setup_logging()
    live_env = os.environ.get("IW_LIVE", "").lower() in ("1", "true", "yes")
    log.info("create_server: building session backend (IW_LIVE=%s)", live_env)

    catalog_fn: Callable[[], list[dict]] = list  # overwritten below to the scenario catalog
    # default the durability store so the workbench backend persists (and reopens) investigations.
    store = store if store is not None else InvestigationStore()
    if manager is None:
        if planner_factory is None:
            # default backend: the six-scenario registry — every use case runnable (UI-SPEC §1).
            # IW_LIVE=1 selects the LLM-driven backend (obs 10: "you should not be in the
            # execution") when a key is present; otherwise the deterministic scripted mock (the
            # CI net) — so tests/offline always work and the product runs live on demand.
            from ..runtime.scenarios import (
                build_manager,
                catalog,
                live_build_manager,
                make_live_client,
            )
            playbook = load_playbook(playbook_path) if playbook_path else None
            want_live = os.environ.get("IW_LIVE", "").lower() in ("1", "true", "yes")
            if want_live and make_live_client() is not None:
                log.info("backend: LIVE (LLM-driven planner, human-gated phase reviews)")
                manager = live_build_manager(playbook=playbook, store=store)
            else:
                if want_live:
                    print("IW_LIVE set but no LLM key found — falling back to the scripted mock.")
                    log.warning("IW_LIVE set but no LLM key found — falling back to the scripted mock.")
                log.info("backend: scripted mock (deterministic scenario registry)")
                manager = build_manager(playbook=playbook, store=store)
            catalog_fn = catalog
        else:
            playbook = load_playbook(playbook_path or _default_playbook())
            manager = SessionManager(playbook, planner_factory, layer_factory=layer_factory,
                                     store=store)

    app = FastAPI(title="Investigation Workbench — session backend")
    app.add_middleware(CORSMiddleware, allow_origins=cors_origins or ["*"],
                       allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    def _require(session_id: str):
        session = manager.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"no session {session_id}")
        return session

    @app.get("/catalog")
    def get_catalog() -> dict:
        return {"incidents": catalog_fn()}

    @app.post("/sessions")
    def create_session(body: CreateBody) -> dict:
        subject = SubjectRef(domain=body.subject.domain, id=body.subject.id, kind=body.subject.kind)
        session = manager.create(subject)                 # starts + runs to the first pause
        return {"session_id": session.id, "state": session.state.value,
                "snapshot": session.snapshot()}

    @app.post("/sessions/{session_id}/advance")
    def advance(session_id: str) -> dict:
        session = _require(session_id)
        return {"events": session.advance(), "state": session.state.value}

    @app.post("/sessions/{session_id}/gate")
    def gate(session_id: str, body: GateBody) -> dict:
        session = _require(session_id)
        try:
            events = session.answer_gate(GateDecision(body.decision),
                                         params=body.params, reason=body.reason)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"events": events, "state": session.state.value}

    @app.post("/sessions/{session_id}/review")
    def review(session_id: str, body: ReviewBody) -> dict:
        session = _require(session_id)
        try:
            events = session.answer_review(ReviewDecision(body.decision), text=body.text)
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"events": events, "state": session.state.value}

    @app.post("/sessions/{session_id}/messages")
    def message(session_id: str, body: MessageBody) -> dict:
        session = _require(session_id)
        return {"message": session.add_message(body.text)}

    @app.get("/sessions/{session_id}/events")
    def events(session_id: str, after: int = 0) -> dict:
        session = _require(session_id)
        return {"events": session.events(after=after), "state": session.state.value}

    @app.get("/sessions/{session_id}/stream")
    async def stream(session_id: str, after: int = 0):
        session = _require(session_id)

        async def gen():
            cursor, ticks = after, 0
            while True:
                new = session.events(after=cursor)
                for ev in new:
                    cursor = ev["seq"]
                    yield f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
                if session.state == SessionState.CLOSED:
                    yield f"event: closed\ndata: {json.dumps({'state': 'closed'})}\n\n"
                    return
                if not new:
                    yield ": keep-alive\n\n"        # SSE comment heartbeat (proxy-friendly)
                await asyncio.sleep(0.5)
                ticks += 1
                if ticks > 1200:                     # ~10 min ceiling on an idle connection
                    return

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/sessions")
    def list_sessions() -> dict:
        return {"sessions": manager.list()}

    @app.get("/sessions/{session_id}")
    def snapshot(session_id: str) -> dict:
        # in-memory first; on a miss (e.g. after a backend restart) reopen read-only from disk.
        reopened = manager.reopen(session_id)
        if reopened is None:
            raise HTTPException(status_code=404, detail=f"no session {session_id}")
        return reopened

    app.state.manager = manager
    app.state.catalog = catalog_fn
    return app
