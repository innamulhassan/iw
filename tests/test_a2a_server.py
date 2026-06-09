"""Tests for the minimal A2A server (Phase 2) — card publication + message routing.

No LLM: the message handler is a stub. These verify the A2A *seam* (Agent Card
JSON shape + /a2a/message round-trip), which is what IC's delegation depends on.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from lunasre.runtime.a2a_server import (
    A2AMessageRequest,
    A2AMessageResponse,
    build_a2a_app,
    build_agent_card,
)


def _stub_app():
    card = build_agent_card(
        name="dbops-agent",
        description="db specialist",
        url="http://localhost:8003",
        skills=[("db-incident", "Investigate db alerts"), ("db-failover", "Recommend failover")],
    )

    async def handler(req: A2AMessageRequest) -> A2AMessageResponse:
        # Echo-style stub — no LLM.
        return A2AMessageResponse(content=f"STUB FINDINGS for: {req.content[:40]}")

    return build_a2a_app(card, handler)


def test_agent_card_served_as_spec_camelcase_json():
    client = TestClient(_stub_app())
    resp = client.get("/.well-known/agent.json")
    assert resp.status_code == 200
    card = resp.json()
    # Spec camelCase keys.
    assert card["name"] == "dbops-agent"
    assert card["protocolVersion"] == "0.3.0"
    assert card["defaultInputModes"] == ["text/plain"]
    assert card["capabilities"]["pushNotifications"] is False
    assert {s["id"] for s in card["skills"]} == {"db-incident", "db-failover"}


def test_healthz():
    client = TestClient(_stub_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_a2a_message_roundtrip():
    client = TestClient(_stub_app())
    resp = client.post(
        "/a2a/message",
        json={"role": "user", "content": "Investigate db-failure on payments-api"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["role"] == "assistant"
    assert "STUB FINDINGS" in body["content"]


def test_a2a_message_accepts_context():
    client = TestClient(_stub_app())
    resp = client.post(
        "/a2a/message",
        json={
            "role": "user",
            "content": "investigate",
            "context": {"service": "payments-api", "alert_id": "8472"},
        },
    )
    assert resp.status_code == 200
