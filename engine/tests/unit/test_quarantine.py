"""P3 airlock step 3 — NAME QUARANTINE (DOMAIN-v3 §2.4 row 1).

An assertion whose name is unknown to the dictionary (no canonical, no alias, no split input)
is NOT rejected-and-erased: it lands as a PROVISIONAL assertion under the quarantine namespace
`x.<source>.<native>`, species as the op inferred it, journaled with the delta, and counted
toward promotion. Promotion stays a human core-registry edit — nothing here mutates the
vocabulary. Referential integrity is untouched (unknown SUBJECT still rejects), and a KNOWN
name on the wrong type still rejects (the airlock is for unknown names, not illegal placements).
"""
from __future__ import annotations

from datetime import UTC, datetime

from e2e._helpers import fact, node, phase

from iw_engine.domain.dictionary import is_quarantined, quarantine_name
from iw_engine.domain.enums import NodeType, Source, Species
from iw_engine.domain.operations import AddAssertion, AddFact, AddNode
from iw_engine.domain.playbook import Tunables
from iw_engine.graph import Graph
from iw_engine.graph.reducer import materialize

T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
SID = "service:payments-api|prod"


def _svc() -> AddNode:
    return AddNode(type=NodeType.SERVICE, props={"service_name": "payments-api", "env": "prod"})


# ── an unknown predicate survives as provisional — not dropped ─────────────────
def test_unknown_fact_name_lands_quarantined_not_rejected():
    ops = [
        _svc(),
        AddAssertion(subject=SID, name="weird_vendor_metric", value=17.3, species=Species.STATE,
                     valid_from=T0, observed_at=T0, source=Source.PROMETHEUS,
                     source_reliability=0.9),
    ]
    mat = materialize(ops, 1, Graph(), Tunables())

    assert mat.rejections == []                       # NOT erased
    assert len(mat.facts) == 1
    f = mat.facts[0]
    assert f.predicate == "x.prometheus.weird_vendor_metric"
    assert is_quarantined(f.predicate)
    assert f.provisional is True
    assert f.source_native_name == "weird_vendor_metric"   # the vendor spelling survives
    assert f.value == 17.3                                  # the observation survives intact
    assert f.source_reliability == 0.9                      # belief discipline unchanged


def test_unknown_name_via_the_addfact_shim_quarantines_too():
    ops = [
        _svc(),
        AddFact(subject=SID, predicate="mystery_gauge", value=1, valid_from=T0, observed_at=T0,
                source=Source.SPLUNK, source_reliability=0.9),
    ]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == []
    assert mat.facts[0].predicate == "x.splunk.mystery_gauge"
    assert mat.facts[0].provisional is True


def test_unknown_event_type_lands_quarantined():
    ops = [
        _svc(),
        AddAssertion(subject=SID, name="WeirdVendorReason", species=Species.EVENT,
                     occurred_at=T0, observed_at=T0, value={"detail": 1}, source=Source.OCP),
    ]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == []
    assert len(mat.events) == 1
    e = mat.events[0]
    assert e.type == "x.ocp.WeirdVendorReason" and e.provisional is True
    assert e.source_native_name == "WeirdVendorReason"
    assert e.payload == {"detail": 1}


def test_quarantine_is_deterministic_and_counts_by_repetition():
    """Same source+native → same quarantine spelling: repeats accumulate under ONE name (the
    promotion-frequency signal a human reads), and a same-subject repeat supersedes cleanly."""
    assert quarantine_name(Source.OCP, "OOMPressure") == "x.ocp.OOMPressure"
    g = Graph()
    first = materialize([
        _svc(),
        AddAssertion(subject=SID, name="OOMPressure", value=1, species=Species.STATE,
                     valid_from=T0, observed_at=T0, source=Source.OCP),
    ], 1, g, Tunables())
    for n in first.nodes:
        g.upsert_node(n)
    for f in first.facts:
        g.add_fact(f)
    second = materialize([
        AddAssertion(subject=SID, name="OOMPressure", value=2, species=Species.STATE,
                     valid_from=datetime(2026, 7, 19, 15, 0, tzinfo=UTC),
                     observed_at=datetime(2026, 7, 19, 15, 0, tzinfo=UTC), source=Source.OCP),
    ], 2, g, Tunables())
    assert second.facts[0].predicate == first.facts[0].predicate == "x.ocp.OOMPressure"
    assert second.facts[0].supersedes == first.facts[0].id     # one name, one history


# ── what the airlock does NOT relax ────────────────────────────────────────────
def test_unknown_subject_still_rejects():
    ops = [AddAssertion(subject="database:ghost|prod", name="weird_metric", value=1,
                        species=Species.STATE, valid_from=T0, observed_at=T0,
                        source=Source.PROMETHEUS)]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.facts == []
    assert [r.reason for r in mat.rejections] == ["unknown subject database:ghost|prod"]


def test_known_name_on_wrong_type_still_rejects():
    ops = [
        AddNode(type=NodeType.ANOMALY, props={"anomaly_id": "ANOM-1"}),
        AddFact(subject="anomaly:anom-1", predicate="degraded", value=True,
                valid_from=T0, observed_at=T0, source=Source.PROMETHEUS,
                source_reliability=0.9),
    ]
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.facts == []
    assert "not allowed on anomaly" in mat.rejections[0].reason


# ── the quarantined assertion is journaled, replayed, and surfaced ─────────────
def test_quarantined_fact_survives_journal_replay_and_bundle():
    import pathlib

    from e2e import scenario_nochange

    import iw_engine
    from iw_engine.api.bundle import export_bundle
    from iw_engine.graph import rebuild
    from iw_engine.runtime import Engine, ScriptedPlanner, load_playbook

    script = [phase("frame", ops=[
        node(NodeType.ANOMALY, anomaly_id="ANOM-1"),
        fact("anomaly:anom-1", "onset_value", 42, T0, source=Source.PROMETHEUS),
        fact("anomaly:anom-1", "vendor_surprise", 9, T0, source=Source.BIGPANDA),
    ], narrative="frame with a discovery")]
    pb = load_playbook(pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml")
    eng = Engine(pb, ScriptedPlanner(script), clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))
    eng.start(scenario_nochange.build()[0])
    eng.step()

    q = [f for f in eng.graph.facts.values() if f.provisional]
    assert [f.predicate for f in q] == ["x.bigpanda.vendor_surprise"]

    # replay reproduces the quarantined fact bit-for-bit (it rides the journaled delta)
    g2, _ = rebuild(eng.journal)
    assert g2.to_dict() == eng.graph.to_dict()

    # the bundle marks ONLY the provisional fact; closed-vocabulary facts keep their shape
    bundle = export_bundle(eng.result())
    marked = [f for f in bundle["graph"]["facts"] if f.get("provisional")]
    assert [f["predicate"] for f in marked] == ["x.bigpanda.vendor_surprise"]
    clean = [f for f in bundle["graph"]["facts"] if not f.get("provisional")]
    assert clean and all("provisional" not in f for f in clean)   # golden-shape protection


def test_live_planner_no_longer_predrops_unknown_names():
    from iw_engine.runtime.live_planner import LivePlanner

    # unknown name: passes through to the reducer's airlock (was: "unknown predicate" drop)
    assert LivePlanner._illegal_predicate(SID, "totally_made_up_metric") is None
    # known-name misplacement is still repaired away before it wastes a turn
    assert LivePlanner._illegal_predicate("anomaly:anom-1", "degraded")
