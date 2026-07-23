"""The semantic dictionary (DOMAIN-v3 §2.3) — canonical names as shared-core registry data.

ONE dictionary keyed by canonical name (facts + events). It is the single NAME authority the
reducer canonicalizes + validates against: `resolve(source, name, unit)` maps an emitted native
name to its canonical (merges) or splits it by unit (1->N), and `applies_to_ok(name, type)`
replaces the per-type `fact_predicates`/`event_allowed` membership lists (those become a derived
view — the fact lists here, the event lists derived from the registry's `event_types`).

Owner ruling (PROGRAM-PLAN f1d24d6a): shared core registry, no folder registries — so this ships
as a module dict, not per-domain YAML. The vendor's own spelling survives on every assertion's
`source_native_name`; this table is what it is translated *to*.

Resolution has three mechanically-distinct classes (see name-assignment-table.md §0):
  1. identity   — the emitted name IS canonical (`degraded`) -> returned as-is.
  2. merge      — N native spellings -> 1 canonical (`red_errors -> error_rate`), source-stable so
                  the reverse index is name-keyed (the design's per-source alias column collapses).
  3. split      — 1 native -> N canonicals by CONTEXT; keyed on `(name, unit)` because the census
                  shows the quantity-kind rides on the unit (`slo_target` ms=latency vs s=freshness).

`no_evidence:<intent>` is the reserved engine namespace — dictionary-exempt (the reducer's
NoEvidence path never routes through here).

`x.<source>.<native>` is the AIRLOCK namespace (P3, DOMAIN-v3 §2.4 row 1): a name that resolves
to nothing is NOT rejected-and-erased — the reducer lands it under this quarantine spelling,
flagged provisional and counted toward promotion. Promotion stays a human core-registry edit
(add the DictEntry/alias here); the quarantine prefix guarantees no collision with any canonical
and no spoofing of the `no_evidence:` engine channel.
"""
from __future__ import annotations

from dataclasses import dataclass

from .enums import NodeType, Source, Species, Stat  # Stat: entry metadata + shape checks
from .nodes import NODE_SPECS

_NT = NodeType


@dataclass(frozen=True)
class DictEntry:
    """One canonical name. `value_type`/`unit`/`direction_of_bad`/`stat` are declarative metadata
    (the catalog renders them; P2 does NOT hard-enforce unit/species shape — that shape-quarantine
    is the P3 airlock, so a twin's None/omitted unit never drops a fact). `applies_to` IS enforced
    (it replaces the per-type membership list)."""

    name: str
    species: Species
    applies_to: tuple[NodeType, ...]
    value_type: str = "float"                 # float|int|bool|str|dict|content
    unit: str | None = None                   # the informative unit; None is always compatible
    direction_of_bad: str | None = None       # up|down|None
    aliases: tuple[str, ...] = ()             # native spellings that merge to this canonical
    stat: Stat | None = None                  # reading statistic (metadata)


