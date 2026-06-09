"""Tests for Phase-4 MCP Gateway scope (L3 swap) + OTel observability (L8). No LLM."""

from __future__ import annotations

from pathlib import Path

import pytest

from lunasre.registries import load_gateway_registry, load_mcp_registry
from lunasre.runtime.observability import install_memory_exporter, span

_INFRA = Path(__file__).resolve().parents[1] / "infra" / "registries"
_MCP = _INFRA / "mcp_registry.yaml"
_SCOPES = _INFRA / "gateway_scopes.yaml"


# ── MCP Gateway scope (L3) ───────────────────────────────────────────────────────────────────────


def test_gateway_satisfies_same_interface_as_file_registry():
    """Both expose .get / .find_by_capability / .all — the swap-without-change proof."""
    file_reg = load_mcp_registry(_MCP)
    gw = load_gateway_registry(_MCP, "ic-agent", _SCOPES)
    for attr in ("get", "find_by_capability", "all"):
        assert hasattr(file_reg, attr) and hasattr(gw, attr)


def test_gateway_enforces_ic_scope():
    """ic-agent scope = [mock_datadog]; it may resolve that + nothing else."""
    gw = load_gateway_registry(_MCP, "ic-agent", _SCOPES)
    assert gw.get("mock_datadog").name == "mock_datadog"
    assert {e.name for e in gw.all()} == {"mock_datadog"}
    with pytest.raises(KeyError):
        gw.get("mock_logs")  # outside IC's scope -> gateway refuses


def test_gateway_dbops_scope_includes_logs_and_pg():
    gw = load_gateway_registry(_MCP, "dbops-agent", _SCOPES)
    assert {e.name for e in gw.all()} == {"mock_logs", "mock_pg"}


def test_gateway_rca_has_empty_scope():
    gw = load_gateway_registry(_MCP, "rca-agent", _SCOPES)
    assert gw.all() == []
    with pytest.raises(KeyError):
        gw.get("mock_datadog")


def test_gateway_unlisted_agent_gets_all():
    """An agent with no scope entry gets all servers (scope=None)."""
    gw = load_gateway_registry(_MCP, "some-new-agent", _SCOPES)
    assert len(gw.all()) == 4


def test_gateway_find_by_capability_respects_scope():
    gw = load_gateway_registry(_MCP, "ic-agent", _SCOPES)
    # alert-drill is on mock_datadog (in scope) -> found; log-search is on mock_logs (out) -> empty
    assert [e.name for e in gw.find_by_capability("alert-drill")] == ["mock_datadog"]
    assert gw.find_by_capability("log-search") == []


def test_resolve_mcp_servers_with_gateway_kind():
    """Flipping registries.kind file->gateway changes resolution; agent code unchanged."""
    from lunasre.agents.base import RegistryConfig, load_agent_config, resolve_mcp_servers

    config = load_agent_config("ic-agent").model_copy(
        update={
            "registries": RegistryConfig(
                mcp="infra/registries/mcp_registry.yaml",
                agent="infra/registries/agent_registry.yaml",
                kind="gateway",
                scopes="infra/registries/gateway_scopes.yaml",
            )
        }
    )
    servers = resolve_mcp_servers(config)
    assert [s.name for s in servers] == ["mock_datadog"]  # IC scope honored


# ── Observability (L8) ───────────────────────────────────────────────────────────────────────────


def test_span_captured_by_memory_exporter():
    exporter = install_memory_exporter()
    with span("mcp.tool_call", agent="ic-agent", server="mock_datadog", tool="drill_into_alert"):
        pass
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    sp = spans[0]
    assert sp.name == "mcp.tool_call"
    assert sp.attributes["lunasre.agent"] == "ic-agent"
    assert sp.attributes["lunasre.tool"] == "drill_into_alert"


def test_nested_spans_captured():
    exporter = install_memory_exporter()
    with span("ic.run", alert="8472"):
        with span("a2a.delegate", caller="ic-agent", target="dbops"):
            pass
    names = {s.name for s in exporter.get_finished_spans()}
    assert {"ic.run", "a2a.delegate"} <= names
