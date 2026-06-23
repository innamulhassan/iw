"""P2 · graph runtime (B9) — unit tests.

The INC-4821 causal chain (biz → svc → app → db → storage) drives the tool-surface tests; the B9.6
guards (unknown-id, conflicting facts, idempotent fold, annotate-needs-evidence, cycles, expand
cap) each get a focused test; the render-slice is checked bounded on a 147-node graph.
"""
from __future__ import annotations

import pytest

from engine.domain import Edge, Node
from engine.graph_runtime import (
    UNKNOWN,
    IncidentGraph,
    MetricsFold,
    TopologyFold,
    default_registry,
    render_slice,
)

CHAIN = ["biz:checkout-journey", "svc:checkout", "app:payments-api", "db:payments-ora", "stor:pay-vol"]


def base_graph() -> IncidentGraph:
    """The INC-4821 dependency chain, built via the topology fold-adapter."""
    g = IncidentGraph()
    nodes = [
        {"id": "biz:checkout-journey", "kind": "system", "type": "journey", "layer": "business"},
        {"id": "svc:checkout", "kind": "system", "type": "app", "layer": "app"},
        {"id": "app:payments-api", "kind": "system", "type": "app", "layer": "app",
         "labels": ["suspect"],
         "facts": [{"key": "health", "value": "degraded", "source": "appd",
                    "observed_at": "14:02", "impact_state": "degraded"}]},
        {"id": "db:payments-ora", "kind": "system", "type": "database", "layer": "database",
         "facts": [{"key": "io_wait_ms", "value": 28, "source": "oem", "observed_at": "14:03"}]},
        {"id": "stor:pay-vol", "kind": "system", "type": "storage", "layer": "storage",
         "labels": ["suspect"],
         "facts": [{"key": "latency_ms", "value": 22.4, "source": "netapp",
                    "observed_at": "14:05", "impact_state": "degraded"}]},
    ]
    edges = [{"type": "depends_on", "from": a, "to": b} for a, b in zip(CHAIN, CHAIN[1:])]
    TopologyFold().fold({"nodes": nodes, "edges": edges}, g)
    return g


# ── tool surface (B9.2) ─────────────────────────────────────────────────
def test_get_returns_node_with_facts():
    g = base_graph()
    n = g.get("app:payments-api")
    assert n["id"] == "app:payments-api"
    assert n["facts"][0]["source"] == "appd"


def test_get_unknown_never_invents():
    g = base_graph()
    assert g.get("does:not-exist") == {"id": "does:not-exist", "status": UNKNOWN}
    assert "does:not-exist" not in g.node_ids()   # querying must not create it


def test_neighbours_directional_and_filtered():
    g = base_graph()
    out = g.neighbours("app:payments-api", edge="depends_on", dir="out")
    assert [s["id"] for s in out["neighbours"]] == ["db:payments-ora"]
    inn = g.neighbours("app:payments-api", dir="in")
    assert [s["id"] for s in inn["neighbours"]] == ["svc:checkout"]


def test_walk_follows_chain():
    g = base_graph()
    w = g.walk("biz:checkout-journey", ["depends_on"], dir="out")
    assert w["path"] == CHAIN


def test_walk_until_predicate_stops():
    g = base_graph()
    w = g.walk("biz:checkout-journey", ["depends_on"], dir="out", until={"layer": "database"})
    assert w["path"][-1] == "db:payments-ora"


def test_find_by_label_and_unhealthy():
    g = base_graph()
    suspects = {s["id"] for s in g.find({"label": "suspect"})["matches"]}
    assert suspects == {"app:payments-api", "stor:pay-vol"}
    unhealthy = {s["id"] for s in g.find({"unhealthy": True})["matches"]}
    assert unhealthy == {"app:payments-api", "stor:pay-vol"}


def test_blast_radius_walks_dependents():
    g = base_graph()
    br = g.blast_radius("stor:pay-vol")
    # everything that (transitively) depends_on storage is impacted
    assert set(br["impacted"]) == {"biz:checkout-journey", "svc:checkout",
                                   "app:payments-api", "db:payments-ora"}


def test_path_shortest_causal():
    g = base_graph()
    assert g.path("app:payments-api", "stor:pay-vol")["path"] == \
        ["app:payments-api", "db:payments-ora", "stor:pay-vol"]


def test_path_unknown_endpoint():
    g = base_graph()
    assert g.path("app:payments-api", "nope")["status"] == UNKNOWN


# ── annotate (B9.2 / B9.6) ──────────────────────────────────────────────
def test_annotate_label_applies_with_evidence():
    g = base_graph()
    g.annotate("db:payments-ora", "label", "suspect", evidence_ref="oem://payments-ora/awr")
    assert "suspect" in g.raw_node("db:payments-ora").labels


