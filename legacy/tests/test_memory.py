"""Tests for the incident memory layer (SqliteMemoryStore behind MemoryStore)."""

from __future__ import annotations

from lunasre.runtime.memory import IncidentMemory, SqliteMemoryStore


def _store(tmp_path):
    return SqliteMemoryStore(tmp_path / "mem.db")


def _incident(alert_id: str, alert_type: str, service: str) -> IncidentMemory:
    return IncidentMemory(
        alert_id=alert_id,
        alert_type=alert_type,
        service=service,
        root_cause=f"root cause for {alert_id}",
        summary=f"summary for {alert_id}",
        created_at="2026-06-08T00:00:00+00:00",
    )


def test_store_then_count(tmp_path):
    s = _store(tmp_path)
    assert s.count() == 0
    s.store_incident(_incident("8472", "db-failure", "payments-api"))
    assert s.count() == 1


def test_recall_by_alert_type(tmp_path):
    s = _store(tmp_path)
    s.store_incident(_incident("8472", "db-failure", "payments-api"))
    s.store_incident(_incident("8480", "db-failure", "orders-api"))
    s.store_incident(_incident("8473", "network-partition", "user-service"))
    hits = s.recall_similar("db-failure", "nonexistent-service", k=5)
    assert {h.alert_id for h in hits} == {"8472", "8480"}


def test_recall_by_service(tmp_path):
    s = _store(tmp_path)
    s.store_incident(_incident("8472", "db-failure", "payments-api"))
    s.store_incident(_incident("8473", "network-partition", "user-service"))
    hits = s.recall_similar("disk-full", "payments-api", k=5)
    assert {h.alert_id for h in hits} == {"8472"}


def test_recall_most_recent_first(tmp_path):
    s = _store(tmp_path)
    s.store_incident(_incident("first", "db-failure", "payments-api"))
    s.store_incident(_incident("second", "db-failure", "payments-api"))
    hits = s.recall_similar("db-failure", None, k=1)
    assert len(hits) == 1
    assert hits[0].alert_id == "second"  # newest first


def test_recall_empty_when_no_match(tmp_path):
    s = _store(tmp_path)
    s.store_incident(_incident("8472", "db-failure", "payments-api"))
    assert s.recall_similar("network-partition", "other", k=5) == []
