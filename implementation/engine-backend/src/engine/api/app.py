"""The HTTP API (FastAPI). Part F / E3.

Thin surface over the session manager + the engine + the read-model: create/join a session, advance
the run (to the gate or to close), answer the gate, read the incident document, post/replay chat, and
record feedback. The engine + sources are injected (an `engine_factory`), so the API is testable with
the mock engine and zero real credentials.
"""
from __future__ import annotations

import json
from typing import Callable, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from engine.domain import Feedback, SubjectRef
from engine.runtime import Engine
from engine.session import NotAuthorized, NotWriter, SessionManager

from .feedback_store import FeedbackStore
from .readmodel import ReadModelStore, project_incident


class SubjectBody(BaseModel):
    domain: str
    id: str
    kind: str
    actor: str


class MessageBody(BaseModel):
    actor: str
    text: str


class ActorBody(BaseModel):
    actor: str


class GateBody(BaseModel):
    actor: str
    gate_id: str
    decision: str            # approve | refine | deny


class FeedbackBody(BaseModel):
    domain: str
    id: str
    kind: str                # outcome | failure | correction
    actor: str
    verdict: Optional[str] = None
    note: Optional[str] = None
    run_id: Optional[str] = None


def create_app(session_manager: SessionManager,
               engine_factory: Callable[[SubjectRef], Engine],
               read_model: Optional[ReadModelStore] = None,
               feedback_store: Optional[FeedbackStore] = None) -> FastAPI:
    app = FastAPI(title="Incident Triage — Investigation Engine API")
    mgr = session_manager
    rm = read_model or ReadModelStore()
    fb = feedback_store or FeedbackStore()
    engines: dict[str, Engine] = {}

    def _refresh(sess, eng) -> dict:
        st = eng.state(sess.id)
        paused = bool(st["next"])
        rm.upsert(project_incident(sess.subject.model_dump(), st["values"], eng.graph, paused=paused))
        return {"session_id": sess.id,
                "status": "waiting_approval" if paused else "done",
                "next": st["next"]}

    def _require(sid: str):
        sess = mgr.get(sid)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        return sess

    @app.post("/sessions")
    def create_session(body: SubjectBody):
        subject = SubjectRef(domain=body.domain, id=body.id, kind=body.kind)
        sess = mgr.create_or_join(subject, body.actor)
        return {"session_id": sess.id, "status": "created", "members": sorted(sess.members),
                "pen_holder": sess.pen_holder, "role": mgr.role_of(sess, body.actor)}

    @app.post("/sessions/{sid}/advance")
    def advance(sid: str):
        sess = _require(sid)
        eng = engines.get(sid)
        if eng is None:
            eng = engine_factory(sess.subject)
            engines[sid] = eng
            eng.start(sess.subject.model_dump(), thread_id=sid)
        elif eng.is_paused(sid):
            eng.resume(sid)
        return _refresh(sess, eng)

    @app.post("/sessions/{sid}/gate")
    def gate(sid: str, body: GateBody):
        sess = _require(sid)
        eng = engines.get(sid)
        if eng is None:
            raise HTTPException(status_code=409, detail="run not started")
        try:
            mgr.require_writer(sess, body.actor)         # only the pen-holder (writer) may approve
        except NotWriter as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        if body.decision == "refine":
            # the operator wants to adjust — keep the gate OPEN (do not lock it answered-once), so a
            # refined approval can still follow; the run stays paused.
            return {"session_id": sess.id, "status": "waiting_approval",
                    "gate": {"gate_id": body.gate_id, "decision": "refine", "actor": body.actor}}

        resolved = mgr.answer_gate(sess, body.gate_id, body.decision, body.actor)  # approve|deny — terminal
        if resolved["decision"] == "approve" and eng.is_paused(sid):
            eng.resume(sid)
        out = _refresh(sess, eng)
        # a denied write does not proceed — the run halts here; a human closes (not stuck pending)
        out["status"] = "denied" if resolved["decision"] == "deny" else out["status"]
        out["gate"] = resolved
        return out

    @app.get("/sessions/{sid}/incident")
    def incident(sid: str):
        sess = _require(sid)
        doc = rm.get(sess.subject.domain, sess.subject.id)
        if doc is None:
            raise HTTPException(status_code=404, detail="no read-model yet — advance the run first")
        return doc

    @app.post("/sessions/{sid}/messages")
    def post_message(sid: str, body: MessageBody):
        sess = _require(sid)
        try:
            mgr.require_writer(sess, body.actor)         # only the pen-holder (writer) may send
            seq = mgr.post_event(sess, body.actor, {"kind": "msg", "text": body.text})
        except (NotAuthorized, NotWriter) as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {"seq": seq}

    @app.post("/sessions/{sid}/take-pen")
    def take_pen(sid: str, body: ActorBody):
        sess = _require(sid)
        try:
            ok = mgr.take_pen(sess, body.actor)
        except NotAuthorized as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        return {"ok": ok, "pen_holder": sess.pen_holder, "role": mgr.role_of(sess, body.actor)}

    @app.post("/sessions/{sid}/release-pen")
    def release_pen(sid: str, body: ActorBody):
        sess = _require(sid)
        ok = mgr.release_pen(sess, body.actor)
        return {"ok": ok, "pen_holder": sess.pen_holder}

    @app.get("/sessions/{sid}/events")
    def events(sid: str, after_seq: int = 0):
        _require(sid)
        return {"events": mgr.events.since(sid, after_seq)}

    @app.get("/sessions/{sid}/stream")
    def stream(sid: str, request: Request, after_seq: int = 0):
        # SSE — replay events since the client's last seq (Last-Event-ID) as text/event-stream, then
        # close; the browser's EventSource reconnects (~retry) for the next batch. The widget layer
        # renders each event by its `kind`; the transport is independent of the payload.
        _require(sid)
        leid = request.headers.get("last-event-id")
        if leid == "-":
            start = 0
        elif leid and leid.isdigit():
            start = int(leid)
        else:
            start = after_seq

        def gen():
            yield "retry: 1000\n\n"
            for ev in mgr.events.since(sid, start):
                yield f"id: {ev['seq']}\ndata: {json.dumps(ev)}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/sessions/{sid}/poll")
    def poll(sid: str, after_seq: int = 0):
        # the polling primitive — clients call this every few seconds: new events since their seq +
        # the read-model snapshot + the run status. No push, no Redis/WebSocket; every server
        # answers it statelessly from the shared store.
        sess = _require(sid)
        eng = engines.get(sid)
        status = "new"
        if eng is not None:
            status = "waiting_approval" if eng.is_paused(sid) else "done"
        return {
            "events": mgr.events.since(sid, after_seq),
            "seq": mgr.events.snapshot_seq(sid),
            "status": status,
            "pen_holder": sess.pen_holder,
            "incident": rm.get(sess.subject.domain, sess.subject.id),
        }

    @app.post("/feedback")
    def feedback(body: FeedbackBody):
        fbk = Feedback(subject=SubjectRef(domain=body.domain, id=body.id, kind="incident"),
                       run_id=body.run_id, actor=body.actor, kind=body.kind,
                       verdict=body.verdict, note=body.note)
        fb.add(fbk)
        return {"stored": True, "kind": fbk.kind.value}

    app.state.read_model = rm
    app.state.feedback = fb
    app.state.engines = engines
    return app
