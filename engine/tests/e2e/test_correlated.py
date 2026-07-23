"""End-to-end: the AIOps correlated-storm + identity-graduation scenario (W7 M27/M28).

Drives the REAL engine through the scenario and asserts the behaviors the demo surface never
showed before this scenario existed: BigPanda's correlation folded end-to-end (M28), the
generic_ci → DATABASE graduation via Retype with its history re-homed, the provisional-twin →
canonical Merge, and the Retract tombstone (M27) — all while converging on the confirmed root
cause exactly like every other scenario (the root-cause invariant is preserved).
"""
from __future__ import annotations

from iw_engine.domain.enums import EdgeType, FactState, HypothesisStatus

from . import scenario_correlated as sc
from ._helpers import assert_replay_equivalent, run


def test_correlated_converges_to_the_confirmed_migration_root():
    subject, script, fixtures = sc.build()
    res = run(subject, script, fixtures)

    assert res.phases_run == ["frame", "investigate", "investigate", "act", "verify", "close"]
    assert res.rejections == [], f"unexpected rejected ops: {res.rejections}"
    assert res.close_outcome == "resolved"
    assert res.confirmed is not None and res.confirmed.id == "hyp:h1"
    # root is the MIGRATION change event (a schema change roots at the change, not the datastore)
    assert res.confirmed.root_candidate == sc.CHG
    # the code-regression rival was RULED OUT (differential diagnosis), not ignored
    assert res.hypothesis_store.hypotheses["hyp:h2"].status == HypothesisStatus.REFUTED
    # the journal alone rebuilds BOTH projections exactly (source-of-truth guarantee)
    assert_replay_equivalent(res)


def test_bigpanda_correlation_folds_the_storm_end_to_end():
    """M28 — the AIOps correlation adapter's normalize() runs end-to-end (it had no fixture
    before): the primary incident, the affected-service blast radius, the member alerts each
    FIRED_ON their service, and the SIMILAR_TO prior all land in the graph."""
    subject, script, fixtures = sc.build()
    res = run(subject, script, fixtures)

    # the three member alerts + the three correlated services
    alerts = {n.id for n in res.graph.nodes.values() if n.type.value == "alert"}
    assert alerts == {"alert:bp-1", "alert:bp-2", "alert:bp-3"}
    for svc in (sc.SVC, sc.ORD, sc.LED):
        assert res.graph.node(svc) is not None, f"missing correlated service {svc}"
    # each alert FIRED_ON its service (the correlation's blast radius)
    fired = {(e.src, e.dst) for e in res.graph.edges.values() if e.type is EdgeType.FIRED_ON}
    assert fired == {("alert:bp-1", sc.SVC), ("alert:bp-2", sc.ORD), ("alert:bp-3", sc.LED)}
    # the SIMILAR_TO prior the platform clustered alongside the primary
    similar = {(e.src, e.dst) for e in res.graph.edges.values() if e.type is EdgeType.SIMILAR_TO}
    assert similar == {(sc.INC, sc.PRIOR)}
    # the transport that served it is on the record (M1): a bigpanda call, mock-served
    inv = [i for i in res.invocations if i.provider == "bigpanda"]
    assert inv and inv[0].served_by == "mock" and inv[0].op_count > 0


def test_generic_ci_graduates_to_a_database_with_history_re_homed():
    """M27 — the generic_ci escape hatch (an unclassified CMDB CI) graduates to its real DATABASE
    type via Retype: the old id resolves forever, its quarantined onset fact and its airlocked
    dependency re-home onto the real entity, and the real DATABASE vocabulary opens up."""
    subject, script, fixtures = sc.build()
    res = run(subject, script, fixtures)

    # the escape hatch is gone; the DATABASE exists; the old id resolves through the remap table
    assert res.graph.node(sc.GCI) is None
    db = res.graph.node(sc.DB)
    assert db is not None and db.type.value == "database" and db.provisional is False
    assert res.graph.id_remaps[sc.GCI] == sc.DB
    assert db.props["class_hint"] == "cmdb_ci_db_ora"   # provenance of the graduation survives

    # the quarantined onset fact re-homed onto the DATABASE (history survived the graduation)
    q = [f for f in res.graph.facts.values()
         if f.subject_ref == sc.DB and f.predicate == "x.servicenow.ora_apply_lag"]
    assert len(q) == 1 and q[0].provisional is True
    # the airlocked DEPENDS_ON edge re-keyed against the real type, airlock lineage preserved
    reheritd = [e for e in res.graph.edges.values()
                if e.type is EdgeType.DEPENDS_ON and e.dst == sc.DB and e.provisional]
    assert reheritd and reheritd[0].src == sc.SVC
    # the real DATABASE vocabulary is now legal on the graduated entity (supports H1)
    assert any(f.subject_ref == sc.DB and f.predicate == "replication_lag"
               for f in res.graph.facts.values())


def test_appd_only_twin_merges_into_the_canonical_service():
    """M27 — a credential-only observation (AppD keyed by app_id, no service_name) mints a
    PROVISIONAL twin that an explicit Merge folds into canonical payments-svc; the twin's fact
    re-homes and the old id stays resolvable."""
    subject, script, fixtures = sc.build()
    res = run(subject, script, fixtures)

    assert res.graph.node(sc.TWIN) is None                 # graduated away
    assert res.graph.id_remaps[sc.TWIN] == sc.SVC          # the old id remains an alias
    # the AppD-only observation re-homed onto canonical payments-svc
    assert any(f.subject_ref == sc.SVC and f.predicate == "error_rate" and f.source.value == "appd"
               for f in res.graph.facts.values())


def test_flaky_scrape_is_retracted_not_deleted():
    """M27 — the Retract tombstone: a wrong observation (a flaky 9999ms APM p99 scrape) is
    tombstoned (state=RETRACTED) but never deleted — it survives as evidence of what was once
    believed, carrying WHAT invalidated it."""
    subject, script, fixtures = sc.build()
    res = run(subject, script, fixtures)

    flaky = sc.fid(sc.SVC, "red_latency_p99", sc.T_ONSET)
    assert flaky in res.graph.facts                        # never deleted (append-only history)
    f = res.graph.facts[flaky]
    assert f.state is FactState.RETRACTED
    assert f.value == 9999                                 # the wrong value is preserved on record
    # the tombstone rode the journaled delta carrying WHAT invalidated it (the re-read p50) — so a
    # replay tombstones the same id, and the audit shows the fact that proved it wrong
    p50_fact = sc.fid(sc.SVC, "red_latency_p50", sc.T_INV)
    rets = [r for e in res.journal.phase_entries() for r in e.delta.retractions]
    assert any(r.target == flaky and r.invalidated_by == p50_fact for r in rets)
