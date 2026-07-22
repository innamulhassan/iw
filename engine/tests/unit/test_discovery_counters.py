"""P3 airlock step 5 — PROMOTION COUNTERS (DOMAIN-v3 §2.4: "class_hint frequencies actually
counted" + quarantine names "counted toward promotion").

A small derived counter over the graph, surfaced in the bundle: repeated `class_hint` values on
generic_ci nodes say WHICH NodeType is missing; repeated `x.<source>.<native>` names say WHICH
DictEntry/alias is missing. The engine only counts — promotion stays a human core-registry edit
(no auto-promotion path exists anywhere in this module)."""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime, timedelta

from e2e import scenario_nochange
from e2e._helpers import fact, node, phase, run

import iw_engine
from iw_engine.api.bundle import discovery_counters, export_bundle
from iw_engine.domain.enums import NodeType, Source, Species
from iw_engine.domain.operations import AddAssertion
from iw_engine.runtime import Engine, ScriptedPlanner, load_playbook

T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)


def test_bundle_counts_class_hints_and_quarantined_names():
    """Two generic_ci nodes sharing a class_hint + one different; one quarantined name emitted
    twice (superseding) on one subject and once as an event — the counters read the frequency."""
    anom = "anomaly:anom-1"
    script = [phase("frame", ops=[
        node(NodeType.ANOMALY, anomaly_id="ANOM-1"),
        fact(anom, "onset_value", 42, T0, source=Source.PROMETHEUS),
        node(NodeType.GENERIC_CI, ci_id="MF-01", class_hint="cmdb_ci_mainframe"),
        node(NodeType.GENERIC_CI, ci_id="MF-02", class_hint="cmdb_ci_mainframe"),
        node(NodeType.GENERIC_CI, ci_id="SAN-1", class_hint="cmdb_ci_san"),
        fact(anom, "vendor_surprise", 1, T0, source=Source.BIGPANDA),
        fact(anom, "vendor_surprise", 2, T0 + timedelta(minutes=5), source=Source.BIGPANDA),
        AddAssertion(subject=anom, name="StorageAlarm", species=Species.EVENT,
                     occurred_at=T0, observed_at=T0, value={}, source=Source.BIGPANDA),
    ], narrative="a frame full of discoveries")]
    pb = load_playbook(pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml")
    eng = Engine(pb, ScriptedPlanner(script), clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))
    eng.start(scenario_nochange.build()[0])
    eng.step()

    bundle = export_bundle(eng.result())
    assert bundle["discovery"] == {
        "class_hints": {"cmdb_ci_mainframe": 2, "cmdb_ci_san": 1},
        "quarantined_names": {"x.bigpanda.StorageAlarm": 1, "x.bigpanda.vendor_surprise": 2},
    }
    # the counter is a pure graph projection — recomputing it gives the same answer
    assert discovery_counters(eng.graph) == bundle["discovery"]


def test_clean_run_has_empty_discovery_counters():
    subject, script, fixtures = scenario_nochange.build()
    bundle = export_bundle(run(subject, script, fixtures))
    assert bundle["discovery"] == {"class_hints": {}, "quarantined_names": {}}


def test_no_auto_promotion():
    """Counting NEVER mutates the vocabulary: after a run full of quarantined names and
    class_hints, the dictionary and the node registry are exactly what they were."""
    from iw_engine.domain.dictionary import DICTIONARY
    from iw_engine.domain.nodes import NODE_SPECS

    before_names = set(DICTIONARY)
    before_types = set(NODE_SPECS)
    test_bundle_counts_class_hints_and_quarantined_names()
    assert set(DICTIONARY) == before_names
    assert set(NODE_SPECS) == before_types
