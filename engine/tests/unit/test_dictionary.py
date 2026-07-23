"""The dictionary is the single NAME authority (P2 build-spec step 2). Two guarantees:
every native name emitted by any adapter/scenario resolves to exactly ONE canonical entry, and
no canonical name is also an alias/split-input of another (closure). Plus the merge/split maps
point only at real canonicals, and every fact canonical is constrained (no empty applies_to).
"""
from __future__ import annotations

import pytest

from iw_engine.domain.dictionary import (
    _MERGE_ALIASES,
    _SPLIT_BY_UNIT,
    CATEGORY_ORDER,
    DICTIONARY,
    DictEntry,
    applies_to_ok,
    categories_for,
    fact_names_for,
    resolve,
)
from iw_engine.domain.enums import NodeType, Source, Species

# Every native fact name emitted across the 11 goldens + the live path (census sec1a/1b + the
# golden inventory). Merges keep their native spelling; splits carry the disambiguating unit.
_MERGE_NATIVES = [
    "red_errors", "red_latency_p99", "red_latency_p50", "call_rate",
    "5xx_rate", "call_error_rate", "p99_latency", "call_latency_p99",
]
_SPLIT_NATIVES = [
    ("slo_target", None, "slo_availability_target"),
    ("slo_target", "ratio", "slo_availability_target"),
    ("slo_target", "ms", "slo_latency_target"),
    ("slo_target", "s", "slo_freshness_target"),
    ("red_rate", "rpm", "request_rate"),
    ("red_rate", None, "request_rate"),
    ("red_rate", "x_baseline", "request_rate_x_baseline"),
    ("epm", "calls_per_min", "calls_per_min"),
    ("epm", None, "errors_per_min"),
]
# names already canonical (emitted verbatim) — resolve to themselves
_IDENTITY_NATIVES = [
    "degraded", "tier", "severity_score", "onset_value", "count", "last_seen", "status_code_dist",
    "conn_pool_util", "active_connections", "max_connections", "replication_lag", "slow_query_rate",
    "index_health", "table_count", "consumer_lag", "dlq_depth", "throughput", "hit_rate",
    "eviction_rate", "cpu_utilization", "mem_utilization", "disk_utilization", "net_utilization",
    "cpu_saturation", "disk_saturation", "restart_count", "phase", "ready", "node_name",
    "packet_loss", "retrans_segs", "probe_success", "image", "available_replicas", "desired_replicas",
    "rollout_progress", "last_duration", "backlog_size", "deny_count", "days_to_expiry", "enabled",
    "rollout_percentage", "availability", "art_p95", "delta_vs_baseline",
    "files_changed", "lines_added", "lines_deleted", "diff_summary", "blame_line",
]
# events emitted natively (already canonical — adapters map vendor reason -> etype)
_EVENT_NATIVES = [
    "fired", "declared", "implemented", "degraded_started", "degraded_cleared", "detected",
    "cleared", "connection_storm", "index_dropped", "expired", "renewed", "flipped", "mitigated",
    "started", "evicted", "restarted", "rollback", "released", "merged",
    "rollout_started", "rollout_complete", "built", "promoted", "OOMKilled", "trace_captured",
]


def test_every_native_resolves_to_exactly_one_canonical():
    for native in _MERGE_NATIVES + _IDENTITY_NATIVES + _EVENT_NATIVES:
        canon = resolve(Source.PROMETHEUS, native, None)
        assert canon is not None, f"{native!r} resolved to nothing"
        assert canon in DICTIONARY, f"{native!r} -> {canon!r} which is not a dictionary entry"


def test_splits_resolve_by_unit():
    for name, unit, expected in _SPLIT_NATIVES:
        canon = resolve(Source.SERVICENOW, name, unit)
        assert canon == expected, f"({name!r},{unit!r}) -> {canon!r}, expected {expected!r}"
        assert canon in DICTIONARY


def test_identity_names_resolve_to_themselves():
    for native in _IDENTITY_NATIVES + _EVENT_NATIVES:
        assert resolve(Source.PROMETHEUS, native, None) == native


def test_unknown_name_is_unresolved():
    # a genuinely unknown name resolves to None (the reducer AIRLOCKS it as a provisional
    # `x.<source>.<native>` assertion — P3 name quarantine, never a silent erase)
    assert resolve(Source.LLM, "totally_made_up_metric", None) is None


# ── closure: the maps are internally consistent ───────────────────────────────
def test_closure_no_canonical_is_an_alias_or_split_input():
    for name in DICTIONARY:
        assert name not in _MERGE_ALIASES, f"{name!r} is both a canonical and a merge alias"
        assert name not in _SPLIT_BY_UNIT, f"{name!r} is both a canonical and a split input"


