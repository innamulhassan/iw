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
    ProviderRoutedSource,
    RestSource,
    RoutedSource,
    build_provider_transports,
    provider_config,
)
from iw_engine.capability.adapters.prometheus import PrometheusAdapter
from iw_engine.capability.sources import _mcp_result, _parse_sse_or_json
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


# ── transport fidelity: MCP result parsing tolerates real-world shapes ─────────────
def test_mcp_result_parses_sse_streamable_http_frame():
    """A Streamable-HTTP MCP server returns the JSON-RPC message inside an SSE frame; the LAST
    data: payload (after any progress frames) is the result."""
    frame = ('event: message\n'
             'data: {"jsonrpc":"2.0","id":1,"result":{"structuredContent":{"ok":true}}}\n\n')
    assert _mcp_result(frame) == {"ok": True}


def test_mcp_result_tolerates_non_dict_body_without_crashing():
    # a bare string / list / None must degrade to {}, never AttributeError
    assert _mcp_result("not json at all") == {}
    assert _mcp_result(None) == {}
    assert _mcp_result([1, 2, 3]) == {}
    # a reply whose result isn't a dict degrades to {}
    assert _mcp_result({"result": "oops"}) == {}


def test_mcp_result_still_raises_on_vendor_error():
    # a genuine tool error is still surfaced (serve() turns it into an `error` Invocation)
    with pytest.raises(RuntimeError):
        _mcp_result({"result": {"isError": True, "content": [{"type": "text", "text": "boom"}]}})
    with pytest.raises(RuntimeError):
        _mcp_result({"error": {"code": -32601, "message": "no method"}})


def test_mcp_source_does_not_crash_on_sse_body():
    """End-to-end: an SSE-returning transport flows through McpSource without raising."""
    def sse_http(endpoint, payload, headers):
        return 'data: {"result":{"structuredContent":{"alerts":[{"id":"A1"}]}}}\n\n'

    src = McpSource("https://mcp.example/rpc", transport=sse_http)
    assert src.fetch(Binding.MCP, "active_alerts", {}) == {"alerts": [{"id": "A1"}]}


def test_parse_sse_or_json_plain_json_and_undecodable():
    assert _parse_sse_or_json('{"a": 1}') == {"a": 1}
    assert _parse_sse_or_json(b'{"a": 1}') == {"a": 1}
    assert _parse_sse_or_json("garbage") == {}   # never raises


# ── config surface: per-provider endpoint + token from the environment ─────────────
def test_provider_config_reads_env_by_convention():
    env = {"IW_CAP_SERVICENOW_URL": "https://snow.example/mcp",
           "IW_CAP_SERVICENOW_TOKEN": "snow-tok"}
    assert provider_config("servicenow", env) == ("https://snow.example/mcp", "snow-tok")
    # unconfigured provider -> (None, None), no crash
    assert provider_config("splunk", env) == (None, None)


def test_build_provider_transports_wires_only_configured_providers():
    bindings = {"servicenow": Binding.MCP, "prometheus": Binding.REST, "splunk": Binding.MCP}
    env = {"IW_CAP_SERVICENOW_URL": "https://snow.example/mcp", "IW_CAP_SERVICENOW_TOKEN": "t1",
           "IW_CAP_PROMETHEUS_URL": "https://prom.example"}
    transports = build_provider_transports(
        bindings, rest_routes={"prometheus": {"instant_query": "/api/v1/query"}}, env=env,
        http_mcp=lambda *a: {}, http_rest=lambda *a: {})
    # only providers with a configured URL are wired; splunk (no URL) is omitted
    assert set(transports) == {"servicenow", "prometheus"}
    assert isinstance(transports["servicenow"], McpSource)
    assert transports["servicenow"].token == "t1"
    assert isinstance(transports["prometheus"], RestSource)
    assert transports["prometheus"].routes == {"instant_query": "/api/v1/query"}


# ── M21: the two export surfaces AGREE on what the live seam is ─────────────────────
def test_layer_and_package_agree_the_live_router_is_provider_routed():
    """`capability.layer` and `capability/__init__` must name the SAME live router. Before M21
    layer.__all__ re-exported only RoutedSource (arity-3, the demoted back-compat one) while the
    package exported ProviderRoutedSource — a surface disagreement about the live seam. Both now
    export the SAME ProviderRoutedSource object, and RoutedSource stays reachable as back-compat."""
    from iw_engine import capability as pkg
    from iw_engine.capability import layer as layer_mod
    assert pkg.ProviderRoutedSource is layer_mod.ProviderRoutedSource
    assert "ProviderRoutedSource" in layer_mod.__all__ and "ProviderRoutedSource" in pkg.__all__
    # RoutedSource remains for single-endpoint back-compat, from both surfaces
    assert pkg.RoutedSource is layer_mod.RoutedSource


