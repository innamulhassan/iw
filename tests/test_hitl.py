"""Tests for the HITL (Phase 3) graph wiring + execute_remediation node.

No LLM: these check the human-gated execute node logic + that the HITL graph is
compiled with the execute_remediation node + interrupt. The full interrupt→resume
across a checkpointer is exercised by the headless end-to-end (makes LLM calls),
not by pytest."""

from __future__ import annotations

from pathlib import Path

import pytest

from lunasre.agents.base import load_agent_config
from lunasre.agents.ic import ICAgent, ICState, _extract_section
from lunasre.registries import load_agent_registry

_INFRA = Path(__file__).resolve().parents[1] / "infra" / "registries"


def _ic_agent() -> ICAgent:
    config = load_agent_config("ic-agent")
    reg = load_agent_registry(_INFRA / "agent_registry.yaml")
    return ICAgent(config, servers=[], agent_registry=reg)


def _state(**over) -> ICState:
    base: ICState = {
        "alert_id": "8472",
        "messages": [{"role": "assistant", "content": "report"}],
        "alert_type": "db-failure",
        "service": "payments-api",
        "alert_payload": None,
        "iterations": 1,
        "tool_calls_executed": 2,
        "a2a_delegations": [],
        "rca_synthesis": None,
        "summary": "WHAT: db down\nREMEDIATION: failover to replica\nVERIFY NEXT: check lag",
        "proposed_remediation": "failover to replica",
        "approved": None,
        "executed": False,
    }
    base.update(over)
    return base


def test_extract_section_pulls_remediation():
    text = "WHAT: x\nREMEDIATION: do the failover\nVERIFY NEXT: y"
    assert _extract_section(text, "REMEDIATION") == "do the failover"


def test_extract_section_missing_returns_none():
    assert _extract_section("WHAT: x\nWHEN: y", "REMEDIATION") is None


@pytest.mark.asyncio
async def test_execute_node_executes_when_approved():
    agent = _ic_agent()
    out = await agent.execute_remediation_node(_state(approved=True))
    assert out["executed"] is True
    assert "EXECUTED" in out["messages"][-1]["content"]


@pytest.mark.asyncio
async def test_execute_node_skips_when_rejected():
    agent = _ic_agent()
    out = await agent.execute_remediation_node(_state(approved=False))
    assert out["executed"] is False
    assert "REJECTED" in out["messages"][-1]["content"]


def test_hitl_graph_has_execute_node():
    graph = _ic_agent().build(hitl=True)
    nodes = set(graph.get_graph().nodes)
    assert "execute_remediation" in nodes
    assert {"investigate", "delegate", "rca", "summarize", "execute_remediation"} <= nodes


def test_non_hitl_graph_has_no_execute_node():
    graph = _ic_agent().build(hitl=False)
    assert "execute_remediation" not in set(graph.get_graph().nodes)


@pytest.mark.asyncio
async def test_hitl_graph_builds_with_checkpointer():
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        graph = _ic_agent().build(hitl=True, checkpointer=saver)
        assert graph is not None