def test_merge_and_split_targets_are_real_canonicals():
    for alias, canon in _MERGE_ALIASES.items():
        assert canon in DICTIONARY, f"merge {alias!r} -> {canon!r} missing from dictionary"
    for name, by_unit in _SPLIT_BY_UNIT.items():
        for unit, canon in by_unit.items():
            assert canon in DICTIONARY, f"split ({name!r},{unit!r}) -> {canon!r} missing"


def test_no_native_is_both_a_merge_alias_and_a_split_input():
    assert not (set(_MERGE_ALIASES) & set(_SPLIT_BY_UNIT))


def test_every_fact_canonical_is_constrained():
    # F7 (the empty-list inversion) dies: every fact/reading/state/descriptor canonical names the
    # types it applies to — no accidental "unconstrained" entry.
    for e in DICTIONARY.values():
        if e.species is not Species.EVENT:
            assert e.applies_to, f"{e.name!r} has empty applies_to"


def test_applies_to_ok_and_fact_names_for_agree():
    # the derived per-type view (catalog) agrees with the enforced applies_to check (reducer)
    for ntype in NodeType:
        for name in fact_names_for(ntype):
            assert applies_to_ok(name, ntype)


def test_error_rate_is_the_7to1_merge_target():
    for spelling in ("red_errors", "5xx_rate", "upstream_5xx_rate", "edge_5xx_rate",
                     "origin_5xx_rate", "call_error_rate"):
        assert resolve(Source.PROMETHEUS, spelling, None) == "error_rate"
    for ntype in (NodeType.SERVICE, NodeType.API_ENDPOINT, NodeType.EXTERNAL_SERVICE):
        assert applies_to_ok("error_rate", ntype)


# ── the per-type CATEGORY view (2026-07-23 primitives §2 — typed schema as DATA) ──────
def test_categories_for_groups_predicates_by_species_and_type():
    # SERVICE: degraded is a STATE, error_rate/latency_p99 are READINGs, `trace` is a SPAN
    svc = categories_for(NodeType.SERVICE)
    assert "degraded" in svc["state"]
    assert "error_rate" in svc["reading"] and "latency_p99" in svc["reading"]
    assert "trace" in svc["span"]
    # DEPLOYMENT: `image` is a STATE (queryable as-of-T), never a PROPERTY
    assert "image" in categories_for(NodeType.DEPLOYMENT)["state"]
    # CODE_COMMIT: diff/blame are renderable PROPERTY content (never sliced as-of-T)
    commit = categories_for(NodeType.CODE_COMMIT)
    assert "diff_summary" in commit["property"] and "blame_line" in commit["property"]
    # SCHEMA: index_health/table_count are PROPERTY (timeless facts about it)
    assert "index_health" in categories_for(NodeType.SCHEMA)["property"]


def test_categories_for_events_come_from_the_nodespec():
    # events are the NodeSpec's `event_types`, not the fact dictionary — BUSINESS_TRANSACTION
    # carries the `trace_captured` occurrence in its span/event surface (M24 single source).
    bt = categories_for(NodeType.BUSINESS_TRANSACTION)
    assert "trace_captured" in bt["event"]
    assert "trace" in bt["span"]                       # the reifiable distributed trace


def test_categories_for_shape_is_uniform_and_ordered():
    for ntype in NodeType:
        cats = categories_for(ntype)
        assert tuple(cats.keys()) == CATEGORY_ORDER    # every category present, in router order
        for v in cats.values():
            assert isinstance(v, tuple) and list(v) == sorted(v)   # sorted → replay-stable


def test_categories_for_non_event_union_equals_fact_names_for():
    # the four datum-shape categories partition exactly the fact/reading/state/property canonicals
    # the reducer accepts on a type — the category view can never drift from `applies_to`.
    for ntype in NodeType:
        cats = categories_for(ntype)
        union = set(cats["property"]) | set(cats["state"]) | set(cats["reading"]) | set(cats["span"])
        assert union == set(fact_names_for(ntype)), ntype.value


def test_registry_node_categories_delegates_to_the_dictionary():
    from iw_engine.domain import registry

    for ntype in (NodeType.SERVICE, NodeType.DATABASE, NodeType.CODE_COMMIT):
        assert registry.node_categories(ntype) == categories_for(ntype)


def test_catalog_renders_the_category_split_per_type():
    from iw_engine.domain.catalog import render_nodes

    txt = render_nodes()
    # the service line carries its reading + span categories as DATA (not a flat fact list)
    assert "reading=[" in txt and "span=[" in txt
    assert "state=[" in txt and "property=[" in txt
    # every rendered category label is a real member of the router order
    import re
    for label in re.findall(r"(\w+)=\[", txt):
        assert label in CATEGORY_ORDER


def test_entries_are_frozen_dataclasses():
    from dataclasses import FrozenInstanceError

    e = DICTIONARY["error_rate"]
    assert isinstance(e, DictEntry)
    with pytest.raises(FrozenInstanceError):
        e.name = "x"  # frozen
