"""Tests for the web layer (Phase 3) — non-LLM endpoints + SSE framing.

The /stream + /approve endpoints make LLM calls (exercised in the headless
end-to-end), so they aren't pytest-driven; here we test the static + data
endpoints + the SSE frame formatter."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lunasre.web import _sse, app

client = TestClient(app)


def test_healthz():
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_alerts_lists_three():
    r = client.get("/alerts")
    assert r.status_code == 200
    ids = {a["alert_id"] for a in r.json()["alerts"]}
    assert ids == {"8472", "8473", "8474"}


def test_index_served():
    r = client.get("/")
    assert r.status_code == 200
    assert "LunaSRE" in r.text
    assert "EventSource" in r.text  # the vanilla SSE client


def test_sse_frame_format():
    frame = _sse({"event": "node", "data": {"node": "investigate", "service": "payments-api"}})
    assert frame.startswith("event: node\n")
    assert '"node": "investigate"' in frame
    assert frame.endswith("\n\n")
