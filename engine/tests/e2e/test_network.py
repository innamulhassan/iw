"""End-to-end: NETWORK layer. Drives the REAL engine (thin loop + reducer + fold + gate +
controller) with a ScriptedPlanner + mocked appd/prometheus/servicenow capability fixtures
through a full incident: an MTU/uplink change causes retransmits/probe failures on a
network segment; the callee service's own health stays clean (the boundary discriminator);
pricing-db is ruled out; reverting the change resolves it.
"""
from __future__ import annotations

from iw_engine.domain.enums import EdgeType, HypothesisStatus

from . import scenario_network as s2
from ._helpers import assert_replay_equivalent, run


def test_network_mtu_change_resolves_incident():
    subject, script, fixtures = s2.build()
    res = run(subject, script, fixtures)

    assert res.phases_run == ["frame", "investigate", "investigate", "act",
                              "verify", "close"]
    assert res.rejections == [], f"unexpected rejected ops: {res.rejections}"
    assert res.close_outcome == "resolved"
    assert res.confirmed is not None and res.confirmed.id == "hyp:h1"

    # differential diagnosis: pricing-db was ruled out, not ignored
    assert res.hypothesis_store.hypotheses["hyp:h2"].status == HypothesisStatus.REFUTED
    assert s2.fid(s2.DB, "conn_pool_util", s2.T_INV) in res.hypothesis_store.hypotheses["hyp:h2"].refuting_facts

    # the confirmed causal chain: H1 -> the network change (CHG-77)
    caused = res.graph.out_edges(s2.H1, EdgeType.CAUSED_BY)
    assert caused and caused[0].dst == s2.CHG

    # the journal alone rebuilds the graph exactly (source-of-truth guarantee)
    assert_replay_equivalent(res)   # graph AND hypothesis store (journal v2)


def test_network_boundary_discriminator_and_recovery():
    """The discriminator: NETWORK_SEGMENT carries the retransmit/probe-failure facts while
    the callee's own health check (appd healthrule_violations) comes back clean — the fault
    is at the boundary, not either endpoint. Confirms appd (bt_health/flowmap/
    healthrule_violations), prometheus (instant_query/range_query) and servicenow
    (find_recent_changes) all folded real capability output into the graph, and that
    recovery superseded (not overwrote) the pre-fix network metrics.
    """
    subject, script, fixtures = s2.build()
    res = run(subject, script, fixtures)

    for node_id in [s2.SVC, s2.SVC_CALLEE, s2.NETSEG, s2.CHG, s2.DB, s2.ANOM, s2.ALERT, s2.H1, s2.BT]:
        assert res.graph.node(node_id) is not None, f"missing node {node_id}"

    # appd flowmap discovered the caller -> callee dependency (the topology of the boundary)
    deps = res.graph.out_edges(s2.SVC, EdgeType.DEPENDS_ON)
    assert any(e.dst == s2.SVC_CALLEE for e in deps)

    # the callee's clean health check is real supporting evidence for H1 (null-result sentinel)
    clean_id = s2.fid(s2.SVC_CALLEE, "no_evidence:healthrule_violations", s2.T_INV)
    assert clean_id in res.hypothesis_store.hypotheses["hyp:h1"].supporting_facts

    # retrans_segs was superseded on recovery (bi-temporal), not overwritten
    retrans_facts = [f for f in res.graph.facts.values()
                     if f.subject_ref == s2.NETSEG and f.predicate == "retrans_segs"]
    assert len(retrans_facts) == 2  # 245 (superseded) + 8 (active)
    active = [f for f in retrans_facts if f.is_open]
    assert len(active) == 1 and active[0].value == 8
