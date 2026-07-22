"""graph/tools — the governed traversal + focus-slice surface for the P7 reasoning loop.

Hand-built graphs, no engine run: these tools are pure read-only functions over the Graph
projection, so the tests pin (1) traversal correctness incl. direction + cycles, (2) the
governance rules (ACTIVE-only, never the inferred layer, hypothesis nodes never entered,
P5 remap resolution), (3) blast-radius direction-awareness over the dependency spine, and
(4) the B9.3 focus-slice contract: tier assignment, latest-per-predicate facts, boundedness
under ANY budget, and the invariant `full + frontier + collapsed == total`.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from iw_engine.domain.common import Confidence
from iw_engine.domain.edge import Edge
from iw_engine.domain.enums import EdgeType, FactState, NodeType, Origin, Source
from iw_engine.domain.fact import Fact
from iw_engine.domain.node import Node
from iw_engine.domain.registry import edge_id
from iw_engine.graph.graph import Graph
from iw_engine.graph.tools import (
    CONTAINMENT_EDGE_TYPES,
    DEPENDENCY_EDGE_TYPES,
    blast_radius,
    focus_slice,
    neighbours,
    path,
    walk,
)

T1 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
T2 = datetime(2026, 7, 19, 15, 0, tzinfo=UTC)
T3 = datetime(2026, 7, 19, 16, 0, tzinfo=UTC)

SVC, SVC2, DB, API = "svc:payments", "svc:checkout", "db:orders", "api:pay"
POD, HOST, NS, CACHE = "pod:p1", "host:h1", "ns:prod", "cache:redis"
ANOM, HYP, CHG = "anom:1", "hyp:h1", "chg:c1"


def _n(g: Graph, nid: str, ntype: NodeType) -> None:
    g.upsert_node(Node(id=nid, type=ntype, created_by=1))


def _e(g: Graph, etype: EdgeType, src: str, dst: str, origin: Origin = Origin.DECLARED, *,
       state: FactState = FactState.ACTIVE, invalidated_by: str | None = None) -> Edge:
    conf = Confidence(value=0.7, basis="test") if origin == Origin.INFERRED else None
    return g.add_edge(Edge(id=edge_id(etype, src, dst, origin), type=etype, src=src, dst=dst,
                           origin=origin, confidence=conf, state=state,
                           invalidated_by=invalidated_by, created_by=1))


def _f(g: Graph, fid: str, nid: str, predicate: str, value, ts: datetime) -> None:
    g.add_fact(Fact(id=fid, subject_ref=nid, predicate=predicate, value=value,
                    valid_from=ts, observed_at=ts, source=Source.PROMETHEUS,
                    source_reliability=0.9, created_by=1))


def spine_graph() -> Graph:
    """Two structural islands + an inferred layer that traversal must never ride.

    svc:checkout -CALLS(disc)-> svc:payments -DEPENDS_ON-> db:orders
                                svc:payments -EXPOSES--> api:pay
                                svc:payments -DEPENDS_ON-> cache:redis   [RETRACTED]
    ns:prod -CONTAINS-> pod:p1 -RUNS_ON(disc)-> host:h1
    anom:1 -CAUSED_BY(inferred)-> db:orders ; svc:payments -SUPPORTS(inferred)-> hyp:h1
    """
    g = Graph()
    for nid, t in ((SVC, NodeType.SERVICE), (SVC2, NodeType.SERVICE), (DB, NodeType.DATABASE),
                   (API, NodeType.API_ENDPOINT), (CACHE, NodeType.CACHE), (POD, NodeType.POD),
                   (HOST, NodeType.HOST), (NS, NodeType.NAMESPACE), (ANOM, NodeType.ANOMALY),
                   (HYP, NodeType.HYPOTHESIS)):
        _n(g, nid, t)
    _e(g, EdgeType.DEPENDS_ON, SVC, DB)
    _e(g, EdgeType.CALLS, SVC2, SVC, Origin.DISCOVERED)
    _e(g, EdgeType.EXPOSES, SVC, API)
    _e(g, EdgeType.DEPENDS_ON, SVC, CACHE, state=FactState.RETRACTED)
    _e(g, EdgeType.CONTAINS, NS, POD)
    _e(g, EdgeType.RUNS_ON, POD, HOST, Origin.DISCOVERED)
    _e(g, EdgeType.CAUSED_BY, ANOM, DB, Origin.INFERRED)
    _e(g, EdgeType.SUPPORTS, SVC, HYP, Origin.INFERRED)
    return g


# ── neighbours ────────────────────────────────────────────────────────────────────────
def test_neighbours_direction_out_in_both():
    g = spine_graph()
    out = neighbours(g, SVC, direction="out")
    assert [(v["id"], v["edge_type"]) for v in out["neighbours"]] == [
        (API, "exposes"), (DB, "depends_on")]
    inn = neighbours(g, SVC, direction="in")
    assert [(v["id"], v["edge_type"], v["direction"]) for v in inn["neighbours"]] == [
        (SVC2, "calls", "in")]
    both = neighbours(g, SVC)
    assert both["count"] == 3
    assert {v["id"] for v in both["neighbours"]} == {API, DB, SVC2}


def test_neighbours_edge_type_filter():
    g = spine_graph()
    got = neighbours(g, SVC, edge_types=[EdgeType.DEPENDS_ON])
    assert [v["id"] for v in got["neighbours"]] == [DB]   # cache edge is RETRACTED — never shown


def test_neighbours_governance_inferred_and_hypothesis_never_cross():
    g = spine_graph()
    assert [v["id"] for v in neighbours(g, DB)["neighbours"]] == [SVC]   # not anom (CAUSED_BY)
    assert neighbours(g, HYP)["count"] == 0                              # hypothesis: no spine
    assert all(v["id"] != HYP for v in neighbours(g, SVC)["neighbours"])


def test_neighbours_multi_edge_pair_reports_each_edge():
    g = Graph()
    _n(g, "a", NodeType.SERVICE)
    _n(g, "b", NodeType.DATABASE)
    _e(g, EdgeType.DEPENDS_ON, "a", "b")
    _e(g, EdgeType.CALLS, "a", "b", Origin.DISCOVERED)
    got = neighbours(g, "a")
    assert [(v["id"], v["edge_type"]) for v in got["neighbours"]] == [
        ("b", "calls"), ("b", "depends_on")]


def test_neighbours_resolves_p5_remaps_and_rejects_unknown_and_bad_direction():
    g = spine_graph()
    g.id_remaps["svc:old"] = SVC
    assert neighbours(g, "svc:old")["node"] == SVC
    with pytest.raises(KeyError, match="unknown node"):
        neighbours(g, "svc:nope")
    with pytest.raises(ValueError, match="direction"):
        neighbours(g, SVC, direction="sideways")


# ── walk ──────────────────────────────────────────────────────────────────────────────
def test_walk_zero_hops_is_just_the_start():
    got = walk(spine_graph(), SVC2, 0)
    assert got["nodes"] == [{"id": SVC2, "type": "service", "hops": 0}]
    assert got["edges"] == [] and got["truncated"] is False


def test_walk_bounded_bfs_with_hop_distances_and_induced_edges():
    got = walk(spine_graph(), SVC2, 3, direction="out")
    assert [(v["id"], v["hops"]) for v in got["nodes"]] == [
        (SVC2, 0), (SVC, 1), (API, 2), (DB, 2)]
    assert [(e["src"], e["dst"], e["type"]) for e in got["edges"]] == [
        (SVC2, SVC, "calls"), (SVC, API, "exposes"), (SVC, DB, "depends_on")]


def test_walk_direction_in_and_both():
    g = spine_graph()
    assert [v["id"] for v in walk(g, HOST, 2, direction="in")["nodes"]] == [HOST, POD, NS]
    both = walk(g, DB, 2)
    assert [(v["id"], v["hops"]) for v in both["nodes"]] == [
        (DB, 0), (SVC, 1), (API, 2), (SVC2, 2)]


def test_walk_edge_type_filter_narrows_the_spine():
    got = walk(spine_graph(), SVC2, 5, [EdgeType.CALLS])
    assert [v["id"] for v in got["nodes"]] == [SVC2, SVC]


def test_walk_terminates_on_cycles():
    g = Graph()
    for nid in ("a", "b", "c"):
        _n(g, nid, NodeType.SERVICE)
    _e(g, EdgeType.DEPENDS_ON, "a", "b")
    _e(g, EdgeType.DEPENDS_ON, "b", "c")
    _e(g, EdgeType.DEPENDS_ON, "c", "a")
    got = walk(g, "a", 10)
    assert [v["id"] for v in got["nodes"]] == ["a", "b", "c"]
    assert got["truncated"] is False


def test_walk_max_nodes_truncates_deterministically():
    got = walk(spine_graph(), SVC2, 3, direction="out", max_nodes=2)
    assert [v["id"] for v in got["nodes"]] == [SVC2, SVC]
    assert got["truncated"] is True


def test_walk_never_enters_inferred_layer_or_hypotheses():
    ids = {v["id"] for v in walk(spine_graph(), SVC, 5)["nodes"]}
    assert HYP not in ids and ANOM not in ids and CACHE not in ids


def test_walk_validation():
    g = spine_graph()
    with pytest.raises(ValueError, match="max_hops"):
        walk(g, SVC, -1)
    with pytest.raises(ValueError, match="max_nodes"):
        walk(g, SVC, 1, max_nodes=0)
    with pytest.raises(KeyError, match="unknown node"):
        walk(g, "svc:nope", 1)


# ── blast_radius ──────────────────────────────────────────────────────────────────────
def test_blast_radius_climbs_dependencies_and_descends_containment():
    got = blast_radius(spine_graph(), DB)
    assert [(v["id"], v["hops"]) for v in got["impacted"]] == [
        (SVC, 1), (API, 2), (SVC2, 2)]   # dependents climb; svc's EXPOSES interface descends
    assert got["count"] == 3


def test_blast_radius_is_direction_aware_providers_are_not_impacted():
    g = spine_graph()
    assert blast_radius(g, SVC2)["count"] == 0                       # nothing depends on svc2
    got = blast_radius(g, SVC)
    assert {v["id"] for v in got["impacted"]} == {API, SVC2}         # NOT db (svc's provider)


def test_blast_radius_containment_and_placement():
    g = spine_graph()
    assert [v["id"] for v in blast_radius(g, NS)["impacted"]] == [POD]    # container descent
    assert [v["id"] for v in blast_radius(g, HOST)["impacted"]] == [POD]  # placement climb
    assert blast_radius(g, POD)["count"] == 0


def test_blast_radius_never_rides_the_inferred_layer():
    assert all(v["id"] != ANOM for v in blast_radius(spine_graph(), DB)["impacted"])


def test_blast_radius_hop_cap_and_custom_spine_params():
    g = spine_graph()
    assert [v["id"] for v in blast_radius(g, DB, max_hops=1)["impacted"]] == [SVC]
    got = blast_radius(g, DB, dependency_edges=[EdgeType.CALLS], containment_edges=[])
    assert got["count"] == 0                                        # svc→db edge is DEPENDS_ON
    assert EdgeType.CONTAINS in CONTAINMENT_EDGE_TYPES
    assert EdgeType.DEPENDS_ON in DEPENDENCY_EDGE_TYPES and CONTAINMENT_EDGE_TYPES.isdisjoint(
        {EdgeType.DEPENDS_ON, EdgeType.CALLS, EdgeType.RUNS_ON})


def test_blast_radius_cycle_terminates_and_excludes_self():
    g = Graph()
    for nid in ("a", "b", "c"):
        _n(g, nid, NodeType.SERVICE)
    _e(g, EdgeType.DEPENDS_ON, "a", "b")
    _e(g, EdgeType.DEPENDS_ON, "b", "c")
    _e(g, EdgeType.DEPENDS_ON, "c", "a")
    got = blast_radius(g, "a")   # c depends on a (hop 1); b depends on c (hop 2); never a itself
    assert [(v["id"], v["hops"]) for v in got["impacted"]] == [("c", 1), ("b", 2)]


def test_blast_radius_validation():
    g = spine_graph()
    with pytest.raises(KeyError, match="unknown node"):
        blast_radius(g, "db:nope")
    with pytest.raises(ValueError, match="max_hops"):
        blast_radius(g, DB, max_hops=-1)


# ── path ──────────────────────────────────────────────────────────────────────────────
def test_path_forward_and_reverse():
    g = spine_graph()
    fwd = path(g, SVC2, DB)
    assert fwd["found"] is True and fwd["hops"] == 2
    assert fwd["nodes"] == [SVC2, SVC, DB]
    assert [(e["type"], e["forward"]) for e in fwd["edges"]] == [
        ("calls", True), ("depends_on", True)]
    rev = path(g, DB, SVC2)
    assert rev["nodes"] == [DB, SVC, SVC2]
    assert [e["forward"] for e in rev["edges"]] == [False, False]


def test_path_respects_direction_and_edge_type_filters():
    g = spine_graph()
    assert path(g, DB, SVC2, direction="out")["found"] is False
    assert path(g, SVC2, DB, edge_types=[EdgeType.CALLS])["found"] is False


def test_path_trivial_disconnected_and_hop_cap():
    g = spine_graph()
    same = path(g, SVC, SVC)
    assert same == {"src": SVC, "dst": SVC, "found": True, "hops": 0, "nodes": [SVC], "edges": []}
    assert path(g, SVC, HOST)["found"] is False                     # separate structural island
    assert path(g, ANOM, DB)["found"] is False                      # only an inferred edge links them
    assert path(g, SVC2, DB, max_hops=1)["found"] is False
    assert path(g, SVC2, DB, max_hops=2)["found"] is True


def test_path_equal_length_tie_breaks_canonically_by_id():
    g = Graph()
    for nid in ("s", "m:a", "m:b", "t"):
        _n(g, nid, NodeType.SERVICE)
    _e(g, EdgeType.DEPENDS_ON, "s", "m:b")   # insertion order deliberately favors m:b
    _e(g, EdgeType.DEPENDS_ON, "m:b", "t")
    _e(g, EdgeType.DEPENDS_ON, "s", "m:a")
    _e(g, EdgeType.DEPENDS_ON, "m:a", "t")
    assert path(g, "s", "t")["nodes"] == ["s", "m:a", "t"]


# ── focus_slice ───────────────────────────────────────────────────────────────────────
def incident_graph() -> Graph:
    """spine_graph plus a live causal explanation, a refuted claim, and healthy fodder:
    hyp:h1 -CAUSED_BY-> anom:1 and -CAUSED_BY-> chg:c1 (ACTIVE); svc -SUPPORTS-> hyp:h1;
    anom:1 -CAUSED_BY-> db:orders RETRACTED (ruled out); facts on svc incl. a 3-point series.
    13 nodes total."""
    g = spine_graph()
    # spine_graph's inferred layer: replace with the richer incident story
    g.retract_edge(edge_id(EdgeType.CAUSED_BY, ANOM, DB, Origin.INFERRED),
                   invalidated_by="fact:refute-1")
    _n(g, CHG, NodeType.CHANGE_EVENT)
    _e(g, EdgeType.CAUSED_BY, HYP, ANOM, Origin.INFERRED)
    _e(g, EdgeType.CAUSED_BY, HYP, CHG, Origin.INFERRED)
    for i, nid in enumerate(("host:f1", "host:f2")):
        _n(g, nid, NodeType.HOST)
        _f(g, f"fact:h{i}", nid, "cpu", 10 + i, T1)
    _f(g, "fact:er1", SVC, "error_rate", 0.1, T1)
    _f(g, "fact:er2", SVC, "error_rate", 0.4, T2)
    _f(g, "fact:er3", SVC, "error_rate", 0.9, T3)
    _f(g, "fact:lat", SVC, "latency_p99", 850, T1)
    return g


def test_focus_slice_tiers_cause_path_suspects_frontier_collapsed():
    got = focus_slice(incident_graph(), ANOM, 30)
    assert got["focus"] == ANOM and got["total"] == 13
    assert [(v["id"], v["tier"]) for v in got["nodes"]] == [
        (ANOM, "focus"), (HYP, "cause_path"), (CHG, "cause_path"), (SVC, "suspect")]
    assert [v["id"] for v in got["frontier"]] == [API, DB, SVC2]
    assert all(v["hops"] == 1 and v["attached_to"] == [SVC] for v in got["frontier"])
    assert got["collapsed_count"] == 6                      # pod, host, ns, 2 fodder hosts, cache
    assert got["collapsed_types"] == {"cache": 1, "host": 3, "namespace": 1, "pod": 1}
    assert got["truncated"] is False


def test_focus_slice_invariant_full_plus_frontier_plus_collapsed_is_total():
    g = incident_graph()
    for budget in range(1, 16):
        got = focus_slice(g, ANOM, budget)
        assert len(got["nodes"]) + len(got["frontier"]) + got["collapsed_count"] == got["total"]
        assert len(got["nodes"]) + len(got["frontier"]) <= budget   # bounded regardless of size


def test_focus_slice_budget_drops_frontier_then_suspects_never_the_focus():
    g = incident_graph()
    b4 = focus_slice(g, ANOM, 4)
    assert [v["id"] for v in b4["nodes"]] == [ANOM, HYP, CHG, SVC]
    assert b4["frontier"] == [] and b4["truncated"] is True and b4["collapsed_count"] == 9
    b2 = focus_slice(g, ANOM, 2)
    assert [v["id"] for v in b2["nodes"]] == [ANOM, HYP]
    b1 = focus_slice(g, ANOM, 1)
    assert [v["id"] for v in b1["nodes"]] == [ANOM]                 # the symptom is never dropped
    b5 = focus_slice(g, ANOM, 5)
    assert [v["id"] for v in b5["frontier"]] == [API]               # remainder goes to frontier


def test_focus_slice_ruled_out_surface_and_active_only_suspects():
    got = focus_slice(incident_graph(), ANOM, 30)
    assert got["ruled_out"] == [{"type": "caused_by", "src": ANOM, "dst": DB,
                                 "invalidated_by": "fact:refute-1"}]
    # db is on a RETRACTED causal edge only → frontier (via structure), never a suspect
    assert DB in [v["id"] for v in got["frontier"]]
    # the active causal + evidence edges among rendered nodes ARE shown to the planner
    rendered_edges = {(e["type"], e["src"], e["dst"]) for e in got["edges"]}
    assert ("caused_by", HYP, ANOM) in rendered_edges
    assert ("supports", SVC, HYP) in rendered_edges
    assert ("caused_by", ANOM, DB) not in rendered_edges            # retracted: not an active edge


def test_focus_slice_facts_are_latest_per_predicate_and_capped():
    got = focus_slice(incident_graph(), ANOM, 30)
    svc_card = next(v for v in got["nodes"] if v["id"] == SVC)
    assert [(f["predicate"], f["value"]) for f in svc_card["facts"]] == [
        ("error_rate", 0.9), ("latency_p99", 850)]          # series deduped to its latest point
    capped = focus_slice(incident_graph(), ANOM, 30, max_facts_per_node=1)
    svc_card = next(v for v in capped["nodes"] if v["id"] == SVC)
    assert [(f["predicate"], f["value"]) for f in svc_card["facts"]] == [("error_rate", 0.9)]


def test_focus_slice_none_or_unknown_anomaly_degrades_to_focusless_view():
    g = incident_graph()
    for ref in (None, "anom:nope"):
        got = focus_slice(g, ref, 30)
        assert got["focus"] is None
        assert [(v["id"], v["tier"]) for v in got["nodes"]] == [
            (ANOM, "suspect"), (CHG, "suspect"), (HYP, "suspect"), (SVC, "suspect")]
        assert len(got["nodes"]) + len(got["frontier"]) + got["collapsed_count"] == got["total"]


def test_focus_slice_empty_graph():
    got = focus_slice(Graph(), None, 10)
    assert got == {"focus": None, "budget": 10, "total": 0, "nodes": [], "frontier": [],
                   "edges": [], "ruled_out": [], "collapsed_count": 0, "collapsed_types": {},
                   "truncated": False}


def test_focus_slice_resolves_p5_remapped_anomaly_ref():
    g = incident_graph()
    g.id_remaps["anom:old"] = ANOM
    assert focus_slice(g, "anom:old", 30)["focus"] == ANOM


def test_focus_slice_frontier_hops_parameter():
    got = focus_slice(incident_graph(), ANOM, 30, frontier_hops=0)
    assert got["frontier"] == [] and got["collapsed_count"] == 9
    assert len(got["nodes"]) + got["collapsed_count"] == got["total"]


def test_focus_slice_validation():
    g = incident_graph()
    with pytest.raises(ValueError, match="budget"):
        focus_slice(g, ANOM, 0)
    with pytest.raises(ValueError, match="frontier_hops"):
        focus_slice(g, ANOM, 5, frontier_hops=-1)
    with pytest.raises(ValueError, match="max_facts_per_node"):
        focus_slice(g, ANOM, 5, max_facts_per_node=-1)


def test_outputs_are_deterministic_and_json_serialisable():
    g = incident_graph()
    views = [focus_slice(g, ANOM, 7), walk(g, SVC2, 3), neighbours(g, SVC),
             blast_radius(g, DB), path(g, SVC2, DB)]
    again = [focus_slice(g, ANOM, 7), walk(g, SVC2, 3), neighbours(g, SVC),
             blast_radius(g, DB), path(g, SVC2, DB)]
    assert [json.dumps(v, sort_keys=True) for v in views] == [
        json.dumps(v, sort_keys=True) for v in again]


def test_tools_are_read_only():
    g = incident_graph()
    before = g.to_dict()
    focus_slice(g, ANOM, 5)
    walk(g, SVC2, 4)
    neighbours(g, SVC)
    blast_radius(g, DB)
    path(g, SVC2, DB)
    assert g.to_dict() == before
