"""P1 · domain model — unit tests.

Two jobs: (1) every INC-4821 design fixture parses with extra="forbid" — i.e. the models are
FIELD-COMPLETE and faithful to ../../design/v2/04-data-model.html; (2) the design's rules are
enforced (confidence is {value,basis}, revert_when enum, tool_call needs a capability, needs are
intents not tool names, no SLO, no bare incident_id).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from engine.domain import (
    OUTPUT_TYPES,
    Access,
    Action,
    AssessResult,
    CapabilityPolicy,
    Candidate,  # noqa: F401 (kept for symmetry / future use)
    Confidence,
    DeclaredCapability,
    Edge,
    Effect,
    Fact,
    Feedback,
    ImpactAssessment,
    ImpactState,
    Node,
    PhaseRecord,
    Playbook,
    PolicyStatus,
    Provider,
    RemediationResult,
    RootCauseResult,
    Step,
    SubjectRef,
    VerifyResult,
)
from fixtures import inc4821 as fx


# ── every design fixture parses (field-completeness vs the design) ──────
def test_subject_ref_parses_and_key():
    s = SubjectRef.model_validate(fx.SUBJECT)
    assert s.key == ("app-incident", "INC-4821")
    assert s.kind == "incident"


def test_fact_parses_with_impact_state_enum():
    f = Fact.model_validate(fx.FACT)
    assert f.impact_state is ImpactState.degraded
    assert f.confidence == 0.9


def test_node_parses_full():
    n = Node.model_validate(fx.NODE)
    assert n.id == "app:payments-api"
    assert n.layer == "app"
    assert "suspect" in n.labels
    assert n.facts[0].source == "appd"


def test_edge_from_alias_roundtrips():
    e = Edge.model_validate(fx.EDGE)
    assert e.from_ == "INC-4821"
    assert e.to == "stor:pay-vol"
    assert e.model_dump(by_alias=True)["from"] == "INC-4821"
    assert e.props["path"][-1] == "stor:pay-vol"


def test_phase_record_and_steps_parse():
    pr = PhaseRecord.model_validate(fx.PHASE_RECORD)
    assert pr.subject.id == "INC-4821"
    assert len(pr.steps) == 2
    assert pr.steps[1].capability == "traces"
    assert pr.steps[1].touched == ["svc:checkout", "db:payments-ora"]


def test_assess_result_parses():
    a = AssessResult.model_validate(fx.ASSESS_RESULT)
    assert a.incident_type.value == "performance"
    assert a.time_factor is None          # null = checked & none (valid)
    assert a.changed == ["chg:deploy-rev47"]
    assert a.cluster is None
    assert a.suggestions[0].confidence == 0.7


def test_root_cause_confidence_is_value_basis():
    rc = RootCauseResult.model_validate(fx.ROOT_CAUSE_RESULT)
    assert isinstance(rc.candidates[0].confidence, Confidence)
    assert rc.candidates[0].confidence.value == 0.9
    assert "INC-4820" in rc.candidates[0].confidence.basis
    assert rc.selected == 0
    assert rc.ruled_out[0].hyp == "rev47 deploy"


def test_remediation_action_parses():
    r = RemediationResult.model_validate(fx.REMEDIATION_RESULT)
    a = r.actions[0]
    assert a.kind.value == "mitigate"
    assert a.temporary is True
    assert a.revert_when == "incident_close"
    assert a.idempotency_key == "INC-4821-a1"
    assert r.followups[0].detail.startswith("replace disk")


def test_verify_result_parses():
    v = VerifyResult.model_validate(fx.VERIFY_RESULT)
    assert v.recovered is True
    assert v.before_after == "p99 4.2s→260ms, errors 18%→0.2%"
    assert v.temporary_actions_status[0].status == "scheduled"


def test_feedback_id_alias_and_kind():
    fb = Feedback.model_validate(fx.FEEDBACK)
    assert fb.id == "fb-9912"             # _id → id
    assert fb.kind.value == "outcome"
    assert fb.subject.domain == "app-incident"


def test_playbook_parses_and_pk():
    pb = Playbook.model_validate(fx.PLAYBOOK)
    assert pb.pk == ("incident-triage", "1.0.0")
    assert [p.id for p in pb.phases] == ["assess", "root-cause", "remediation", "verify-close"]
    rc = next(p for p in pb.phases if p.id == "root-cause")
    assert rc.min_confidence == 0.7
    rem = next(p for p in pb.phases if p.id == "remediation")
    assert rem.gate_writes is True
    assert rem.effect.value == "write"
    assert pb.defaults.retry.max == 3


def test_registry_parses_and_pending_default():
    Provider.model_validate(fx.PROVIDER)
    dc = DeclaredCapability.model_validate(fx.DECLARED_CAPABILITY)
    assert dc.effect_hint is Effect.read
    pol = CapabilityPolicy.model_validate(fx.CAPABILITY_POLICY)
    assert pol.access is Access.ask
    fresh = CapabilityPolicy(capability_id="x__y", effect=Effect.write, access=Access.deny)
    assert fresh.status is PolicyStatus.pending_review   # a NEW capability lands here


def test_output_types_registry_complete():
    assert set(OUTPUT_TYPES) == {
        "AssessResult", "RootCauseResult", "RemediationResult", "VerifyResult",
    }
    for name, model in OUTPUT_TYPES.items():
        assert model.__name__ == name


# ── guards / negative tests ─────────────────────────────────────────────
def test_extra_field_is_rejected():
    with pytest.raises(ValidationError):
        SubjectRef.model_validate(dict(fx.SUBJECT, surprise="nope"))


def test_bad_impact_state_rejected():
    with pytest.raises(ValidationError):
        Fact.model_validate(dict(fx.FACT, impact_state="bananas"))


def test_confidence_must_be_value_basis_not_bare_float():
    bad_cand = dict(fx.ROOT_CAUSE_RESULT["candidates"][0], confidence=0.9)
    with pytest.raises(ValidationError):
        RootCauseResult.model_validate({**fx.ROOT_CAUSE_RESULT, "candidates": [bad_cand]})


def test_tool_call_step_requires_capability():
    with pytest.raises(ValidationError):
        Step(seq=9, kind="tool_call")          # no capability


def test_reasoning_step_needs_no_capability():
    s = Step(seq=1, kind="reasoning", note="thinking")
    assert s.capability is None


def test_revert_when_enum_enforced():
    base = dict(fx.REMEDIATION_RESULT["actions"][0])
    assert Action.model_validate(dict(base, revert_when="action:a1")).revert_when == "action:a1"
    with pytest.raises(ValidationError):
        Action.model_validate(dict(base, revert_when="whenever"))


def test_playbook_rejects_tool_name_in_needs():
    bad_phase = dict(fx.PLAYBOOK["phases"][0], needs=["topology", "appd__get_health"])
    bad = {**fx.PLAYBOOK, "phases": [bad_phase, *fx.PLAYBOOK["phases"][1:]]}
    with pytest.raises(ValidationError):
        Playbook.model_validate(bad)


def test_no_slo_field_anywhere():
    # v2 removed SLO/burn_rate — impact is stated in business terms. Guard against regression.
    assert "slo" not in AssessResult.model_fields
    assert "slo_status" not in AssessResult.model_fields
    assert "burn_rate" not in ImpactAssessment.model_fields


def test_no_bare_incident_id_on_universal_entities():
    for model in (Feedback, PhaseRecord):
        assert "incident_id" not in model.model_fields
        assert "subject" in model.model_fields


# ── round-trip (dump → re-validate equals original) ─────────────────────
@pytest.mark.parametrize(
    "model, fixture_name",
    [
        (Node, "NODE"), (Edge, "EDGE"), (AssessResult, "ASSESS_RESULT"),
        (RootCauseResult, "ROOT_CAUSE_RESULT"), (RemediationResult, "REMEDIATION_RESULT"),
        (VerifyResult, "VERIFY_RESULT"), (PhaseRecord, "PHASE_RECORD"), (Playbook, "PLAYBOOK"),
        (Feedback, "FEEDBACK"),
    ],
)
def test_roundtrip(model, fixture_name):
    raw = getattr(fx, fixture_name)
    obj = model.model_validate(raw)
    again = model.model_validate(obj.model_dump(by_alias=True))
    assert again == obj