# ── fact-predicate canonicals (from name-assignment-table.md §3) ──────────────
_FACT_ENTRIES: tuple[DictEntry, ...] = (
    # RED / exit-call telemetry (merge + split targets)
    DictEntry("error_rate", Species.READING, (_NT.SERVICE, _NT.API_ENDPOINT, _NT.EXTERNAL_SERVICE),
              "float", "ratio", "up",
              aliases=("red_errors", "5xx_rate", "upstream_5xx_rate", "edge_5xx_rate",
                       "origin_5xx_rate", "call_error_rate"), stat=Stat.RATIO),
    DictEntry("latency_p99", Species.READING, (_NT.SERVICE, _NT.API_ENDPOINT, _NT.EXTERNAL_SERVICE),
              "int", "ms", "up",
              aliases=("red_latency_p99", "p99_latency", "origin_latency_p99", "call_latency_p99"),
              stat=Stat.PERCENTILE),
    DictEntry("latency_p50", Species.READING, (_NT.SERVICE,), "int", "ms", "up",
              aliases=("red_latency_p50",), stat=Stat.PERCENTILE),
    DictEntry("request_rate", Species.READING, (_NT.SERVICE, _NT.API_ENDPOINT, _NT.EXTERNAL_SERVICE),
              "int", "rpm", None, aliases=("call_rate",), stat=Stat.RATE),
    DictEntry("request_rate_x_baseline", Species.READING, (_NT.SERVICE,), "float", "x_baseline", "up",
              stat=Stat.DELTA_VS_BASELINE),
    DictEntry("availability", Species.READING, (_NT.EXTERNAL_SERVICE,), "float", "ratio", "down",
              stat=Stat.RATIO),
    DictEntry("degraded", Species.STATE, (_NT.SERVICE,), "bool", None, "up"),
    # BusinessTransaction (AppD BT)
    DictEntry("art_p95", Species.READING, (_NT.BUSINESS_TRANSACTION,), "int", "ms", "up",
              stat=Stat.PERCENTILE),
    DictEntry("calls_per_min", Species.READING, (_NT.BUSINESS_TRANSACTION,), "int", "calls_per_min",
              None, stat=Stat.RATE),
    DictEntry("errors_per_min", Species.READING, (_NT.BUSINESS_TRANSACTION,), "int", "errors_per_min",
              "up", stat=Stat.RATE),
    DictEntry("delta_vs_baseline", Species.READING, (_NT.BUSINESS_TRANSACTION,), "float", None, "up",
              stat=Stat.DELTA_VS_BASELINE),
    # SPAN canonicals (2026-07-23 primitives §2.6) — bounded happenings a subject PARTICIPATES in,
    # `[started_at, ended_at)` with an outcome, two-phase-then-frozen. `trace` is a distributed
    # trace captured on a BT/Service (the Rung-2 reification candidate keyed by correlation_id=trace_id);
    # `hop` is the Rung-1 atomic A->B call span addressed to the discovered CALLS EDGE (applies_to
    # names the legal node scope for a node-subject span; the edge-borne case is governed by
    # CALLS.fact_predicates). value carries the outcome (e.g. {"status": "ok"|"error"}).
    DictEntry("trace", Species.SPAN, (_NT.BUSINESS_TRANSACTION, _NT.SERVICE), "dict", None, None),
    DictEntry("hop", Species.SPAN, (_NT.SERVICE, _NT.API_ENDPOINT), "dict", None, None),
    # USE — data & messaging
    DictEntry("conn_pool_util", Species.READING, (_NT.DATABASE,), "float", "ratio", "up",
              stat=Stat.GAUGE),
    DictEntry("active_connections", Species.READING, (_NT.DATABASE,), "int", "conn", "up",
              stat=Stat.GAUGE),
    DictEntry("max_connections", Species.STATE, (_NT.DATABASE,), "int", "conn", None),
    DictEntry("replication_lag", Species.READING, (_NT.DATABASE,), "float", "s", "up", stat=Stat.GAUGE),
    DictEntry("slow_query_rate", Species.READING, (_NT.DATABASE,), "int", "per_min", "up",
              stat=Stat.RATE),
    DictEntry("index_health", Species.PROPERTY, (_NT.SCHEMA,), "float", None, "down"),
    DictEntry("table_count", Species.PROPERTY, (_NT.SCHEMA,), "int", None, None),
    DictEntry("consumer_lag", Species.READING, (_NT.MESSAGE_QUEUE,), "int", "msgs", "up",
              stat=Stat.GAUGE),
    DictEntry("dlq_depth", Species.READING, (_NT.MESSAGE_QUEUE,), "int", "msgs", "up", stat=Stat.GAUGE),
    DictEntry("throughput", Species.READING, (_NT.MESSAGE_QUEUE,), "int", "msgs_per_min", "down",
              stat=Stat.RATE),
    DictEntry("hit_rate", Species.READING, (_NT.CACHE,), "float", "ratio", "down", stat=Stat.RATIO),
    DictEntry("eviction_rate", Species.READING, (_NT.CACHE,), "int", "per_min", "up", stat=Stat.RATE),
    # USE — host / pod
    DictEntry("cpu_utilization", Species.READING, (_NT.HOST, _NT.POD), "float", "ratio", "up",
              stat=Stat.GAUGE),
    DictEntry("mem_utilization", Species.READING, (_NT.HOST, _NT.POD, _NT.CACHE), "float", "ratio",
              "up", stat=Stat.GAUGE),
    DictEntry("disk_utilization", Species.READING, (_NT.HOST,), "float", "ratio", "up", stat=Stat.GAUGE),
    DictEntry("net_utilization", Species.READING, (_NT.HOST,), "float", "ratio", "up", stat=Stat.GAUGE),
    DictEntry("cpu_saturation", Species.READING, (_NT.HOST,), "float", None, "up", stat=Stat.GAUGE),
    DictEntry("disk_saturation", Species.READING, (_NT.HOST,), "float", None, "up", stat=Stat.GAUGE),
    DictEntry("restart_count", Species.READING, (_NT.POD,), "int", None, "up", stat=Stat.COUNTER),
    DictEntry("phase", Species.STATE, (_NT.POD,), "str", None, None),
    DictEntry("ready", Species.STATE, (_NT.POD,), "bool", None, "down"),
    DictEntry("node_name", Species.PROPERTY, (_NT.POD,), "str", None, None),
    # network segment
    DictEntry("packet_loss", Species.READING, (_NT.NETWORK_SEGMENT,), "float", "ratio", "up",
              stat=Stat.RATIO),
    DictEntry("retrans_segs", Species.READING, (_NT.NETWORK_SEGMENT,), "int", "count", "up",
              stat=Stat.COUNT),
    DictEntry("probe_success", Species.READING, (_NT.NETWORK_SEGMENT,), "float", "ratio", "down",
              stat=Stat.RATIO),
    # deployment / batch / firewall / cert / flag
    DictEntry("image", Species.STATE, (_NT.DEPLOYMENT,), "str", None, None),
    DictEntry("available_replicas", Species.STATE, (_NT.DEPLOYMENT,), "int", None, "down"),
    DictEntry("desired_replicas", Species.STATE, (_NT.DEPLOYMENT,), "int", None, None),
    DictEntry("rollout_progress", Species.STATE, (_NT.DEPLOYMENT,), "int", None, "down"),
    DictEntry("last_duration", Species.PROPERTY, (_NT.BATCH_JOB,), "int", "s", "up"),
    DictEntry("backlog_size", Species.READING, (_NT.BATCH_JOB,), "int", "rows", "up", stat=Stat.GAUGE),
    DictEntry("deny_count", Species.READING, (_NT.FIREWALL_RULE,), "int", None, "up", stat=Stat.COUNT),
    DictEntry("days_to_expiry", Species.STATE, (_NT.CERTIFICATE,), "int", "days", "down"),
    DictEntry("enabled", Species.STATE, (_NT.FEATURE_FLAG,), "bool", None, None),
    DictEntry("rollout_percentage", Species.STATE, (_NT.FEATURE_FLAG,), "int", None, None),
    # SLO targets (1->N split of slo_target, by unit)
    DictEntry("slo_availability_target", Species.STATE, (_NT.SERVICE,), "float", "ratio", None),
    DictEntry("slo_latency_target", Species.STATE, (_NT.SERVICE,), "int", "ms", None),
    DictEntry("slo_freshness_target", Species.STATE, (_NT.SERVICE,), "int", "s", None),
    # signals / classification
    DictEntry("tier", Species.STATE, (_NT.SERVICE,), "str", None, None),
    DictEntry("severity_score", Species.STATE, (_NT.ANOMALY,), "int", None, "up"),
    DictEntry("onset_value", Species.STATE, (_NT.ANOMALY,), "float", None, "up"),  # P7 -> onset_<quantity>
    DictEntry("count", Species.READING, (_NT.ERROR_SIGNATURE,), "int", None, "up", stat=Stat.COUNT),
    DictEntry("last_seen", Species.PROPERTY, (_NT.ERROR_SIGNATURE,), "str", None, None),
    DictEntry("status_code_dist", Species.PROPERTY, (_NT.API_ENDPOINT,), "dict", None, None),
    # change content (git)
    DictEntry("files_changed", Species.PROPERTY, (_NT.CODE_COMMIT, _NT.CHANGE_EVENT), "int", None, None),
    DictEntry("lines_added", Species.PROPERTY, (_NT.CODE_COMMIT, _NT.CHANGE_EVENT), "int", None, None),
    DictEntry("lines_deleted", Species.PROPERTY, (_NT.CODE_COMMIT, _NT.CHANGE_EVENT), "int", None, None),
    DictEntry("diff_summary", Species.PROPERTY, (_NT.CODE_COMMIT, _NT.CHANGE_EVENT), "content", None, None),
    DictEntry("blame_line", Species.PROPERTY, (_NT.CODE_COMMIT,), "content", None, None),
)


