"""The direction invariant (NODE-EDGE-PRIMITIVES §5.2/§5.3 invariant 1): per-category direction is
PREDICATE-FIXED, and CORRESPONDENCE (symmetric) edges are stored in one canonical direction yet
READ symmetric (from either endpoint) — without ever re-writing the stored direction (which would
break the servicenow fold's asserted primary->prior / primary->peer authoring direction).
"""
from __future__ import annotations

from iw_engine.domain import registry
from iw_engine.domain.common import Confidence
from iw_engine.domain.edge import Edge
from iw_engine.domain.enums import EdgeType, NodeType, Origin
from iw_engine.domain.operations import AddEdge, AddNode
from iw_engine.domain.playbook import Tunables
from iw_engine.domain.registry import canonical_symmetric_pair, is_symmetric_edge
from iw_engine.graph import Graph
from iw_engine.graph.reducer import materialize

INC_A = "incident:inc-6001"
INC_B = "incident:inc-5990"


def test_symmetric_edge_reads_from_either_endpoint():
    # a SIMILAR_TO prior stored in ONE direction (current inc-6001 -> prior inc-5990), exactly as
    # the servicenow fold authors it — read symmetric means both endpoints see the relationship.
    g = Graph()
    g.add_edge(Edge(id=f"edge:similar_to:{INC_A}->{INC_B}:inferred", type=EdgeType.SIMILAR_TO,
                    src=INC_A, dst=INC_B, origin=Origin.INFERRED,
                    confidence=Confidence(value=0.6, basis="clustered prior"), created_by=1))
    assert g.symmetric_neighbours(INC_A) == [INC_B]        # from the stored src
    assert g.symmetric_neighbours(INC_B) == [INC_A]        # ...and from the stored dst — read symmetric
    # scoping by the symmetric type works from either side too
    assert g.symmetric_neighbours(INC_B, EdgeType.SIMILAR_TO) == [INC_A]


def test_non_symmetric_edge_direction_is_predicate_fixed_and_never_symmetric_read():
    a = registry.node_id(NodeType.SERVICE, {"service_name": "checkout-api", "env": "prod"})
    b = registry.node_id(NodeType.SERVICE, {"service_name": "payments-api", "env": "prod"})
    ops = [
        AddNode(type=NodeType.SERVICE, props={"service_name": "checkout-api", "env": "prod"}),
        AddNode(type=NodeType.SERVICE, props={"service_name": "payments-api", "env": "prod"}),
        AddEdge(type=EdgeType.DEPENDS_ON, src=a, dst=b),
    ]
    g = Graph()
    mat = materialize(ops, 1, g, Tunables())
    for n in mat.nodes:
        g.upsert_node(n)
    for e in mat.edges:
        g.add_edge(e)
    (e,) = mat.edges
    # the reducer stores it EXACTLY as authored — direction is predicate-fixed, never canonicalised
    assert (e.src, e.dst) == (a, b)
    assert not is_symmetric_edge(e.type)
    # ...and a directional edge is invisible to the symmetric read from either side
    assert g.symmetric_neighbours(a) == []
    assert g.symmetric_neighbours(b) == []


def test_canonical_symmetric_pair_is_order_independent():
    # the opt-in write-side dedup: A~B and B~A canonicalise to the same stored pair
    assert canonical_symmetric_pair("incident:z", "incident:a") == ("incident:a", "incident:z")
    assert (canonical_symmetric_pair(INC_A, INC_B)
            == canonical_symmetric_pair(INC_B, INC_A) == (INC_B, INC_A))
