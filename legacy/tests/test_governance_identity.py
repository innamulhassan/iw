"""Tests for Phase-4 governance (audit) + identity (workload tokens). No LLM."""

from __future__ import annotations

import jwt
import pytest

from lunasre.runtime.audit import AuditLog, _fingerprint
from lunasre.runtime.identity import (
    caller_from_authorization,
    mint_token,
    verify_token,
)

# ── Audit (L9) ──────────────────────────────────────────────────────────────────────────────────


def test_audit_records_and_queries(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    assert log.count() == 0
    log.record(
        agent_id="ic-agent",
        action="mcp.tool_call",
        target="mock_datadog.drill_into_alert",
        args={"alert_id": "8472"},
        result={"type": "db-failure"},
    )
    log.record(agent_id="ic-agent", action="a2a.delegate", target="http://localhost:8003")
    assert log.count() == 2
    recent = log.recent()
    assert recent[0]["action"] == "a2a.delegate"  # newest first
    assert recent[1]["target"] == "mock_datadog.drill_into_alert"
    assert recent[1]["agent_id"] == "ic-agent"


def test_audit_fingerprint_is_stable_and_not_plaintext():
    fp = _fingerprint({"alert_id": "8472"})
    assert fp.startswith("sha256:")
    assert "8472" not in fp  # payload not stored verbatim
    assert fp == _fingerprint({"alert_id": "8472"})  # stable


def test_audit_records_failure_flag(tmp_path):
    log = AuditLog(tmp_path / "audit.db")
    log.record(agent_id="dbops-agent", action="mcp.tool_call", target="mock_logs.grep", ok=False)
    assert log.recent()[0]["ok"] == 0


# ── Identity (L12) ──────────────────────────────────────────────────────────────────────────────


def test_mint_then_verify_roundtrip():
    claims = verify_token(mint_token("dbops-agent", scopes=["db-incident"]))
    assert claims["sub"] == "dbops-agent"
    assert claims["scopes"] == ["db-incident"]


def test_caller_from_valid_bearer():
    agent, verified = caller_from_authorization(f"Bearer {mint_token('ic-agent')}")
    assert agent == "ic-agent" and verified is True


def test_caller_from_missing_is_anonymous_permissive():
    agent, verified = caller_from_authorization(None)
    assert agent == "anonymous" and verified is False


def test_caller_from_tampered_is_anonymous_permissive():
    agent, verified = caller_from_authorization("Bearer not.a.real.token")
    assert agent == "anonymous" and verified is False


def test_strict_mode_rejects_missing(monkeypatch):
    monkeypatch.setenv("LUNASRE_ENFORCE_IDENTITY", "1")
    with pytest.raises(PermissionError):
        caller_from_authorization(None)


def test_tampered_token_fails_verify():
    good = mint_token("ic-agent")
    tampered = good[:-3] + "xxx"
    with pytest.raises(jwt.InvalidTokenError):
        verify_token(tampered)
