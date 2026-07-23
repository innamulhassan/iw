"""OBSERVED-edge belief the reducer EARNS, symmetric with the atom (NODE-EDGE-PRIMITIVES §5.2/§5.4).

An edge carries confidence XOR source_reliability, keyed on origin: a DECLARED spine edge is
trusted ~1.0, a DISCOVERED (telemetry-inferred) topology edge is graded < 1, and a discovered
STRUCTURAL edge below the provisional floor lands `provisional`. Belief is engine-filled (the LLM
never authors edges), never both fields at once. PROVENANCE/lineage is immutable — a retract naming
one is refused (superseded-on-rebuild, never retracted-as-wrong).
"""
from __future__ import annotations

from iw_engine.domain import registry
from iw_engine.domain.enums import ConfidenceLevel, EdgeType, NodeType, Origin
from iw_engine.domain.operations import AddEdge, AddNode, ProposeHypothesis, Retract
from iw_engine.domain.playbook import Tunables
from iw_engine.graph import Graph
from iw_engine.graph.reducer import materialize


def _svc(name: str) -> str:
    return registry.node_id(NodeType.SERVICE, {"service_name": name, "env": "prod"})


def _two_services():
    return [
        AddNode(type=NodeType.SERVICE, props={"service_name": "checkout-api", "env": "prod"}),
        AddNode(type=NodeType.SERVICE, props={"service_name": "payments-api", "env": "prod"}),
    ]


# ── the observed-edge belief the reducer earns ─────────────────────────────────
def test_discovered_structural_edge_earns_graded_reliability_below_one():
    ops = [*_two_services(),
           AddEdge(type=EdgeType.CALLS, src=_svc("checkout-api"), dst=_svc("payments-api"))]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == []
    (e,) = mat.edges
    assert e.origin is Origin.DISCOVERED           # CALLS default
    assert e.source_reliability == 0.9             # graded < 1 (discovered_edge_reliability default)
    assert e.confidence is None                    # never both — belief keyed on origin
    assert e.provisional is False                  # 0.9 is above the floor


def test_declared_structural_edge_is_trusted_reliability_one():
    ops = [*_two_services(),
           AddEdge(type=EdgeType.DEPENDS_ON, src=_svc("checkout-api"), dst=_svc("payments-api"))]
    mat = materialize(ops, 1, Graph(), Tunables())
    (e,) = mat.edges
    assert e.origin is Origin.DECLARED             # DEPENDS_ON default
    assert e.source_reliability == 1.0             # a declared spine edge is trusted
    assert e.confidence is None
    assert e.provisional is False


def test_discovered_structural_below_floor_lands_provisional():
    # a genuinely low-reliability discovery trips the floor → provisional (dim, penalised)
    tun = Tunables(discovered_edge_reliability=0.4)   # below the 0.5 floor
    ops = [*_two_services(),
           AddEdge(type=EdgeType.CALLS, src=_svc("checkout-api"), dst=_svc("payments-api"))]
    mat = materialize(ops, 1, Graph(), tun)
    (e,) = mat.edges
    assert e.source_reliability == 0.4
    assert e.provisional is True                   # earned the airlock treatment via the floor
    assert e.confidence is None


def test_inferred_causal_edge_carries_confidence_not_reliability():
    change = registry.node_id(NodeType.CHANGE_EVENT, {"change_id": "CHG-1"})
    ops = [
        AddNode(type=NodeType.CHANGE_EVENT, props={"change_id": "CHG-1"}),
        ProposeHypothesis(hid="h1", statement="the change caused it",
                          confidence_level=ConfidenceLevel.HIGH),
        AddEdge(type=EdgeType.CAUSED_BY, src="hyp:h1", dst=change,
                confidence_level=ConfidenceLevel.HIGH),
    ]
    mat = materialize(ops, 1, Graph(), Tunables())
    (e,) = [x for x in mat.edges if x.type is EdgeType.CAUSED_BY]
    assert e.confidence is not None                # an inferred claim carries confidence
    assert e.source_reliability is None            # ...and NEVER a reliability (never both)


# ── PROVENANCE/lineage is immutable — a retract naming one is refused ───────────
def _materialize_into_graph(ops):
    g = Graph()
    mat = materialize(ops, 1, g, Tunables())
    for n in mat.nodes:
        g.upsert_node(n)
    for e in mat.edges:
        g.add_edge(e)
    return g, mat


def test_immutable_lineage_edge_refuses_retract():
    ops = [
        AddNode(type=NodeType.BUILD_ARTIFACT, props={"digest": "sha256:abc"}),
        AddNode(type=NodeType.CODE_COMMIT, props={"sha": "deadbeef"}),
        AddEdge(type=EdgeType.BUILT_FROM,
                src=registry.node_id(NodeType.BUILD_ARTIFACT, {"digest": "sha256:abc"}),
                dst=registry.node_id(NodeType.CODE_COMMIT, {"sha": "deadbeef"})),
    ]
    g, mat = _materialize_into_graph(ops)
    (edge,) = mat.edges
    assert registry.is_immutable_edge(edge.type)
    out = materialize([Retract(target=edge.id, reason="wrong build")], 2, g, Tunables())
    assert out.retractions == []                   # NOT tombstoned
    assert len(out.rejections) == 1
    assert "immutable" in out.rejections[0].reason.lower()


def test_structural_edge_stays_freely_retractable():
    # only the five lineage predicates are frozen — the STRUCTURAL spine stays refutable-with-trail
    ops = [*_two_services(),
           AddEdge(type=EdgeType.DEPENDS_ON, src=_svc("checkout-api"), dst=_svc("payments-api"))]
    g, mat = _materialize_into_graph(ops)
    (edge,) = mat.edges
    assert not registry.is_immutable_edge(edge.type)
    out = materialize([Retract(target=edge.id, reason="reorg")], 2, g, Tunables())
    assert out.rejections == []
    assert len(out.retractions) == 1 and out.retractions[0].target == edge.id
