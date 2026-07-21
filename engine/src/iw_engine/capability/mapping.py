"""mapping.py — vendor JSON -> adapter-shape translation (the live-tools seam).

The adapters' `normalize(raw)` expect a fixed shape (the de-facto schema documented
by `runtime/live_fixtures.py`). Fixtures (`MockSource`/`ScenarioSource`) ARE that
shape, so the hermetic test net is untouched. But REAL tool responses — a
Prometheus `/api/v1/query` envelope, a ServiceNow `result[]` array, an MCP server's
vendor JSON — don't match. This module maps them, so live MCP/REST tools work
without changing any adapter.

Design:
- `map_response(provider, intent, vendor_raw) -> dict` — a pure dispatch over a
  registry keyed by `(provider, intent)` (falling back to provider-wide). Returns
  the adapter-expected shape. Unknown provider/intent -> pass-through (the vendor
  JSON is returned unchanged; if it already matches the adapter, it just works).
- `MappingSource` — a `Source` wrapper that maps the inner transport's output
  before returning. Fixtures bypass it (they're already adapter-shaped); wire it
  around `McpSource`/`RestSource`/`RoutedSource` for real tools.

Translators ship for the highest-impact providers (Prometheus, ServiceNow, Splunk,
Git). The rest are pass-through by default — the framework is in place; add a
translator per provider as you wire that tool live. Each translator is a pure
function; none touch the network.
"""
from __future__ import annotations

from collections.abc import Callable

from ..domain.enums import Binding
from .sources import Source

# ── the translator registry ────────────────────────────────────────────────────
# key: (provider, intent) -> translator. A provider-wide fallback ("*", "*") is
# tried before exact (provider, intent). Translators are pure: vendor_raw -> dict.
Translator = Callable[[dict], dict]
_TRANSLATORS: dict[tuple[str, str], Translator] = {}


def register(provider: str, intent: str = "*") -> Callable[[Translator], Translator]:
    """Decorator: register a vendor->adapter translator for (provider, intent).
    intent="*" makes it the provider-wide default for any unmapped intent."""
    def deco(fn: Translator) -> Translator:
        _TRANSLATORS[(provider, intent)] = fn
        return fn
    return deco


def map_response(provider: str, intent: str, vendor_raw: dict) -> dict:
    """Translate a vendor tool response into the adapter-expected shape.

    Looks up (provider, intent) exactly, then falls back to (provider, "*"). If
    neither is registered, returns vendor_raw unchanged (pass-through — correct
    when the vendor JSON already matches the adapter, e.g. a custom MCP server
    that emits the documented shape). Never raises: a translator that fails
    returns the input unchanged so a bad mapping can't crash a live run."""
    fn = _TRANSLATORS.get((provider, intent)) or _TRANSLATORS.get((provider, "*"))
    if fn is None:
        return vendor_raw
    try:
        return fn(vendor_raw)
    except Exception:
        # a broken translator must never crash a live investigation — fall back to
        # the raw vendor JSON (the adapter's presence-driven fold tolerates it).
        return vendor_raw


# ── MappingSource: a Source wrapper that translates before returning ───────────
class MappingSource:
    """Composes over any `Source` (McpSource/RestSource/RoutedSource): fetches from
    the inner transport, then maps the vendor JSON to the adapter shape via
    `map_response`. Fixtures don't need this — they're already adapter-shaped; use
    it only for real tool transports.

    The wrapper also remembers the intent->provider map (built from the layer's
    adapters) so it can route a fetch's intent to the right translator."""

    def __init__(self, inner: Source, intent_provider: dict[str, str]) -> None:
        self.inner = inner
        self.intent_provider = dict(intent_provider)
        self.phase: str = "*"   # passed through to ScenarioSource if inner is one

    def fetch(self, binding: Binding, intent: str, params: dict) -> dict:
        raw = self.inner.fetch(binding, intent, params)
        if not isinstance(raw, dict):
            return raw   # lists/scalars are the caller's contract; don't touch
        provider = self.intent_provider.get(intent, "")
        return map_response(provider, intent, raw)


# ── translators: Prometheus (REST) ─────────────────────────────────────────────
@register("prometheus", "instant_query")
@register("prometheus", "range_query")
@register("prometheus", "fetch_metrics")
def _prometheus_query(raw: dict) -> dict:
    """Prometheus /api/v1/query envelope -> adapter shape.

    Vendor: {"status":"success","data":{"resultType":"vector",
              "result":[{"metric":{"__name__":"red_errors","service":"payments-api"},
                          "value":[1689770000,"0.4"]}, ...]}}
    Adapter wants: {"service":{...}, "metrics":[{predicate,value,at,reliability,unit}]}
    The service block comes from the call params (params["service"]/["env"]) since a
    Prometheus metric carries only labels, not the identity the adapter needs."""
    result = (raw.get("data") or {}).get("result") or raw.get("result") or []
    metrics = []
    for sample in result:
        if isinstance(sample, dict) and "value" in sample:
            metric_labels = sample.get("metric") or {}
            value_pair = sample.get("value") or []
            # value is [unix_ts, "string_value"]; range queries use "values": [[ts,v],...]
            value_pairs = [value_pair] if value_pair else (sample.get("values") or [])
            for ts, val in value_pairs:
                metrics.append({
                    "predicate": metric_labels.get("__name__") or metric_labels.get("metric"),
                    "value": _coerce_num(val),
                    "at": _unix_to_iso(ts),
                    "reliability": 0.97,
                    "unit": metric_labels.get("unit"),
                })
        elif isinstance(sample, dict) and {"predicate", "value"} <= sample.keys():
            metrics.append(sample)   # already adapter-shaped
    return {"metrics": metrics}


