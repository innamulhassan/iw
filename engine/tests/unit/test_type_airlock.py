"""P3 airlock step 4 — TYPE AIRLOCK: generic_ci structural participation (DOMAIN-v3 §2.4 row 2).

Before P3 the escape hatch was edge-isolated (3 of 316 legal pairs; blamable never). Now
`generic_ci` may substitute for either endpoint of a STRUCTURAL edge — the edge lands
`provisional`, origin FORCED to discovered, confidence (when present) reduced by the
`discovery_penalty` tunable — and can be NAMED A CAUSE via CAUSED_BY (declared pairs, still
inferred + mandatory confidence, provisional + penalized). Still governed: only the structural
layer substitutes, the non-generic endpoint must be legal on its side, and every other
causal/evidence edge type rejects a generic_ci exactly as before.
"""
from __future__ import annotations

from datetime import UTC, datetime

from iw_engine.domain.enums import EdgeType, NodeType, Origin
from iw_engine.domain.operations import AddEdge, AddNode, ProposeHypothesis
from iw_engine.domain.playbook import Tunables
from iw_engine.domain.registry import edge_airlocked, edge_allowed
from iw_engine.graph import Graph
from iw_engine.graph.reducer import materialize

T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
SID = "service:payments-api|prod"
CI = "generic_ci:mainframe-01"


def _seed():
    return [
        AddNode(type=NodeType.SERVICE, props={"service_name": "payments-api", "env": "prod"}),
        AddNode(type=NodeType.GENERIC_CI, props={"ci_id": "MAINFRAME-01",
                                                 "class_hint": "cmdb_ci_mainframe"}),
    ]


# ── registry: the substitution rule itself ─────────────────────────────────────
def test_generic_ci_substitutes_on_structural_edges_only():
    g, s = NodeType.GENERIC_CI, NodeType.SERVICE
    # structural: substitution on either side, and both-generic
    assert edge_allowed(EdgeType.DEPENDS_ON, s, g)
    assert edge_allowed(EdgeType.DEPENDS_ON, g, s)          # service is a legal dst there
    assert edge_allowed(EdgeType.RUNS_ON, g, NodeType.HOST)
    assert edge_allowed(EdgeType.DEPENDS_ON, g, g)
    assert edge_airlocked(EdgeType.CALLS, s, g)
    # governed: the non-generic endpoint must be legal on ITS side of that edge type
    assert not edge_allowed(EdgeType.DEPENDS_ON, g, NodeType.TEAM)     # team is never a dst
    assert not edge_allowed(EdgeType.RUNS_ON, NodeType.HOST, g)        # host is never a src
    # causal/evidence layers never substitute (caused_by's generic pairs are DECLARED, not
    # substituted — so edge_airlocked is False for them)
    assert not edge_airlocked(EdgeType.FIRED_ON, NodeType.ALERT, g)
    assert not edge_allowed(EdgeType.FIRED_ON, NodeType.ALERT, g)
    assert not edge_allowed(EdgeType.EMITTED, g, NodeType.ERROR_SIGNATURE)
    assert not edge_airlocked(EdgeType.CAUSED_BY, NodeType.HYPOTHESIS, g)
    assert edge_allowed(EdgeType.CAUSED_BY, NodeType.HYPOTHESIS, g)    # declared by P3


# ── reducer: the admitted edge is provisional + discovered + penalized ─────────
def test_structural_edge_to_generic_ci_lands_provisional_discovered():
    ops = [*_seed(), AddEdge(type=EdgeType.DEPENDS_ON, src=SID, dst=CI)]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == []
    assert len(mat.edges) == 1
    e = mat.edges[0]
    assert e.provisional is True
    assert e.origin is Origin.DISCOVERED          # forced — an observation, not a declaration
    assert e.src == SID and e.dst == CI


def test_generic_ci_as_src_and_declared_origin_is_overridden():
    ops = [*_seed(),
           AddNode(type=NodeType.HOST, props={"fqdn": "node-7"}),
           AddEdge(type=EdgeType.RUNS_ON, src=CI, dst="host:node-7", origin=Origin.DECLARED)]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == []
    e = mat.edges[0]
    assert e.provisional is True and e.origin is Origin.DISCOVERED   # claim overridden


