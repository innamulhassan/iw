"""Tests for IC's supervisor routing (Phase 2) — the deterministic delegate-or-not edge.

No LLM: route_after_investigate is pure logic over (alert_type, delegation map,
agent registry). L29.P: all 3 alert types are now mapped to specialists.
"""

from __future__ import annotations

from pathlib import Path

from lunasre.agents.base import (
    AgentConfig,
    DelegationConfig,
    LLMConfig,
    RegistryConfig,
    RuntimeConfig,
    ToolsConfig,
    load_agent_config,
)
from lunasre.agents.ic import ICAgent, ICState
from lunasre.registries import load_agent_registry

_INFRA = Path(__file__).resolve().parents[1] / "infra" / "registries"


def _agent_registry():
    return load_agent_registry(_INFRA / "agent_registry.yaml")


def _ic_agent(delegation_map: dict[str, str] | None = None) -> ICAgent:
    if delegation_map is None:
        config = load_agent_config("ic-agent")
    else:
        config = AgentConfig(
            agent_id="ic-agent",
            role="incident-commander",
            llm=LLMConfig(base_url="http://localhost:4000", model="ollama-mistral"),
            registries=RegistryConfig(
                mcp="infra/registries/mcp_registry.yaml",
                agent="infra/registries/agent_registry.yaml",
            ),
            tools=ToolsConfig(use_servers=["mock_datadog"]),
            runtime=RuntimeConfig(graph="supervisor", max_tool_iterations=4),
            delegation=DelegationConfig(by_alert_type=delegation_map),
        )
    return ICAgent(config, servers=[], agent_registry=_agent_registry())


def _state(alert_type: str | None) -> ICState:
    return {
        "alert_id": "8472",
        "messages": [],
        "alert_type": alert_type,
        "service": "payments-api",
        "alert_payload": None,
        "iterations": 1,
        "tool_calls_executed": 2,
        "a2a_delegations": [],
        "rca_synthesis": None,
        "summary": None,
    }


def test_all_three_alert_types_route_to_delegate():
    """L29.P: db-failure, network-partition, deploy-regression all map to specialists."""
    agent = _ic_agent()
    assert agent.route_after_investigate(_state("db-failure")) == "delegate"
    assert agent.route_after_investigate(_state("network-partition")) == "delegate"
    assert agent.route_after_investigate(_state("deploy-regression")) == "delegate"


def test_unknown_alert_type_routes_to_summarize():
    agent = _ic_agent()
    assert agent.route_after_investigate(_state("disk-full")) == "summarize"


def test_none_alert_type_routes_to_summarize():
    agent = _ic_agent()
    assert agent.route_after_investigate(_state(None)) == "summarize"


def test_mapped_but_missing_agent_routes_to_summarize():
    agent = _ic_agent({"db-failure": "ghost-agent-not-in-registry"})
    assert agent.route_after_investigate(_state("db-failure")) == "summarize"


def test_registry_resolves_all_specialist_card_urls():
    reg = _agent_registry()
    assert reg.get("dbops-agent").card_url.endswith("8003/.well-known/agent.json")
    assert reg.get("netops-agent").card_url.endswith("8004/.well-known/agent.json")
    assert reg.get("deployops-agent").card_url.endswith("8005/.well-known/agent.json")
    assert reg.get("rca-agent").card_url.endswith("8002/.well-known/agent.json")
