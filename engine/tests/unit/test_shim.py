"""P1a compat shim (build-spec step 2) — AddFact/AddEvent → AddAssertion mapping. Proves the
field mapping + the §9.1 species classifier. The reducer wiring (step 3) is tested separately;
here we assert the pure op→op conversion so a misclassification is caught at the atom boundary."""
from __future__ import annotations

from datetime import UTC, datetime

from iw_engine.domain.common import EvidenceRef
from iw_engine.domain.enums import ConfidenceLevel, Source, Species
from iw_engine.domain.operations import AddEvent, AddFact
from iw_engine.domain.shim import (
    assertion_from_event,
    assertion_from_fact,
    species_for_predicate,
)

T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 19, 15, 0, tzinfo=UTC)


# ── the §9.1 boundary test ────────────────────────────────────────────────────
def test_species_state_is_the_default():
    assert species_for_predicate("degraded") is Species.STATE
    assert species_for_predicate("red_errors") is Species.STATE
    assert species_for_predicate("slo_target") is Species.STATE
    assert species_for_predicate("some_unknown_metric") is Species.STATE


def test_species_descriptor_for_content_and_identity_adjacent():
    assert species_for_predicate("repo") is Species.DESCRIPTOR
    assert species_for_predicate("diff_summary") is Species.DESCRIPTOR
    assert species_for_predicate("status_code_dist") is Species.DESCRIPTOR
    assert species_for_predicate("node_name") is Species.DESCRIPTOR


def test_species_reading_when_reading_shaped():
    assert species_for_predicate("red_errors", has_reading_shape=True) is Species.READING


# ── AddFact → AddAssertion ────────────────────────────────────────────────────
def test_measured_fact_maps_to_state_assertion():
    op = AddFact(subject="service:pay", predicate="red_errors", value=0.4, unit="ratio",
                 valid_from=T0, valid_to=T1, observed_at=T0, source=Source.PROMETHEUS,
                 source_reliability=0.95,
                 evidence=[EvidenceRef(kind="metric_query", ref="q1")])
    a = assertion_from_fact(op)
    assert a.subject == "service:pay" and a.name == "red_errors"
    assert a.value == 0.4 and a.unit == "ratio"
    assert a.species is Species.STATE
    assert a.valid_from == T0 and a.valid_to == T1 and a.observed_at == T0
    assert a.occurred_at is None
    assert a.source is Source.PROMETHEUS and a.source_reliability == 0.95
    assert a.confidence_level is None
    assert a.evidence[0].ref == "q1"


def test_inferred_fact_carries_confidence_level_unresolved():
    op = AddFact(subject="service:pay", predicate="root_hint", value="bad deploy",
                 valid_from=T0, observed_at=T0, source=Source.LLM,
                 confidence_level=ConfidenceLevel.HIGH)
    a = assertion_from_fact(op)
    assert a.source is Source.LLM
    assert a.confidence_level is ConfidenceLevel.HIGH
    assert a.source_reliability is None


def test_content_fact_maps_to_descriptor():
    op = AddFact(subject="code_commit:abc", predicate="diff_summary", value={"files": 3},
                 valid_from=T0, observed_at=T0, source=Source.GIT, source_reliability=0.9)
    a = assertion_from_fact(op)
    assert a.species is Species.DESCRIPTOR
    assert a.value == {"files": 3}


# ── AddEvent → AddAssertion ───────────────────────────────────────────────────
def test_event_maps_to_event_assertion():
    op = AddEvent(entity="deployment:web", type="rollout_complete", occurred_at=T0,
                  observed_at=T1, payload={"image": "v2"}, source=Source.OCP)
    a = assertion_from_event(op)
    assert a.subject == "deployment:web" and a.name == "rollout_complete"
    assert a.species is Species.EVENT
    assert a.value == {"image": "v2"}
    assert a.occurred_at == T0 and a.observed_at == T1
    assert a.source is Source.OCP
    assert a.source_reliability is None      # reducer defaults it via INV-9
