"""P6 · the HTTP API (FastAPI) — contract tests via TestClient.

Drives the INC-4821 flow over HTTP: create session → advance (runs to the gate) → read the incident
document → answer the gate (approve → resume) → run completes. Plus chat + per-event auth + feedback.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from engine.api import create_app
from engine.session import SessionManager
from fixtures.mock_engine import build_engine


@pytest.fixture
def client(playbook):
    app = create_app(SessionManager(), lambda subject: build_engine(playbook))
    return TestClient(app)


def _create(client, actor="alice"):
    r = client.post("/sessions", json={"domain": "app-incident", "id": "INC-4821",
                                       "kind": "incident", "actor": actor})
    assert r.status_code == 200
    return r.json()["session_id"]


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
    doc = client.get(f"/sessions/{sid}/incident").json()
    assert [p["phase"] for p in doc["phases"]] == ["assess", "root-cause"]
    assert doc["graph"]["node_count"] >= 2
    assert doc["symptom"]                                 # read-model projected from Assess


def test_gate_approve_resumes_to_completion(client):
    sid = _create(client)
    client.post(f"/sessions/{sid}/advance")
    body = client.post(f"/sessions/{sid}/gate",
                       json={"actor": "alice", "gate_id": "g1", "decision": "approve"}).json()
    assert body["status"] == "done"
    assert body["next"] == []
    doc = client.get(f"/sessions/{sid}/incident").json()
    assert [p["phase"] for p in doc["phases"]] == ["assess", "root-cause", "remediation", "verify-close"]
    assert all(p["state"] == "done" for p in doc["phases"])


def test_gate_is_writer_only_then_answered_once(client):
    sid = _create(client, "alice")                       # alice holds the pen (writer)
    client.post("/sessions", json={"domain": "app-incident", "id": "INC-4821",
                                   "kind": "incident", "actor": "bob"})   # bob joins as a viewer
    client.post(f"/sessions/{sid}/advance")
    # a viewer cannot approve the gate
    denied = client.post(f"/sessions/{sid}/gate",
                         json={"actor": "bob", "gate_id": "g1", "decision": "approve"})
    assert denied.status_code == 403
    # the writer approves; a repeat is idempotent (answered-once)
    first = client.post(f"/sessions/{sid}/gate",
                        json={"actor": "alice", "gate_id": "g1", "decision": "approve"}).json()
    second = client.post(f"/sessions/{sid}/gate",
                         json={"actor": "alice", "gate_id": "g1", "decision": "deny"}).json()
    assert first["gate"] == second["gate"]
    assert first["gate"]["decision"] == "approve"


def test_gate_refine_keeps_open_then_approve(client):
    sid = _create(client, "alice")
    client.post(f"/sessions/{sid}/advance")              # paused at the gate
    refined = client.post(f"/sessions/{sid}/gate",
                          json={"actor": "alice", "gate_id": "g1", "decision": "refine"}).json()
    assert refined["status"] == "waiting_approval"       # gate stays OPEN, not locked
    done = client.post(f"/sessions/{sid}/gate",
                       json={"actor": "alice", "gate_id": "g1", "decision": "approve"}).json()
    assert done["status"] == "done"                      # a refined approval still proceeds


def test_gate_deny_halts_not_stuck(client):
    sid = _create(client, "alice")
    client.post(f"/sessions/{sid}/advance")
    denied = client.post(f"/sessions/{sid}/gate",
                         json={"actor": "alice", "gate_id": "g1", "decision": "deny"}).json()
    assert denied["status"] == "denied"                  # run halts — not stuck on waiting_approval


def test_chat_messages_and_replay(client):
    sid = _create(client, "alice")
    client.post(f"/sessions/{sid}/messages", json={"actor": "alice", "text": "checkout is slow"})
    client.post(f"/sessions/{sid}/messages", json={"actor": "alice", "text": "errors climbing"})
    evs = client.get(f"/sessions/{sid}/events").json()["events"]
    assert [e["text"] for e in evs] == ["checkout is slow", "errors climbing"]
    # resume-from-seq
    tail = client.get(f"/sessions/{sid}/events", params={"after_seq": 1}).json()["events"]
    assert [e["text"] for e in tail] == ["errors climbing"]


def test_poll_returns_deltas_and_snapshot(client):
    sid = _create(client, "alice")
    client.post(f"/sessions/{sid}/advance")                      # run to the gate → read-model exists
    client.post(f"/sessions/{sid}/messages", json={"actor": "alice", "text": "any update?"})
    body = client.get(f"/sessions/{sid}/poll", params={"after_seq": 0}).json()
    assert body["status"] == "waiting_approval"
    assert body["incident"]["_id"] == "INC-4821"                 # the read-model snapshot
    assert any(e["text"] == "any update?" for e in body["events"])
    # resume-from-seq: a follow-up poll from the latest seq returns no repeats
    tail = client.get(f"/sessions/{sid}/poll", params={"after_seq": body["seq"]}).json()
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
    client.post("/sessions", json={"domain": "app-incident", "id": "INC-4821",
                                   "kind": "incident", "actor": "bob"})   # bob joins as viewer
    # a viewer cannot send
    assert client.post(f"/sessions/{sid}/messages", json={"actor": "bob", "text": "hi"}).status_code == 403
    # bob cannot take the pen while alice holds it
    r = client.post(f"/sessions/{sid}/take-pen", json={"actor": "bob"}).json()
    assert r["ok"] is False and r["pen_holder"] == "alice"
    # alice releases → bob takes it → bob can now send
    client.post(f"/sessions/{sid}/release-pen", json={"actor": "alice"})
    took = client.post(f"/sessions/{sid}/take-pen", json={"actor": "bob"}).json()
    assert took["ok"] is True and took["role"] == "writer"
    assert client.post(f"/sessions/{sid}/messages",
                       json={"actor": "bob", "text": "now I can write"}).status_code == 200


def test_sse_stream_replays_events_by_seq(client):
    sid = _create(client, "alice")
    client.post(f"/sessions/{sid}/messages", json={"actor": "alice", "text": "first"})
    client.post(f"/sessions/{sid}/messages", json={"actor": "alice", "text": "second"})
    r = client.get(f"/sessions/{sid}/stream")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    assert "first" in r.text and "second" in r.text
    assert "id: 1" in r.text and "id: 2" in r.text       # SSE event ids = seq
    # resume from a seq (Last-Event-ID) returns only later events
    tail = client.get(f"/sessions/{sid}/stream", headers={"Last-Event-ID": "1"}).text
    assert "second" in tail and "first" not in tail