def test_annotate_fact_applies_with_evidence():
    g = base_graph()
    g.annotate("db:payments-ora", "root_hint", "io-bound", evidence_ref="otel://trace/9af3", by_step="s4")
    facts = {f.key: f for f in g.raw_node("db:payments-ora").facts}
    assert facts["root_hint"].source == "agent"
    assert facts["root_hint"].evidence_ref == "otel://trace/9af3"


def test_annotate_without_evidence_is_rejected():
    g = base_graph()
    with pytest.raises(ValueError):
        g.annotate("db:payments-ora", "label", "suspect", evidence_ref="")


def test_annotate_unknown_target_rejected():
    g = base_graph()
    with pytest.raises(KeyError):
        g.annotate("ghost:node", "label", "suspect", evidence_ref="x://y")


# ── fold guards (B9.6) ──────────────────────────────────────────────────
def test_fold_is_idempotent_on_replay():
    g = base_graph()
    result = {"target": "db:payments-ora",
              "facts": [{"key": "io_wait_ms", "value": 28, "source": "oem", "observed_at": "14:03"}]}
    before = len(g.raw_node("db:payments-ora").facts)
    MetricsFold().fold(result, g)
    MetricsFold().fold(result, g)   # replay — must not duplicate
    assert len(g.raw_node("db:payments-ora").facts) == before  # same (node,key,source,observed_at)


def test_conflicting_facts_are_both_kept():
    g = base_graph()
    # a second source disagrees on health — keep both, never overwrite
    MetricsFold().fold(
        {"target": "app:payments-api",
         "facts": [{"key": "health", "value": "ok", "source": "synthetic", "observed_at": "14:02"}]},
        g,
    )
    healths = [f for f in g.raw_node("app:payments-api").facts if f.key == "health"]
    assert {f.source for f in healths} == {"appd", "synthetic"}
    assert {f.value for f in healths} == {"degraded", "ok"}


def test_fold_requires_existing_node_for_facts():
    g = base_graph()
    with pytest.raises(KeyError):
        MetricsFold().fold({"target": "ghost", "facts": [{"key": "x", "value": 1, "source": "s"}]}, g)


def test_default_registry_dispatch():
    g = base_graph()
    reg = default_registry()
    touched = reg.fold("metrics",
                       {"target": "stor:pay-vol",
                        "facts": [{"key": "disk", "value": "reconstructing", "source": "netapp",
                                   "observed_at": "14:05"}]}, g)
    assert touched == ["stor:pay-vol"]
    with pytest.raises(KeyError):
        reg.fold("no-such-source", {}, g)


# ── cycle safety (B9.6) ─────────────────────────────────────────────────
def test_walk_is_cycle_safe():
    g = base_graph()
    g.add_edge(Edge.model_validate({"type": "depends_on", "from": "stor:pay-vol",
                                    "to": "biz:checkout-journey"}))  # close the loop
    w = g.walk("biz:checkout-journey", ["depends_on"], dir="out")
    assert len(w["path"]) == len(set(w["path"]))   # terminates, no node twice


# ── expand-too-wide cap (B9.6) ──────────────────────────────────────────
def test_neighbours_caps_breadth():
    g = base_graph()
    for i in range(20):
        g.upsert_node(Node.model_validate({"id": f"leaf:{i}", "kind": "system", "type": "app"}))
        g.add_edge(Edge.model_validate({"type": "depends_on", "from": "stor:pay-vol", "to": f"leaf:{i}"}))
    out = g.neighbours("stor:pay-vol", edge="depends_on", cap=12)
    assert len(out["neighbours"]) == 12
    assert out["more"] == 8


# ── render-slice bounded on a large graph (B9.3, AC6) ───────────────────
def test_render_slice_is_bounded_on_147_nodes():
    g = base_graph()
    # inflate to 147 nodes with healthy, unflagged, unconnected leaves
    for i in range(142):
        g.upsert_node(Node.model_validate({"id": f"host:{i}", "kind": "system", "type": "compute",
                                           "layer": "compute"}))
    assert len(g) == 147
    sl = render_slice(g, "biz:checkout-journey", cause_path=CHAIN)
    assert sl["total"] == 147
    assert sl["rendered"] <= 30           # bounded regardless of size
    assert sl["collapsed"]["count"] >= 117
    # the live nodes (subject + cause path + suspects) are in full
    full_ids = {n["id"] for n in sl["full"]}
    assert {"biz:checkout-journey", "stor:pay-vol", "app:payments-api"} <= full_ids


