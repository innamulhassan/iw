"""Tests for the IC supervisor graph wiring (rca node, checkpointer) + rca skip logic.

No LLM, no network — these check graph composition + the rca-node no-op path."""

from __future__ import annotations

from pathlib import Path

import pytest

from lunasre.agents.base import load_agent_config
from lunasre.agents.ic import ICAgent, ICState
from lunasre.registries import load_agent_registry

_INFRA = Path(__file__).resolve().parents[1] / "infra" / "registries"


def _ic_agent() -> ICAgent:
    config = load_agent_config("ic-agent")
    reg = load_agent_registry(_INFRA / "agent_registry.yaml")
    return ICAgent(config, servers=[], agent_registry=reg)


def _state(**over) -> ICState:
    base: ICState = {
        "alert_id": "8472",
        "messages": [],
        "alert_type": "db-failure",
        "service": "payments-api",
        "alert_payload": None,
        "iterations": 1,
        "tool_calls_executed": 2,
        "a2a_delegations": [],
        "rca_synthesis": None,
        "summary": None,
    }
    base.update(over)
    return base


def test_graph_builds_without_checkpointer():
    graph = _ic_agent().build()
    assert graph is not None
    # The compiled graph should expose the supervisor nodes.
    nodes = set(graph.get_graph().nodes)
    assert {"investigate", "delegate", "rca", "summarize"} <= nodes


@pytest.mark.asyncio
async def test_graph_builds_with_sqlite_checkpointer():
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(":memory:") as saver:
        graph = _ic_agent().build(checkpointer=saver)
        assert graph is not None


@pytest.mark.asyncio
async def test_rca_node_skips_when_no_specialist_findings():
    """rca_node is a no-op (returns {}) when no specialist returned findings —
    so it makes no network call. Pure logic path."""
    agent = _ic_agent()
    out = await agent.rca_node(_state(a2a_delegations=[]))
    assert out == {}


@pytest.mark.asyncio
async def test_rca_node_skips_when_delegation_failed():
    agent = _ic_agent()
    failed = [{"agent": "dbops-agent", "status": "failed", "error": "boom"}]
    out = await agent.rca_node(_state(a2a_delegations=failed))
    assert out == {}
