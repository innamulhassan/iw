"""Tests for IC config loading + registry resolution + real MCP-subprocess tool fetch.

NOTE: these tests spawn the mock_datadog MCP server as a subprocess (real L3 transport),
but they do NOT make any live LLM call — the LiteLLM proxy is exercised only by the
end-to-end run, not by pytest.
"""

from __future__ import annotations

import pytest

from lunasre.agents.base import (
    AgentConfig,
    fetch_mcp_tools,
    load_agent_config,
    resolve_mcp_servers,
)


def test_ic_config_loads_and_validates():
    config = load_agent_config("ic-agent")
    assert isinstance(config, AgentConfig)
    assert config.agent_id == "ic-agent"
    assert config.role == "incident-commander"
    assert config.llm.base_url == "http://localhost:4000"
    # Chunk 2 default model — one of the Ollama routes via the LiteLLM proxy.
    # (Exact ollama variant is tunable; the contract is: it's a non-empty string
    # that matches a model_name in infra/litellm_config.yaml.)
    assert config.llm.model.startswith("ollama")
    assert 0.0 <= config.llm.temperature <= 1.0


def test_ic_resolves_mock_datadog_from_registry():
    config = load_agent_config("ic-agent")
    servers = resolve_mcp_servers(config)
    # Chunk 2 picks exactly one server by explicit name.
    assert len(servers) == 1
    assert servers[0].name == "mock_datadog"
    assert servers[0].transport == "stdio"
    assert servers[0].command is not None
    # Verify the discovered command is module-qualified (will spawn via uv).
    assert "lunasre.mcp_servers.mock_datadog.server" in " ".join(servers[0].command)


@pytest.mark.asyncio
async def test_ic_fetches_two_tools_via_mcp_subprocess():
    """Real L3 transport — spawns mock_datadog via stdio, calls tools/list,
    expects drill_into_alert + tail_logs."""
    config = load_agent_config("ic-agent")
    servers = resolve_mcp_servers(config)
    fetched = await fetch_mcp_tools(servers[0])
    names = {t["name"] for t in fetched}
    assert names == {"drill_into_alert", "tail_logs"}
    # Both should have proper JSON-Schema-shaped input_schema.
    for tool in fetched:
        assert "input_schema" in tool
        assert tool["input_schema"].get("type") == "object"
        # Each schema should have at least one parameter defined.
        assert "properties" in tool["input_schema"]


def test_ic_config_path_mapping():
    """Agent IDs like 'ic-agent' should map to file 'ic.yaml' (strip -agent suffix)."""
    # Indirectly: loading 'ic-agent' succeeds → mapping works.
    config = load_agent_config("ic-agent")
    assert config.agent_id == "ic-agent"

    with pytest.raises(FileNotFoundError):
        load_agent_config("nonexistent-agent")
