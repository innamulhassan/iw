"""The fetch seam — the ONE side-effecting boundary (DESIGN §2.5 R-K1, VALIDATION-VERDICT §C).

Every live capability read is a single `fetch(binding, intent, params) -> raw` request; the
adapter's pure `normalize(raw) -> Operation[]` is identical no matter which transport served
the raw. Three transports share the `Source` signature so the fold never forks:

  - `MockSource`  — fixtures; the hermetic test transport (zero network). THIS is the one thing
    that swaps to go live; adapters + engine are unchanged either way.
  - `McpSource`   — one generic MCP `tools/call` client. A new MCP vendor is a config line (its
    intents added to an MCP-bound adapter), not new code.
  - `RestSource`  — a thin raw-REST shim for the tools without a first-party MCP server
    (Prometheus, local git). Two REST clients total, not eight.

`RoutedSource` composes the per-binding transports behind the same seam for a live, mixed-tool
layer; the hermetic suite just uses `MockSource`, which answers every binding from a fixture.

The HTTP is injected (`transport=`) so `McpSource`/`RestSource` are unit-testable against a fake
— no live server is ever required. The stdlib urllib defaults are used only on a real run.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Protocol, runtime_checkable

from ..domain.enums import Binding


@runtime_checkable
class Source(Protocol):
    """The transport contract: turn a (binding, intent, params) request into the tool's raw
    JSON. `binding` is carried so a composing transport can route; a single concrete transport
    (Mock/Mcp/Rest) simply ignores it."""

    def fetch(self, binding: Binding, intent: str, params: dict) -> dict: ...


class MockSource:
    """Fixture-backed transport — the hermetic test seam. Returns the canned raw tool output
    per intent regardless of binding (a fixture stands in for whatever transport the live tool
    would use), so the whole suite runs with zero credentials/network."""

    def __init__(self, fixtures: dict[str, dict | list] | None = None) -> None:
        self._fx = fixtures or {}

    def fetch(self, binding: Binding, intent: str, params: dict) -> dict:
        raw = self._fx.get(intent, {})
        return raw if isinstance(raw, dict) else {"records": raw}


class McpSource:
    """One generic MCP transport for every MCP-bound tool (VALIDATION-VERDICT §C.2). A live
    fetch is a single JSON-RPC `tools/call(name=intent, arguments=params)`; the tool's vendor
    JSON is handed to `normalize()` verbatim (MCP returns vendor JSON, NOT the closed ops — it
    must not collapse into `query`). The HTTP client is injected so the shape is testable
    against a fake with no live MCP server."""

    def __init__(self, endpoint: str, *, transport=None, token: str | None = None) -> None:
        self.endpoint = endpoint
        self._http = transport or _urllib_post   # (endpoint, payload, headers) -> reply dict
        self.token = token
        self._id = 0

    def fetch(self, binding: Binding, intent: str, params: dict) -> dict:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": "tools/call",
                   "params": {"name": intent, "arguments": params or {}}}
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return _mcp_result(self._http(self.endpoint, payload, headers))


class RestSource:
    """A thin raw-REST transport (VALIDATION-VERDICT §C.2) for the tools without a first-party
    MCP server. `routes` maps an intent to a relative path; `params` become the request's query
    / body and the JSON body is handed to `normalize()` unchanged. An unrouted intent yields an
    empty raw (the adapter folds it to zero ops). HTTP is injected for testable shape."""

    def __init__(self, base_url: str, routes: dict[str, str], *, transport=None,
                 token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.routes = dict(routes)   # intent -> relative path
        self._http = transport or _urllib_get   # (url, params, headers) -> body dict
        self.token = token

    def fetch(self, binding: Binding, intent: str, params: dict) -> dict:
        path = self.routes.get(intent)
        if path is None:
            return {}
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        body = self._http(self.base_url + path, params or {}, headers)
        return body if isinstance(body, dict) else {"records": body}


class RoutedSource:
    """Composes the per-binding transports behind the one fetch seam — dispatches each request
    to the transport wired for its adapter's `Binding`. This is how a LIVE layer runs mixed
    tools (McpSource for MCP, RestSource for REST, ...); the hermetic suite needs no routing
    because `MockSource` answers every binding from a fixture.

    NOTE — routing arity: `Binding` has arity 3 (MCP/REST/A2A), so this collapses all 7 MCP
    providers onto ONE endpoint+token. A real mixed-tool run needs `ProviderRoutedSource`
    (arity 9 — one transport per PROVIDER, part4-capability §3). This binding-router remains for
    the single-endpoint case and back-compat."""

    def __init__(self, transports: dict[Binding, Source]) -> None:
        self.transports = dict(transports)

    def fetch(self, binding: Binding, intent: str, params: dict) -> dict:
        transport = self.transports.get(binding)
        if transport is None:
            raise LookupError(f"no transport wired for binding {binding.value!r} (intent {intent!r})")
        return transport.fetch(binding, intent, params)


class ProviderRoutedSource:
    """Routes each fetch to the transport wired for the intent's PROVIDER, not its Binding
    (part4-capability §3: 'routing by provider, not binding — the routing axis has arity 9, not
    3'). Each of the 9 providers gets its OWN endpoint + credential, so `servicenow` and `splunk`
    no longer share one MCP endpoint+token.

    An intent whose provider has no wired transport returns `{}` — the HONEST 'that tool isn't
    connected for this incident' (the adapter folds it to zero ops → a clean-empty, never a
    crash), mirroring `ScenarioSource`'s unfixtured-provider behaviour."""

    def __init__(self, intent_provider: dict[str, str],
                 transports_by_provider: dict[str, Source]) -> None:
        self.intent_provider = dict(intent_provider)
        self.transports = dict(transports_by_provider)

    def fetch(self, binding: Binding, intent: str, params: dict) -> dict:
        provider = self.intent_provider.get(intent)
        transport = self.transports.get(provider) if provider else None
        if transport is None:
            return {}
        return transport.fetch(binding, intent, params)