@register("prometheus", "active_alerts")
def _prometheus_alerts(raw: dict) -> dict:
    """Prometheus /api/v1/alerts envelope -> adapter shape.
    Vendor: {"data":{"alerts":[{"labels":{"alertname":"HighErrorRate","alert_id":"ALT-1",
              "service":"payments-api"},"state":"firing","activeAt":"2026-..."}, ...]}}"""
    alerts = (raw.get("data") or {}).get("alerts") or raw.get("alerts") or []
    out = []
    for al in alerts:
        labels = al.get("labels") or al
        out.append({
            "id": labels.get("alert_id") or labels.get("id") or labels.get("alertname"),
            "alertname": labels.get("alertname"),
            "at": al.get("activeAt") or al.get("at"),
            "state": al.get("state", "firing"),
        })
    return {"alerts": out}


# ── translators: ServiceNow (MCP) ──────────────────────────────────────────────
@register("servicenow", "*")
def _servicenow(raw: dict) -> dict:
    """ServiceNow REST returns {"result": <obj>|[...]}. The adapter already uses native
    ServiceNow field names (number, opened_at, cmdb_ci.display_value, u_release_tag...),
    so this is mostly an envelope unwrap: pull `result` up to the top level when the
    vendor wrapped it. Already-adapter-shaped dicts (no "result" key) pass through."""
    if "result" in raw and isinstance(raw["result"], (dict, list)):
        r = raw["result"]
        # a single record wrapped: merge its fields up; a list: keep as the relevant key
        if isinstance(r, dict):
            # common ServiceNow single-table responses: the record's fields ARE the adapter input
            return {**r, **{k: v for k, v in raw.items() if k != "result"}}
        # list of records — the adapter reads named keys (changes/related_incidents/impacted);
        # a bare result list most likely maps to "changes" (the find_recent_changes intent)
        return {"changes": r}
    return raw


# ── translators: Splunk (MCP) ──────────────────────────────────────────────────
@register("splunk", "*")
def _splunk(raw: dict) -> dict:
    """Splunk search/jobs returns {"results":[...]} or {"rows":[...]} (the search head's
    JSON export). The adapter reads "errors" / "fw_denies" lists of records with
    signature_hash/_time/count fields. A bare result/rows list is treated as errors;
    a dict already carrying "errors"/"fw_denies" passes through."""
    if {"errors", "fw_denies"} & raw.keys():
        return raw   # already adapter-shaped
    rows = raw.get("results") or raw.get("rows") or raw.get("search")
    if isinstance(rows, list):
        return {"errors": rows}
    return raw


# ── translators: Git (REST) ────────────────────────────────────────────────────
@register("git", "*")
def _git(raw: dict) -> dict:
    """Most Git providers (GitHub/GitLab REST, or an MCP wrapper) already return
    fields close to the adapter shape (sha, commit, pr, diff, blame). The adapter
    reads optional top-level keys, so the common case is pass-through. Two small
    normalizations: a GitHub-style {"sha":..., "commit":{"author":...}} commit
    object gets its author pulled up; a list of commits under "commits" -> the first
    as "commit" (the adapter folds one commit at a time)."""
    if isinstance(raw.get("commit"), dict) and "author" not in raw["commit"] and "commit" in raw["commit"]:
        # GitHub: {sha, commit:{author:{date}, message, ...}} -> flatten author/date
        inner = raw["commit"]
        raw["commit"] = {**inner, **inner.get("commit", {})}
    commits = raw.get("commits")
    if isinstance(commits, list) and commits and "commit" not in raw:
        raw["commit"] = commits[0]
    return raw


# ── small coercion helpers ─────────────────────────────────────────────────────
def _coerce_num(val):
    """Prometheus values are strings ("0.4", "+Inf"); the adapter wants a number."""
    try:
        return float(val)
    except (TypeError, ValueError):
        return val


def _unix_to_iso(ts) -> str | None:
    """Prometheus timestamps are unix epoch floats; the adapter parses ISO strings."""
    if ts is None:
        return None
    try:
        from datetime import UTC, datetime
        return datetime.fromtimestamp(float(ts), tz=UTC).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError):
        return str(ts) if ts else None