# ── event canonicals — a DERIVED VIEW of the registry's per-type event_types ──
# Build-spec step 3: "the per-type spec lists become a derived view". Event E applies to type T
# iff `E in node_spec(T).event_types`, so this reproduces `registry.event_allowed` exactly (no
# golden event is dropped). SINGLE SOURCE: every event's applies-to is DATA on the NodeSpec —
# `trace_captured` lives in the BUSINESS_TRANSACTION + SERVICE `event_types` (M24 retired the
# `_EVENT_EXTRA_APPLIES` engine-code special-case that patched it there, exactly the per-scenario
# drift a derived view exists to eliminate).


def _event_entries() -> dict[str, DictEntry]:
    apply: dict[str, set[NodeType]] = {}
    for ntype, spec in NODE_SPECS.items():
        for et in spec.event_types:
            apply.setdefault(et, set()).add(ntype)
    return {name: DictEntry(name=name, species=Species.EVENT,
                            applies_to=tuple(sorted(types, key=lambda t: t.value)),
                            value_type="dict")
            for name, types in apply.items()}


DICTIONARY: dict[str, DictEntry] = {e.name: e for e in _FACT_ENTRIES}
for _name, _entry in _event_entries().items():
    DICTIONARY.setdefault(_name, _entry)   # fact names win any (nonexistent) collision