def test_structural_substitution_confidence_penalty():
    tun = Tunables()
    ops = [*_seed(),
           AddEdge(type=EdgeType.DEPENDS_ON, src=SID, dst=CI, confidence_level="high")]
    mat = materialize(ops, 1, Graph(), tun)
    e = mat.edges[0]
    assert e.confidence is not None
    assert e.confidence.value == round(tun.confidence_band["high"] * tun.discovery_penalty, 4)
    assert "provisional" in e.confidence.basis


def test_generic_ci_is_now_blamable_via_caused_by():
    tun = Tunables()
    ops = [*_seed(),
           ProposeHypothesis(hid="h1", statement="the mainframe is the cause",
                             root_candidate=CI, confidence_level="med"),
           AddEdge(type=EdgeType.CAUSED_BY, src="hyp:h1", dst=CI, confidence_level="high")]
    mat = materialize(ops, 1, Graph(), tun)
    assert mat.rejections == []
    caused = [e for e in mat.edges if e.type is EdgeType.CAUSED_BY]
    assert len(caused) == 1
    e = caused[0]
    assert e.provisional is True
    assert e.origin is Origin.INFERRED            # a causal claim stays inferred, never "discovered"
    assert e.confidence.value == round(tun.confidence_band["high"] * tun.discovery_penalty, 4)


def test_caused_by_generic_ci_still_requires_confidence():
    ops = [*_seed(),
           ProposeHypothesis(hid="h1", statement="s", root_candidate=CI, confidence_level="med"),
           AddEdge(type=EdgeType.CAUSED_BY, src="hyp:h1", dst=CI)]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert [e for e in mat.edges if e.type is EdgeType.CAUSED_BY] == []
    assert any("requires confidence" in r.reason for r in mat.rejections)


def test_pre_p3_generic_pairs_stay_unmarked():
    """AFFECTS incident->generic_ci predates the airlock (bookkeeping, not new admission):
    it must not acquire the provisional flag or a forced origin."""
    ops = [
        AddNode(type=NodeType.INCIDENT, props={"incident_id": "INC-1"}),
        AddNode(type=NodeType.GENERIC_CI, props={"ci_id": "MAINFRAME-01"}),
        AddEdge(type=EdgeType.AFFECTS, src="incident:inc-1", dst="generic_ci:mainframe-01"),
    ]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == []
    assert mat.edges[0].provisional is False


def test_non_structural_substitution_still_rejected_by_reducer():
    ops = [*_seed(),
           AddNode(type=NodeType.ALERT, props={"alert_id": "ALT-1"}),
           AddEdge(type=EdgeType.FIRED_ON, src="alert:alt-1", dst=CI)]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.edges == []
    assert any("illegal edge" in r.reason for r in mat.rejections)


# ── the admitted edge is replay-safe and marked in the bundle ──────────────────
def test_airlocked_edge_survives_replay_and_is_marked_in_bundle():
    import pathlib

    from e2e import scenario_nochange
    from e2e._helpers import fact, phase

    import iw_engine
    from iw_engine.api.bundle import export_bundle
    from iw_engine.domain.enums import Source
    from iw_engine.graph import rebuild
    from iw_engine.runtime import Engine, ScriptedPlanner, load_playbook

    script = [phase("frame", ops=[
        AddNode(type=NodeType.ANOMALY, props={"anomaly_id": "ANOM-1"}),
        fact("anomaly:anom-1", "onset_value", 42, T0, source=Source.PROMETHEUS),
        *_seed(),
        AddEdge(type=EdgeType.DEPENDS_ON, src=SID, dst=CI),
    ], narrative="frame with a discovered unknown-CI dependency")]
    pb = load_playbook(pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml")
    eng = Engine(pb, ScriptedPlanner(script), clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))
    eng.start(scenario_nochange.build()[0])
    eng.step()

    g2, _ = rebuild(eng.journal)
    assert g2.to_dict() == eng.graph.to_dict()

    bundle = export_bundle(eng.result())
    marked = [e for e in bundle["graph"]["edges"] if e.get("provisional")]
    assert [(e["src"], e["dst"]) for e in marked] == [(SID, CI)]
    clean = [e for e in bundle["graph"]["edges"] if not e.get("provisional")]
    assert all("provisional" not in e for e in clean)   # golden-shape protection
