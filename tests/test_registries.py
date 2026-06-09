"""Smoke tests for the registry interface (Chunk 1)."""

from pathlib import Path

import pytest

from lunasre.registries import load_agent_registry, load_mcp_registry

_INFRA = Path(__file__).resolve().parents[1] / "infra" / "registries"


def test_mcp_registry_loads_four_servers():
    reg = load_mcp_registry(_INFRA / "mcp_registry.yaml")
    assert len(reg) == 4
    names = [e.name for e in reg]
    assert set(names) == {"mock_datadog", "mock_logs", "mock_traces", "mock_pg"}


def test_mcp_registry_sessions_flag():
    reg = load_mcp_registry(_INFRA / "mcp_registry.yaml")
    assert reg.get("mock_logs").supports_sessions is True
    assert reg.get("mock_datadog").supports_sessions is False


def test_mcp_registry_capability_lookup():
    reg = load_mcp_registry(_INFRA / "mcp_registry.yaml")
    found = reg.find_by_capability("alert-drill")
    assert len(found) == 1
    assert found[0].name == "mock_datadog"


def test_agent_registry_loads_seven_agents():
    reg = load_agent_registry(_INFRA / "agent_registry.yaml")
    assert len(reg) == 7
    names = [e.name for e in reg]
    assert set(names) == {
        "ic-agent",
        "rca-agent",
        "dbops-agent",
        "netops-agent",
        "deployops-agent",
        "pm-writer",
        "grok-reviewer",
    }


def test_agent_registry_router_not_present():
    """Router is graph-first, NOT an A2A peer — must not be in agent_registry."""
    reg = load_agent_registry(_INFRA / "agent_registry.yaml")
    with pytest.raises(KeyError):
        reg.get("router")


@pytest.mark.parametrize(
    ("alert_type", "expected_agent"),
    [
        ("db-failure", "dbops-agent"),
        ("network-partition", "netops-agent"),
        ("deploy-regression", "deployops-agent"),
    ],
)
def test_agent_registry_specialist_routing(alert_type: str, expected_agent: str):
    reg = load_agent_registry(_INFRA / "agent_registry.yaml")
    resolved = reg.find_by_trigger(alert_type)
    assert resolved is not None
    assert resolved.name == expected_agent