# ── per-provider config surface (env-based, part4-capability §3) ───────────────────
def provider_config(provider: str, env: dict[str, str] | None = None) -> tuple[str | None, str | None]:
    """Read a provider's live endpoint + credential from the environment. The convention is
    `IW_CAP_<PROVIDER>_URL` / `IW_CAP_<PROVIDER>_TOKEN` (e.g. `IW_CAP_SERVICENOW_URL`) — a first
    real connection is a couple of env vars, not new factory code (today the only env vars are
    LLM-side). `env` is injectable for testing; it defaults to `os.environ`."""
    e = os.environ if env is None else env
    key = provider.upper()
    return e.get(f"IW_CAP_{key}_URL"), e.get(f"IW_CAP_{key}_TOKEN")


def build_provider_transports(
    bindings: dict[str, Binding],
    *,
    rest_routes: dict[str, dict[str, str]] | None = None,
    env: dict[str, str] | None = None,
    http_mcp=None,
    http_rest=None,
) -> dict[str, Source]:
    """Build a `provider -> Source` map from the per-provider env config. For each provider whose
    `IW_CAP_<PROVIDER>_URL` is set, wire an `McpSource` (MCP binding) or `RestSource` (REST
    binding) at that endpoint with its token. Providers without a configured URL are omitted —
    their intents route to `{}` (clean-empty) through `ProviderRoutedSource`.

    `bindings` is the provider->Binding map (from the layer's adapters); `rest_routes` supplies
    each REST provider's intent->path table. HTTP clients are injectable for hermetic tests."""
    routes = rest_routes or {}
    transports: dict[str, Source] = {}
    for provider, binding in bindings.items():
        url, token = provider_config(provider, env)
        if not url:
            continue
        if binding is Binding.REST:
            transports[provider] = RestSource(url, routes.get(provider, {}),
                                              transport=http_rest, token=token)
        elif binding is Binding.MCP:
            transports[provider] = McpSource(url, transport=http_mcp, token=token)
        # A2A (write delegation) has no live transport yet — left unwired (deferred, §3).
    return transports


