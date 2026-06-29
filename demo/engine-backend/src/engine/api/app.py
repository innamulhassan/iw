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
    _delta_seen: dict[str, tuple] = {}             # per-session marker to dedup stream deltas
    SERVER_ID = "api"                              # this replica's run-lock owner id (B8.2)

    def _engine_for(sess) -> Engine:
        # rehydrate the Engine on a cache miss (SHOU-21) — over a shared checkpointer a second
        # replica reattaches to the live thread instead of reporting "not started".
        eng = engines.get(sess.id)
        if eng is None:
            eng = engine_factory(sess.subject)
            engines[sess.id] = eng
        return eng

    def _refresh(sess, eng) -> dict:
        st = eng.state(sess.id)
        paused = bool(st["next"])
        rm.upsert(project_incident(sess.subject.model_dump(), st["values"], eng.graph, paused=paused))
        # mirror phase/graph progress onto the one ordered event stream (B8.3) so a reconnecting /
        # second operator catches up on agent progress + the open-gate prompt, not just chat. Dedup
        # so we only append when the phase, pause state, or graph size actually changed.
        records = st["values"].get("phase_records", [])
        cur = records[-1].get("phase") if records else None
        marker = (cur, paused, len(eng.graph))
        if _delta_seen.get(sess.id) != marker:
            _delta_seen[sess.id] = marker
            mgr.events.append(sess.id, {"kind": "phase", "phase": cur,
                                        "state": "waiting_approval" if paused else "done"})
            mgr.events.append(sess.id, {"kind": "graph", "node_count": len(eng.graph),
                                        "nodes": eng.graph.node_ids()})
            if paused:
                mgr.events.append(sess.id, {"kind": "gate_prompt", "next": st["next"]})
        return {"session_id": sess.id,
                "status": "waiting_approval" if paused else "done",
                "next": st["next"]}

    def _require(sid: str):
        sess = mgr.get(sid)
        if sess is None:
            raise HTTPException(status_code=404, detail="session not found")
        return sess

    def _require_member(sid: str, actor: str):
        # SHOU-20 / AC9 — the read surface re-checks membership too, so a revoked operator loses the
        # incident doc + the live stream, not just the write path.
        sess = _require(sid)
        if not mgr.is_member(sess, actor):
            raise HTTPException(status_code=403, detail=f"{actor} is not a member of {sid}")
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
        eng = _engine_for(sess)
        if eng.started(sid) and eng.is_paused(sid):
            # a paused run is paused AT A GATE — it can only be resumed through the writer-guarded
            # gate endpoint with an explicit approve. advance() must never resume past the gate.
            raise HTTPException(status_code=409,
                                detail="run is paused at a gate — answer via POST /sessions/{sid}/gate")
        # MUST-8 — serialize the run: acquire the run-owner lock so two concurrent advances can't
        # both drive the same thread (the "second run" the lock prevents, FR16/AC9).
        token = mgr.lock.acquire(sid, SERVER_ID)
        if token is None:
            raise HTTPException(status_code=409, detail="run is already advancing (locked by another worker)")
        try:
            if not eng.started(sid):
                eng.start(sess.subject.model_dump(), thread_id=sid)
            drained = mgr.drain_inputs(sess)         # NICE-9 — fold queued operator inputs into the run
            eng.add_messages(sid, drained)
            return _refresh(sess, eng)
        finally:
            mgr.lock.release(sid, token)

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

        # SHOU-19 — for a FRESH gate, bind the client's gate_id to the engine's ACTUAL pending pause,
        # so an arbitrary/stale gate_id can't approve whatever the run is paused at. An already-resolved
        # gate skips the check so answered-once stays idempotent (a late repeat returns the cached
        # decision, not a 409).
        if body.gate_id not in sess.gate_decisions:
            pending = eng.state(sid)["next"]
            if not pending:
                raise HTTPException(status_code=409, detail="run is not paused at a gate")
            if body.gate_id != pending[0]:
                raise HTTPException(status_code=409,
                                    detail=f"gate_id mismatch — the run is paused at {pending[0]!r}")

        if body.decision == "refine":
            # the operator wants to adjust — keep the gate OPEN (do not lock it answered-once), so a
            # refined approval can still follow; the run stays paused.
            mgr.events.append(sid, {"kind": "decision", "gate_id": body.gate_id,
                                    "decision": "refine", "actor": body.actor})
            return {"session_id": sess.id, "status": "waiting_approval",
                    "gate": {"gate_id": body.gate_id, "decision": "refine", "actor": body.actor}}

        resolved = mgr.answer_gate(sess, body.gate_id, body.decision, body.actor)  # approve|deny — terminal
        # every gate decision goes on the one ordered event stream so a reconnecting / second operator
        # sees the decision, not just other users' chat (B8.3).
        mgr.events.append(sid, {"kind": "decision", "gate_id": body.gate_id,
                                "decision": resolved["decision"], "actor": body.actor})
        if resolved["decision"] == "deny":
            # the write is REFUSED — the run halts here, it does not stay paused at the gate. Project a
            # terminal 'denied' read-model (paused=False) so polling clients aren't misled into thinking
            # approval is still pending on the refused write.
            st = eng.state(sess.id)
            rm.upsert(project_incident(sess.subject.model_dump(), st["values"], eng.graph,
                                       paused=False, terminal="denied"))
            return {"session_id": sess.id, "status": "denied", "next": [], "gate": resolved}
        if eng.is_paused(sid):                       # approve — resume past the gate, recording WHO approved
            token = mgr.lock.acquire(sid, SERVER_ID)  # MUST-8 — serialize the resume too
            if token is None:
                raise HTTPException(status_code=409, detail="run is busy (locked by another worker)")
            try:
                eng.resume(sid, decision={"decision": resolved["decision"], "actor": body.actor})
            finally:
                mgr.lock.release(sid, token)
        out = _refresh(sess, eng)
        out["gate"] = resolved
        return out

    @app.get("/sessions/{sid}/incident")
    def incident(sid: str, actor: str):
        sess = _require_member(sid, actor)
        doc = rm.get(sess.subject.domain, sess.subject.id)
        if doc is None:
            raise HTTPException(status_code=404, detail="no read-model yet — advance the run first")
        return doc

    @app.post("/sessions/{sid}/messages")
    def post_message(sid: str, body: MessageBody):
        sess = _require(sid)
        try:
            mgr.require_writer(sess, body.actor)         # only the pen-holder (writer) may send
            mgr.enqueue_input(sess, {"actor": body.actor, "text": body.text})  # NICE-9 — drained into the run
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
    def events(sid: str, actor: str, after_seq: int = 0):
        _require_member(sid, actor)
        return {"events": mgr.events.since(sid, after_seq)}

    @app.get("/sessions/{sid}/stream")
    def stream(sid: str, request: Request, actor: str, after_seq: int = 0):
        # SSE — replay events since the client's last seq (Last-Event-ID) as text/event-stream, then
        # close; the browser's EventSource reconnects (~retry) for the next batch. The widget layer
        # renders each event by its `kind`; the transport is independent of the payload.
        _require_member(sid, actor)
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
    def poll(sid: str, actor: str, after_seq: int = 0):
        # the polling primitive — clients call this every few seconds: new events since their seq +
        # the read-model snapshot + the run status. No push, no Redis/WebSocket; every server
        # answers it statelessly from the shared store. Membership re-checked per poll (AC9).
        sess = _require_member(sid, actor)
        eng = engines.get(sid)
        incident = rm.get(sess.subject.domain, sess.subject.id)
        status = "new"
        if eng is not None:
            status = "waiting_approval" if eng.is_paused(sid) else "done"
        if incident and incident.get("state") == "denied":
            status = "denied"               # a refused write is terminal — the durable read-model wins
        return {
            "events": mgr.events.since(sid, after_seq),
            "seq": mgr.events.snapshot_seq(sid),
            "status": status,
            "pen_holder": sess.pen_holder,
            "role": mgr.role_of(sess, actor),       # writer|viewer — drives the UI composer/approve gating
            "incident": incident,
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