# ── merges: native spelling -> canonical (source-stable, name-keyed) ──────────
_MERGE_ALIASES: dict[str, str] = {a: e.name for e in _FACT_ENTRIES for a in e.aliases}

# ── splits: (name, unit) -> canonical (1->N; the reducer carries op.unit) ─────
_SPLIT_BY_UNIT: dict[str, dict[str | None, str]] = {
    "slo_target": {None: "slo_availability_target", "ratio": "slo_availability_target",
                   "%": "slo_availability_target", "pct": "slo_availability_target",
                   "ms": "slo_latency_target", "s": "slo_freshness_target"},
    "red_rate": {None: "request_rate", "rpm": "request_rate",
                 "x_baseline": "request_rate_x_baseline"},
    "epm": {None: "errors_per_min", "errors_per_min": "errors_per_min", "epm": "errors_per_min",
            "calls_per_min": "calls_per_min", "cpm": "calls_per_min"},
}
# an unmapped unit falls back to the default (never reject a split in P2 — the shape-quarantine
# that would reject an off-enum unit is the P3 airlock; the names-only gate is paramount here).
_SPLIT_DEFAULT: dict[str, str] = {
    "slo_target": "slo_availability_target",
    "red_rate": "request_rate",
    "epm": "errors_per_min",
}


QUARANTINE_PREFIX = "x."


def quarantine_name(source: Source, native: str) -> str:
    """The airlock spelling for an unknown name (P3, DOMAIN-v3 §2.4 row 1): `x.<source>.<native>`.
    Deterministic (same source+native → same spelling, so repeats COUNT toward promotion) and
    collision-free: no canonical starts with `x.` and the engine's reserved `no_evidence:` prefix
    cannot be spoofed through it."""
    return f"{QUARANTINE_PREFIX}{source.value}.{native}"


def is_quarantined(name: str) -> bool:
    """Whether a stored name is airlock-quarantined (provisional vocabulary, not yet promoted)."""
    return name.startswith(QUARANTINE_PREFIX)


def resolve(source: Source | None, name: str, unit: str | None = None) -> str | None:
    """The canonical name for an emitted `(source, name, unit)`, or None if `name` is unknown
    (neither a canonical, a merge alias, nor a split input). `source` is accepted for fidelity to
    the build-spec's `(provider, native_name)` index; native spellings are source-stable in this
    codebase so the lookup is name-keyed. `unit` drives the 1->N splits."""
    if name in _SPLIT_BY_UNIT:
        return _SPLIT_BY_UNIT[name].get(unit, _SPLIT_DEFAULT[name])
    if name in DICTIONARY:
        return name
    return _MERGE_ALIASES.get(name)


def shape_violation(canonical: str, *, unit: str | None, stat: Stat | None,
                    species: Species | None, has_window: bool) -> str | None:
    """P3 SHAPE QUARANTINE (DOMAIN-v3 §9.1 — the airlock's second lane): why a KNOWN name
    arrived with an invalid shape, or None when the shape is acceptable. The reducer lands a
    violating assertion PROVISIONAL with a journaled rejection notice — never silently accepted,
    never erased.

    Checked (cannot fire on a shim-defaulted emission):
      - unit mismatch — both the op and the DictEntry declare a unit and they differ (None on
        either side is always compatible: "a twin's None/omitted unit never drops a fact");
      - a claimed READING without its mandatory stat+window.
    NOT checked yet: stat mismatch — the P1a/P1b adapters stamp `stat=gauge` as a compat default
    on metrics whose entries declare rate/ratio/percentile (their fixtures state no stat), so a
    mismatch lane would quarantine the scripted happy path. It becomes checkable when adapters
    emit true stats."""
    e = DICTIONARY.get(canonical)
    if e is None or e.species is Species.EVENT:
        return None                    # unknown → name quarantine; events carry no reading shape
    if unit is not None and e.unit is not None and unit != e.unit:
        return f"unit mismatch: got '{unit}', dictionary declares '{e.unit}'"
    if species is Species.READING and (stat is None or not has_window):
        return "a reading requires both stat and window"
    return None


def applies_to_ok(canonical: str, ntype: NodeType) -> bool:
    """Whether `canonical` is legal on `ntype` — the dictionary's `applies_to` replacing the
    per-type `fact_predicates`/`event_allowed` membership check (build-spec step 3)."""
    e = DICTIONARY.get(canonical)
    return e is not None and ntype in e.applies_to


def fact_names_for(ntype: NodeType) -> tuple[str, ...]:
    """The canonical fact/reading/state/property names legal on a type — a DERIVED view of
    `applies_to` (replaces `NodeSpec.fact_predicates` for the LLM catalog, build-spec step 5)."""
    return tuple(sorted(e.name for e in DICTIONARY.values()
                        if e.species is not Species.EVENT and ntype in e.applies_to))