class ScenarioSource:
    """The LIVE-run fixture transport (VALIDATION-VERDICT §A gap 4). Where `MockSource` keys a
    fixture by the EXACT intent, this resolves intent -> PROVIDER -> that provider's blob, so
    ANY valid intent for a provider returns the provider's data. That closes the two-vocabulary
    gap: a live model that reaches for a provider's data need only pick the right provider, not
    guess the one wired intent name — every read intent of a fixtured provider is 'connected'.

    Fixtures are `provider -> {phase | "*": raw_blob}` — phase-scoped because tools return the
    CURRENT state of the world: a provider may override its blob in a later phase (e.g. the
    post-remediation recovery metrics a `verify`-phase metric read should return). The driver
    sets `.phase` before each step; a provider with no phase-specific blob falls back to "*".

    `intent_provider` is the intent->provider map (built from the layer's adapters); an intent
    with no fixtured provider returns `{}` (the adapter folds it to zero ops — an honest 'that
    tool isn't wired for this incident')."""

    def __init__(self, intent_provider: dict[str, str],
                 fixtures: dict[str, dict[str, dict | list]]) -> None:
        self.intent_provider = dict(intent_provider)
        self.fixtures = fixtures
        self.phase: str = "*"

    def fetch(self, binding: Binding, intent: str, params: dict) -> dict:
        provider = self.intent_provider.get(intent)
        table = self.fixtures.get(provider) if provider else None
        if not table:
            return {}
        blob = table.get(self.phase, table.get("*", {}))
        return dict(blob) if isinstance(blob, dict) else {"records": blob}


# ── MCP envelope + stdlib HTTP defaults (exercised only on a live run) ─────────────
def _mcp_result(reply) -> dict:
    """Extract the tool's vendor JSON from an MCP `tools/call` reply. Prefers structured
    content; falls back to a single JSON text block. Raises `RuntimeError` on a JSON-RPC or
    tool error (which `CapabilityLayer.serve` catches → an `error` Invocation).

    TRANSPORT-FIDELITY (part4-capability §3): tolerant of the shapes a real MCP endpoint emits —
    a raw JSON string, an SSE / Streamable-HTTP text frame (`event: message\\ndata: {...}`), or a
    non-dict body — none of which may blow up with an AttributeError. Only a genuine vendor error
    is raised; a merely-unrecognised shape degrades to `{}` (clean-empty)."""
    if isinstance(reply, (str, bytes)):
        reply = _parse_sse_or_json(reply)
    if not isinstance(reply, dict):
        return {}
    if reply.get("error"):
        raise RuntimeError(f"MCP JSON-RPC error: {reply['error']}")
    result = reply.get("result")
    if not isinstance(result, dict):
        return result if isinstance(result, dict) else {}
    if result.get("isError"):
        raise RuntimeError(f"MCP tool error: {result.get('content')}")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    content = result.get("content")
    for item in content if isinstance(content, list) else []:
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                return json.loads(item["text"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return result


def _parse_sse_or_json(body) -> dict:
    """Decode a raw body into a dict. Handles a plain JSON string and an SSE / Streamable-HTTP
    frame (`event: message` + one or more `data:` lines carrying the JSON-RPC message). The LAST
    parseable `data:` payload wins (SSE may stream progress frames before the result). Never
    raises: an undecodable body degrades to `{}`."""
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else str(body)
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    found: dict = {}
    for line in text.splitlines():
        if line.startswith("data:"):
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                parsed = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                found = parsed
    return found


def _urllib_post(endpoint: str, payload: dict, headers: dict) -> dict:
    data = json.dumps(payload).encode()
    # Accept both plain JSON and the SSE stream a Streamable-HTTP MCP server may return.
    headers = {"Accept": "application/json, text/event-stream", **headers}
    req = urllib.request.Request(endpoint, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return _parse_sse_or_json(raw)


def _urllib_get(url: str, params: dict, headers: dict) -> dict:
    import urllib.parse
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)