# ── audit fixes: predicate correctness, BFS walk, blast-radius coverage, annotate idempotency ──
def test_find_rejects_unknown_predicate_key():
    g = base_graph()
    assert g.find({"bogus": True})["count"] == 0      # unknown key matches NOTHING (not match-all)
    assert g.find({})["count"] == len(g)              # empty predicate still matches all


def test_find_impacted_and_id_predicates():
    g = base_graph()
    impacted = {m["id"] for m in g.find({"impacted": True})["matches"]}
    assert impacted == {"app:payments-api", "stor:pay-vol"}     # the two degraded nodes
    one = g.find({"id": "db:payments-ora"})
    assert one["count"] == 1 and one["matches"][0]["id"] == "db:payments-ora"


def _node(nid: str) -> Node:
    return Node.model_validate({"id": nid, "kind": "system", "type": "app", "layer": "app"})


def _edge(a: str, b: str, t: str = "depends_on") -> Edge:
    return Edge.model_validate({"type": t, "from": a, "to": b})


def test_walk_until_bfs_finds_target_on_branching_topology():
    g = IncidentGraph()
    for nid in ["a", "dead", "c", "target"]:
        g.upsert_node(_node(nid))
    for a, b in [("a", "dead"), ("a", "c"), ("c", "target")]:   # a→dead AND a→c→target
        g.add_edge(_edge(a, b))
    w = g.walk("a", ["depends_on"], until={"id": "target"})     # greedy walk would dead-end on `dead`
    assert w["reached"] is True
    assert w["path"][-1] == "target"


def test_walk_until_reports_unreached_not_silent():
    g = base_graph()
    w = g.walk("biz:checkout-journey", ["depends_on"], until={"id": "nope"})
    assert w["reached"] is False


def test_blast_radius_includes_hosted_on_chain():
    g = IncidentGraph()
    for nid in ["app:x", "db:y", "stor:z"]:
        g.upsert_node(_node(nid))
    g.add_edge(_edge("app:x", "db:y", "depends_on"))
    g.add_edge(_edge("db:y", "stor:z", "hosted_on"))
    br = g.blast_radius("stor:z")
    assert "db:y" in br["impacted"]            # hosted_on shares depends_on's impact direction
    assert "app:x" in br["impacted"]           # transitive up the chain


def test_annotate_keeps_corrected_finding_with_new_evidence():
    g = base_graph()
    t = "db:payments-ora"
    g.annotate(t, "root_hint", "io_wait", evidence_ref="otel://1")
    g.annotate(t, "root_hint", "disk_latency", evidence_ref="otel://2")   # corrected — new evidence
    facts = [f for f in g.raw_node(t).facts if f.key == "root_hint"]
    assert len(facts) == 2                     # both kept (B9.6 never silently overwrites)


def test_annotate_exact_replay_is_idempotent():
    g = base_graph()
    t = "db:payments-ora"
    g.annotate(t, "root_hint", "io_wait", evidence_ref="otel://1")
    g.annotate(t, "root_hint", "io_wait", evidence_ref="otel://1")        # exact replay
    facts = [f for f in g.raw_node(t).facts if f.key == "root_hint"]
    assert len(facts) == 1


# ── audit fixes: globally-bounded render frontier, recency-aware health ──
def test_render_slice_frontier_is_globally_bounded():
    g = IncidentGraph()
    for h in range(3):                                     # 3 suspect hubs, 10 distinct leaves each
        g.upsert_node(Node.model_validate({"id": f"hub{h}", "kind": "system", "type": "app",
                                           "layer": "app", "labels": ["suspect"]}))
        for i in range(10):
            leaf = f"leaf{h}-{i}"
            g.upsert_node(_node(leaf))
            g.add_edge(_edge(f"hub{h}", leaf))
    sl = render_slice(g, "hub0", frontier_cap=20, expand_cap=12)
    assert len(sl["frontier"]) == 20                       # 30 distinct neighbours capped to 20
    # invariant holds: full + rendered-frontier + collapsed == total (overflow folds into collapsed)
    assert len(sl["full"]) + len(sl["frontier"]) + sl["collapsed"]["count"] == sl["total"]


def test_health_verdict_is_recency_aware():
    g = IncidentGraph()
    g.upsert_node(Node.model_validate({"id": "n", "kind": "system", "type": "app", "layer": "app",
        "facts": [
            {"key": "health", "value": "degraded", "source": "appd", "observed_at": "14:00", "impact_state": "degraded"},
            {"key": "health", "value": "ok", "source": "appd", "observed_at": "14:10", "impact_state": "ok"}]}))
    assert g.find({"impacted": True})["count"] == 0        # latest (14:10) is ok — stale degraded loses
