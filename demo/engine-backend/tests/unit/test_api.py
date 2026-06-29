"""P6 · the HTTP API (FastAPI) — contract tests via TestClient.

Drives the INC-4821 flow over HTTP: create session → advance (runs to the gate) → read the incident
document → answer the gate (approve → resume) → run completes. Plus chat + per-event auth + feedback.
Read endpoints take `?actor=` and re-check membership (AC9); the gate_id is bound to the pending pause.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from engine.api import create_app
from engine.session import SessionManager
from fixtures.mock_engine import build_engine

GATE = "remediation"   # the run pauses (interrupt_before) at the write phase — the server-derived gate id


@pytest.fixture
def client(playbook):
    app = create_app(SessionManager(), lambda subject: build_engine(playbook))
    return TestClient(app)


def _create(client, actor="alice"):
    r = client.post("/sessions", json={"domain": "app-incident", "id": "INC-4821",
                                       "kind": "incident", "actor": actor})
    assert r.status_code == 200
    return r.json()["session_id"]


def _join(client, actor):
    client.post("/sessions", json={"domain": "app-incident", "id": "INC-4821",
                                   "kind": "incident", "actor": actor})


def _get(client, sid, path, actor="alice", **params):
    return client.get(f"/sessions/{sid}/{path}", params={"actor": actor, **params})


def _gate(client, sid, decision, actor="alice", gate_id=GATE):
    return client.post(f"/sessions/{sid}/gate",
                       json={"actor": actor, "gate_id": gate_id, "decision": decision})


def test_create_session_is_idempotent(client):
    sid = _create(client, "alice")
    assert sid == "app-incident:INC-4821"
    r = client.post("/sessions", json={"domain": "app-incident", "id": "INC-4821",
                                       "kind": "incident", "actor": "bob"})
    assert r.json()["members"] == ["alice", "bob"]      # joined, not a 2nd thread


def test_advance_runs_to_the_gate(client):
    sid = _create(client)
    body = client.post(f"/sessions/{sid}/advance").json()
    assert body["status"] == "waiting_approval"
    assert body["next"] == ["remediation"]              # paused before the write phase
    doc = _get(client, sid, "incident").json()
    assert [p["phase"] for p in doc["phases"]] == ["assess", "root-cause"]
    assert doc["graph"]["node_count"] >= 2
    assert doc["symptom"]                                 # read-model projected from Assess


def test_gate_approve_resumes_to_completion(client):
    sid = _create(client)
    client.post(f"/sessions/{sid}/advance")
    body = _gate(client, sid, "approve").json()
    assert body["status"] == "done"
    assert body["next"] == []
    doc = _get(client, sid, "incident").json()
    assert [p["phase"] for p in doc["phases"]] == ["assess", "root-cause", "remediation", "verify-close"]
    assert all(p["state"] == "done" for p in doc["phases"])


def test_gate_is_writer_only_then_answered_once(client):
    sid = _create(client, "alice")                       # alice holds the pen (writer)
    _join(client, "bob")                                 # bob joins as a viewer
    client.post(f"/sessions/{sid}/advance")
    assert _gate(client, sid, "approve", actor="bob").status_code == 403   # a viewer cannot approve
    # the writer approves; a repeat is idempotent (answered-once)
    first = _gate(client, sid, "approve").json()
    second = _gate(client, sid, "deny").json()
    assert first["gate"] == second["gate"]
    assert first["gate"]["decision"] == "approve"


def test_gate_refine_keeps_open_then_approve(client):
    sid = _create(client, "alice")
    client.post(f"/sessions/{sid}/advance")              # paused at the gate
    refined = _gate(client, sid, "refine").json()
    assert refined["status"] == "waiting_approval"       # gate stays OPEN, not locked
    done = _gate(client, sid, "approve").json()
    assert done["status"] == "done"                      # a refined approval still proceeds


def test_gate_deny_halts_not_stuck(client):
    sid = _create(client, "alice")
    client.post(f"/sessions/{sid}/advance")
    denied = _gate(client, sid, "deny").json()
    assert denied["status"] == "denied"                  # run halts — not stuck on waiting_approval


def test_chat_messages_and_replay(client):
    sid = _create(client, "alice")
    client.post(f"/sessions/{sid}/messages", json={"actor": "alice", "text": "checkout is slow"})
    client.post(f"/sessions/{sid}/messages", json={"actor": "alice", "text": "errors climbing"})
    evs = _get(client, sid, "events").json()["events"]
    assert [e["text"] for e in evs] == ["checkout is slow", "errors climbing"]
    tail = _get(client, sid, "events", after_seq=1).json()["events"]
    assert [e["text"] for e in tail] == ["errors climbing"]


def test_poll_returns_deltas_and_snapshot(client):
    sid = _create(client, "alice")
    client.post(f"/sessions/{sid}/advance")                      # run to the gate → read-model exists
    client.post(f"/sessions/{sid}/messages", json={"actor": "alice", "text": "any update?"})
    body = _get(client, sid, "poll", after_seq=0).json()
    assert body["status"] == "waiting_approval"
    assert body["incident"]["_id"] == "INC-4821"                 # the read-model snapshot
    assert body["role"] == "writer"                              # role drives the UI gating (MUST-11)
    assert any(e.get("text") == "any update?" for e in body["events"])   # chat among the deltas
    assert any(e.get("kind") == "phase" for e in body["events"])          # phase progress on the stream
    tail = _get(client, sid, "poll", after_seq=body["seq"]).json()
    assert tail["events"] == []


def test_message_from_non_member_is_forbidden(client):
    sid = _create(client, "alice")
    r = client.post(f"/sessions/{sid}/messages", json={"actor": "mallory", "text": "let me in"})
    assert r.status_code == 403


def test_feedback_is_recorded(client):
    r = client.post("/feedback", json={"domain": "app-incident", "id": "INC-4821",
                                       "kind": "outcome", "actor": "alice", "verdict": "fix held"})
    assert r.json() == {"stored": True, "kind": "outcome"}


def test_pen_one_writer_at_a_time(client):
    sid = _create(client, "alice")                       # alice creates → holds the pen
    _join(client, "bob")                                 # bob joins as viewer
    assert client.post(f"/sessions/{sid}/messages", json={"actor": "bob", "text": "hi"}).status_code == 403
    r = client.post(f"/sessions/{sid}/take-pen", json={"actor": "bob"}).json()
    assert r["ok"] is False and r["pen_holder"] == "alice"
    client.post(f"/sessions/{sid}/release-pen", json={"actor": "alice"})
    took = client.post(f"/sessions/{sid}/take-pen", json={"actor": "bob"}).json()
    assert took["ok"] is True and took["role"] == "writer"
    assert client.post(f"/sessions/{sid}/messages",
                       json={"actor": "bob", "text": "now I can write"}).status_code == 200


def test_sse_stream_replays_events_by_seq(client):
    sid = _create(client, "alice")
    client.post(f"/sessions/{sid}/messages", json={"actor": "alice", "text": "first"})
    client.post(f"/sessions/{sid}/messages", json={"actor": "alice", "text": "second"})
    r = _get(client, sid, "stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "first" in r.text and "second" in r.text
    assert "id: 1" in r.text and "id: 2" in r.text       # SSE event ids = seq
    tail = client.get(f"/sessions/{sid}/stream", params={"actor": "alice"},
                      headers={"Last-Event-ID": "1"}).text
    assert "second" in tail and "first" not in tail


# ── audit fixes: gate bypass, deny terminal in the read-model, decision on the event stream ──
def test_advance_on_paused_run_is_rejected(client):
    sid = _create(client, "alice")
    client.post(f"/sessions/{sid}/advance")                      # runs to the gate (paused)
    r = client.post(f"/sessions/{sid}/advance")                  # must NOT resume past the gate
    assert r.status_code == 409
    assert _get(client, sid, "poll").json()["status"] == "waiting_approval"   # still paused


def test_gate_deny_is_terminal_in_readmodel(client):
    sid = _create(client, "alice")
    client.post(f"/sessions/{sid}/advance")
    _gate(client, sid, "deny")
    assert _get(client, sid, "incident").json()["state"] == "denied"
    poll = _get(client, sid, "poll").json()
    assert poll["status"] == "denied" and poll["incident"]["state"] == "denied"


def test_gate_decision_appears_on_the_event_stream(client):
    sid = _create(client, "alice")
    client.post(f"/sessions/{sid}/advance")
    _gate(client, sid, "approve")
    evs = _get(client, sid, "events").json()["events"]
    decisions = [e for e in evs if e.get("kind") == "decision"]
    assert decisions and decisions[-1]["decision"] == "approve" and decisions[-1]["actor"] == "alice"


# ── audit fixes (foundation wave): run-lock, gate-id binding, read authz, input drain ──
def test_advance_is_locked_against_concurrent_runs(playbook):
    mgr = SessionManager()
    app = create_app(mgr, lambda subject: build_engine(playbook))
    client = TestClient(app)
    sid = _create(client, "alice")
    token = mgr.lock.acquire(sid, "other-worker")        # another worker holds the run lock
    assert token is not None
    assert client.post(f"/sessions/{sid}/advance").status_code == 409   # can't advance while locked


def test_gate_id_must_match_the_pending_pause(client):
    sid = _create(client, "alice")
    client.post(f"/sessions/{sid}/advance")
    r = _gate(client, sid, "approve", gate_id="WRONG")
    assert r.status_code == 409                          # an arbitrary gate_id can't approve the pause


def test_read_surface_requires_membership(client):
    sid = _create(client, "alice")
    client.post(f"/sessions/{sid}/advance")              # read-model exists
    assert _get(client, sid, "incident", actor="mallory").status_code == 403   # non-member (AC9)
    assert _get(client, sid, "poll", actor="mallory").status_code == 403
    assert _get(client, sid, "events", actor="mallory").status_code == 403


def test_queued_operator_input_is_drained_into_the_run(playbook):
    mgr = SessionManager()
    app = create_app(mgr, lambda subject: build_engine(playbook))
    client = TestClient(app)
    sid = _create(client, "alice")
    client.post(f"/sessions/{sid}/messages", json={"actor": "alice", "text": "also check the cache"})
    assert mgr.get(sid).input_queue                      # queued (not a 2nd run)
    client.post(f"/sessions/{sid}/advance")              # drains at the step boundary
    assert mgr.get(sid).input_queue == []
    msgs = app.state.engines[sid].state(sid)["values"].get("messages", [])
    assert any(m.get("text") == "also check the cache" for m in msgs)   # merged into the one run
