"""Transport-seam tests (VALIDATION-VERDICT §C.2). The fetch seam has three transports behind
one `fetch(binding, intent, params)` signature; normalize() is identical across all of them.
These prove the SHAPE of McpSource/RestSource/RoutedSource against a fake HTTP — no live server
is ever required (MockSource remains the hermetic transport the rest of the suite uses)."""
from __future__ import annotations

import json

import pytest

from iw_engine.capability import (
    CapabilityCall,
    CapabilityLayer,
    McpSource,
    MockSource,
    RestSource,
    RoutedSource,
)
from iw_engine.capability.adapters.prometheus import PrometheusAdapter
from iw_engine.domain.enums import Binding, Effect


# ── McpSource: one generic tools/call, vendor JSON handed through verbatim ────────
def test_mcp_source_issues_generic_tools_call_and_returns_structured_json():
    seen = {}

    def fake_http(endpoint, payload, headers):
        seen.update(endpoint=endpoint, payload=payload, headers=headers)
        return {"jsonrpc": "2.0", "id": payload["id"],
                "result": {"structuredContent": {"service": {"name": "payments-api", "env": "prod"}},
                           "isError": False}}

    src = McpSource("https://mcp.example/rpc", transport=fake_http, token="t0ken")
    raw = src.fetch(Binding.MCP, "get_incident", {"number": "INC-1"})

    # ONE generic tools/call — a new MCP vendor is a config line, not new code
    assert seen["payload"]["method"] == "tools/call"
    assert seen["payload"]["params"] == {"name": "get_incident", "arguments": {"number": "INC-1"}}
    assert seen["headers"]["Authorization"] == "Bearer t0ken"
    # vendor JSON is returned verbatim (it must NOT collapse into the closed ops)
    assert raw == {"service": {"name": "payments-api", "env": "prod"}}


def test_mcp_source_parses_json_text_content_block():
    def fake_http(endpoint, payload, headers):
        return {"result": {"content": [{"type": "text",
                                        "text": json.dumps({"alerts": [{"id": "A1"}]})}]}}

    src = McpSource("https://mcp.example/rpc", transport=fake_http)
    assert src.fetch(Binding.MCP, "active_alerts", {}) == {"alerts": [{"id": "A1"}]}


def test_mcp_source_raises_on_tool_error():
    def fake_http(endpoint, payload, headers):
        return {"result": {"isError": True, "content": [{"type": "text", "text": "boom"}]}}

    src = McpSource("https://mcp.example/rpc", transport=fake_http)
    with pytest.raises(RuntimeError):
        src.fetch(Binding.MCP, "get_incident", {})


def test_mcp_source_raises_on_jsonrpc_error():
    src = McpSource("x", transport=lambda *a: {"error": {"code": -32601, "message": "no method"}})
    with pytest.raises(RuntimeError):
        src.fetch(Binding.MCP, "get_incident", {})


# ── RestSource: intent -> route, JSON body handed through ─────────────────────────
def test_rest_source_routes_intent_and_returns_json():
    seen = {}

    def fake_http(url, params, headers):
        seen.update(url=url, params=params)
        return {"data": {"result": []}}

    src = RestSource("https://prom.example/", {"instant_query": "/api/v1/query"}, transport=fake_http)
    raw = src.fetch(Binding.REST, "instant_query", {"query": "up"})

    assert seen["url"] == "https://prom.example/api/v1/query"   # base_url trailing slash trimmed
    assert seen["params"] == {"query": "up"}
    assert raw == {"data": {"result": []}}


def test_rest_source_unrouted_intent_is_empty_raw():
    src = RestSource("https://prom.example", {}, transport=lambda *a: {"unexpected": True})
    assert src.fetch(Binding.REST, "not_wired", {}) == {}


def test_rest_source_wraps_non_dict_body_in_records():
    src = RestSource("https://prom.example", {"list_promotions": "/promos"},
                     transport=lambda url, params, headers: [{"id": 1}, {"id": 2}])
    assert src.fetch(Binding.REST, "list_promotions", {}) == {"records": [{"id": 1}, {"id": 2}]}


# ── RoutedSource: dispatch the one seam on the adapter's Binding ───────────────────
def test_routed_source_dispatches_on_binding():
    calls: dict[str, list] = {"mcp": [], "rest": []}

    class _T:
        def __init__(self, tag):
            self.tag = tag

        def fetch(self, binding, intent, params):
            calls[self.tag].append(intent)
            return {"via": self.tag}

    routed = RoutedSource({Binding.MCP: _T("mcp"), Binding.REST: _T("rest")})
    assert routed.fetch(Binding.MCP, "get_incident", {}) == {"via": "mcp"}
    assert routed.fetch(Binding.REST, "instant_query", {}) == {"via": "rest"}
    assert calls == {"mcp": ["get_incident"], "rest": ["instant_query"]}


def test_routed_source_unwired_binding_raises():
    routed = RoutedSource({Binding.MCP: MockSource()})
    with pytest.raises(LookupError):
        routed.fetch(Binding.A2A, "ocp__restart", {})


# ── layer.serve: resolve -> gate -> fetch -> normalize (gate-FIRST) ───────────────
_PROM_RAW = {
    "service": {"name": "payments-api", "env": "prod"},
    "metrics": [{"predicate": "red_errors", "value": 0.4, "at": "2026-07-19T14:00:00Z"}],
}


def test_serve_fetches_via_source_then_normalizes():
    layer = CapabilityLayer([PrometheusAdapter()],
                            source=MockSource({"fetch_metrics": _PROM_RAW}))
    ops, inv = layer.serve(CapabilityCall(intent="fetch_metrics"), allow_write=False)
    assert inv.provider == "prometheus" and not inv.blocked
    assert inv.op_count == len(ops) and ops   # the mock's raw folded to real ops


def test_serve_is_gate_first_write_blocked_before_any_fetch():
    fetched: list[str] = []

    class _Src:
        def fetch(self, binding, intent, params):
            fetched.append(intent)
            return {}

    class _WriteAdapter:
        provider = "ocp"
        intents = frozenset({"ocp__restart"})
        effect = Effect.WRITE
        binding = Binding.A2A

        def normalize(self, raw):
            raise AssertionError("a blocked write must never reach normalize()")

    layer = CapabilityLayer([_WriteAdapter()], source=_Src())
    ops, inv = layer.serve(CapabilityCall(intent="ocp__restart"), allow_write=False)

    assert ops == [] and inv.blocked and "write blocked" in inv.reason
    assert fetched == []   # the transport was never touched — gate precedes the side-effect


def test_serve_unknown_intent_blocks_without_touching_source():
    fetched: list[str] = []

    class _Src:
        def fetch(self, binding, intent, params):
            fetched.append(intent)
            return {}

    layer = CapabilityLayer([PrometheusAdapter()], source=_Src())
    ops, inv = layer.serve(CapabilityCall(intent="nope"), allow_write=False)
    assert ops == [] and inv.blocked and "no capability" in inv.reason
    assert fetched == []


def test_adapter_bindings_are_declared_data():
    from iw_engine.capability.adapters import default_adapters
    from iw_engine.capability.adapters.ocp import OcpRestartAdapter

    by_provider = {a.provider: a.binding for a in default_adapters()}
    assert by_provider["prometheus"] is Binding.REST
    assert by_provider["git"] is Binding.REST
    for mcp_provider in ("splunk", "appd", "servicenow", "cmdb", "ocp", "artifactory"):
        assert by_provider[mcp_provider] is Binding.MCP
    assert OcpRestartAdapter().binding is Binding.A2A   # reserved write-side binding