# ── ProviderRoutedSource: route by PROVIDER (arity 9), not Binding (arity 3) ────────
def test_provider_routed_source_dispatches_on_provider():
    calls: dict[str, list] = {"servicenow": [], "splunk": []}

    class _T:
        def __init__(self, tag):
            self.tag = tag

        def fetch(self, binding, intent, params):
            calls[self.tag].append(intent)
            return {"via": self.tag}

    intent_provider = {"get_incident": "servicenow", "search_errors": "splunk"}
    routed = ProviderRoutedSource(intent_provider,
                                  {"servicenow": _T("servicenow"), "splunk": _T("splunk")})
    # two MCP providers get their OWN transport — no shared endpoint (the arity-9 fix)
    assert routed.fetch(Binding.MCP, "get_incident", {}) == {"via": "servicenow"}
    assert routed.fetch(Binding.MCP, "search_errors", {}) == {"via": "splunk"}
    assert calls == {"servicenow": ["get_incident"], "splunk": ["search_errors"]}


def test_provider_routed_source_unwired_provider_is_clean_empty():
    """An intent whose provider has no wired transport returns {} (clean-empty, the adapter
    folds to zero ops) — the honest 'that tool isn't connected', never a crash."""
    routed = ProviderRoutedSource({"get_incident": "servicenow"}, {})
    assert routed.fetch(Binding.MCP, "get_incident", {}) == {}
    assert routed.fetch(Binding.MCP, "unknown_intent", {}) == {}


# ── build_live_layer: the one-line LIVE composition seam (M20) ──────────────────────
def test_build_live_layer_composes_and_runs_clean_empty_with_no_env():
    """The live factory COMPOSES + RUNS end-to-end with NO vendor configured: every provider is
    unwired, so every read routes clean-empty exactly like the mock layer with an unfixtured intent
    (the mock-equivalent prod seam the 'two swaps are one-seam' bar wants exercised)."""
    from iw_engine.runtime.scenarios import build_live_layer
    layer = build_live_layer(env={})
    ops, inv = layer.serve(CapabilityCall(intent="fetch_metrics"), allow_write=False)
    assert ops == [] and not inv.blocked and inv.outcome == "empty"   # clean-empty, NOT error
    assert inv.served_by == "mapping"   # the composing transport that served it (M1 provenance)


def test_build_live_layer_routes_a_configured_provider_through_its_transport():
    """A provider with an `IW_CAP_<P>_URL` set wires its transport; the factory routes that
    provider's intent to it, maps the vendor envelope, and folds real ops — all with injected HTTP,
    no live server (proves the composition is a real live seam, not just clean-empty degradation)."""
    from iw_engine.runtime.scenarios import build_live_layer

    def fake_rest(url, params, headers):   # a Prometheus /api/v1/query envelope
        return {"status": "success", "data": {"result": [
            {"metric": {"__name__": "red_errors"}, "value": [1689770000, "0.4"]}]}}

    layer = build_live_layer(
        env={"IW_CAP_PROMETHEUS_URL": "https://prom.example"},
        rest_routes={"prometheus": {"fetch_metrics": "/api/v1/query"}}, http_rest=fake_rest)
    ops, inv = layer.serve(
        CapabilityCall(intent="fetch_metrics", params={"service": "payments-api", "env": "prod"}),
        allow_write=False)
    assert inv.provider == "prometheus" and inv.served_by == "mapping"
    assert ops and inv.outcome == "data"   # the vendor envelope mapped + folded to real ops


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
    from iw_engine.capability.adapters.ocp import OcpAdapter

    by_provider = {a.provider: a.binding for a in default_adapters()}
    assert by_provider["prometheus"] is Binding.REST
    assert by_provider["git"] is Binding.REST
    for mcp_provider in ("splunk", "appd", "servicenow", "cmdb", "ocp", "artifactory"):
        assert by_provider[mcp_provider] is Binding.MCP
    # ocp__restart rides the SAME MCP-bound adapter as the ocp reads, write per-intent
    # (the split A2A placeholder adapter is retired — part4-capability §1)
    assert "ocp__restart" in OcpAdapter.intents
    assert OcpAdapter.effects["ocp__restart"] is Effect.WRITE
