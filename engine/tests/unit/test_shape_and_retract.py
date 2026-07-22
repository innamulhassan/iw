"""P3 airlock step 6 — SHAPE QUARANTINE (DOMAIN-v3 §9.1, the airlock's second lane) and the
RETRACT op (R-J3 finally reachable through the op grammar).

Shape quarantine: a KNOWN name arriving with an invalid shape (unit mismatch; a claimed reading
without stat+window) is neither silently accepted nor erased — it lands PROVISIONAL with a
journaled rejection notice carrying the exact mismatch. The stat-mismatch lane is deliberately
off while the adapters stamp their compat-default `stat=gauge` (pinned below).

Retract: a tombstone rides the PhaseResult delta — validated by the reducer (target must
exist), applied by the fold AFTER the delta's additions, and replayed bit-for-bit.
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime

from e2e import scenario_nochange
from e2e._helpers import fact, node, phase

import iw_engine
from iw_engine.domain.assertion import Window
from iw_engine.domain.enums import EdgeType, FactState, NodeType, Source, Species, Stat
from iw_engine.domain.operations import AddAssertion, AddEdge, AddNode, Retract
from iw_engine.domain.playbook import Tunables
from iw_engine.domain.registry import edge_id, fact_id
from iw_engine.graph import Graph, rebuild
from iw_engine.graph.reducer import materialize
from iw_engine.runtime import Engine, ScriptedPlanner, load_playbook

PLAYBOOK = pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"
T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 19, 15, 0, tzinfo=UTC)
SID = "service:payments-api|prod"


def _svc() -> AddNode:
    return AddNode(type=NodeType.SERVICE, props={"service_name": "payments-api", "env": "prod"})


# ── shape quarantine ───────────────────────────────────────────────────────────
def test_unit_mismatch_lands_provisional_with_a_journaled_notice():
    ops = [_svc(),
           AddAssertion(subject=SID, name="latency_p99", value=4.8, unit="s",   # dict says ms
                        species=Species.STATE, valid_from=T0, observed_at=T0,
                        source=Source.PROMETHEUS)]
    mat = materialize(ops, 1, Graph(), Tunables())

    assert len(mat.facts) == 1                       # never erased — the observation survives
    f = mat.facts[0]
    assert f.predicate == "latency_p99" and f.provisional is True and f.unit == "s"
    # …and never silently accepted: the mismatch is on record (journal → bundle → next plan)
    assert len(mat.rejections) == 1
    r = mat.rejections[0]
    assert "shape quarantine" in r.reason and "unit mismatch" in r.reason
    assert "'s'" in r.reason and "'ms'" in r.reason and "landed provisional" in r.reason


def test_claimed_reading_without_stat_and_window_is_shape_quarantined():
    ops = [_svc(),
           AddAssertion(subject=SID, name="error_rate", value=0.4, unit="ratio",
                        species=Species.READING,                   # claims READING, no stat/window
                        valid_from=T0, observed_at=T0, source=Source.PROMETHEUS)]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.facts[0].provisional is True
    assert any("stat and window" in r.reason for r in mat.rejections)


def test_valid_shape_and_omitted_unit_stay_clean():
    ops = [_svc(),
           AddAssertion(subject=SID, name="latency_p99", value=480, unit="ms",
                        species=Species.STATE, valid_from=T0, observed_at=T0,
                        source=Source.PROMETHEUS),
           # the P2 twin protection survives P3: an omitted unit is always compatible
           AddAssertion(subject=SID, name="error_rate", value=0.4,
                        species=Species.STATE, valid_from=T0, observed_at=T0,
                        source=Source.PROMETHEUS)]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == []
    assert all(not f.provisional for f in mat.facts)


def test_adapter_gauge_default_is_not_quarantined():
    """The compat guarantee: adapters stamp stat=gauge + a point window on metrics whose entries
    declare rate/ratio/percentile (their fixtures state no stat). The stat-mismatch lane stays
    OFF until adapters emit true stats — this exact emission must remain clean."""
    ops = [_svc(),
           AddAssertion(subject=SID, name="red_errors", value=0.4, unit="ratio",
                        species=Species.READING, stat=Stat.GAUGE, window=Window(at=T0),
                        valid_from=T0, observed_at=T0, source=Source.PROMETHEUS,
                        source_reliability=0.97, source_native_name="red_errors")]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == []
    assert mat.facts[0].predicate == "error_rate" and mat.facts[0].provisional is False


# ── retract ────────────────────────────────────────────────────────────────────
def _engine(script) -> Engine:
    eng = Engine(load_playbook(PLAYBOOK), ScriptedPlanner(script),
                 clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))
    eng.start(scenario_nochange.build()[0])
    return eng


def test_retract_tombstones_a_fact_and_replays_bit_for_bit():
    anom = "anomaly:anom-1"
    wrong_fact = fact_id(anom, "onset_value", T0)
    script = [
        phase("frame", ops=[
            node(NodeType.ANOMALY, anomaly_id="ANOM-1"),
            fact(anom, "onset_value", 9999, T0, source=Source.PROMETHEUS),
        ], narrative="frame with a mis-read onset"),
        phase("investigate", ops=[
            fact(anom, "severity_score", 2, T1, source=Source.SERVICENOW),
            Retract(target=wrong_fact, reason="flaky exporter — onset re-read as 42"),
        ], narrative="tombstone the wrong observation"),
    ]
    eng = _engine(script)
    eng.step()
    eng.step()

    f = eng.graph.facts[wrong_fact]
    assert f.state is FactState.RETRACTED            # tombstoned…
    assert wrong_fact in eng.graph.facts             # …never deleted (append-only history)

    # the tombstone rides the journaled delta and replays exactly
    entry = eng.journal.phase_entries()[1]
    assert [(r.target, r.reason) for r in entry.delta.retractions] == \
           [(wrong_fact, "flaky exporter — onset re-read as 42")]
    g2, _ = rebuild(eng.journal)
    assert g2.to_dict() == eng.graph.to_dict()

    # surfaced: the bundle shows the fact retracted
    from iw_engine.api.bundle import export_bundle
    bf = [x for x in export_bundle(eng.result())["graph"]["facts"] if x["id"] == wrong_fact]
    assert bf[0]["state"] == "retracted"


def test_retract_edge_carries_invalidated_by_and_same_batch_target_works():
    from iw_engine.domain.enums import Origin
    eid = edge_id(EdgeType.DEPENDS_ON, SID, "database:orders-db", Origin.DECLARED)
    ops = [
        _svc(),
        AddNode(type=NodeType.DATABASE, props={"db_id": "orders-db"}),
        AddEdge(type=EdgeType.DEPENDS_ON, src=SID, dst="database:orders-db"),
        Retract(target=eid, invalidated_by="fact:proof-1", reason="stale CMDB row"),
    ]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == []
    assert [(r.target, r.invalidated_by) for r in mat.retractions] == [(eid, "fact:proof-1")]

    # apply the delta the way the fold does: adds first, tombstones after
    from iw_engine.domain.common import Confidence
    from iw_engine.domain.phase_result import PhaseResult, PhaseVerdict
    from iw_engine.graph.fold import apply_delta
    from iw_engine.hypothesis import HypothesisStore

    g = Graph()
    delta = PhaseResult(phase_id="frame", goal_restated="g", nodes_touched=mat.nodes,
                        edges_added=mat.edges, retractions=mat.retractions, narrative="n",
                        verdict=PhaseVerdict(status="advance",
                                             confidence=Confidence(value=0.9, basis="t")))
    apply_delta(delta, 1, g, HypothesisStore())
    e = g.edges[eid]
    assert e.state is FactState.RETRACTED and e.invalidated_by == "fact:proof-1"


def test_retract_unknown_target_is_rejected():
    mat = materialize([Retract(target="fact:doesnotexist", reason="oops")],
                      1, Graph(), Tunables())
    assert mat.retractions == []
    assert [r.reason for r in mat.rejections] == ["unknown retract target fact:doesnotexist"]


def test_live_planner_parses_retract():
    from iw_engine.runtime.live_planner import LivePlanner

    lp = LivePlanner(client=None, catalog_text="", tools_text="", tool_intents=set())
    op, err = lp._parse_op({"op": "retract", "target": "fact:abc", "reason": "wrong"})
    assert err is None and isinstance(op, Retract)
    assert op.target == "fact:abc" and op.reason == "wrong" and op.invalidated_by is None
