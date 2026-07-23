"""SERVE the journal whole (B2). export_bundle's journal projection now serves EVERY kind with
its kind + ts + full per-kind fields — no kind dropped, no detail collapsed. The owner's CLEAN +
COMPOSABLE rule: UI, audit and the fold read the ONE record without special-casing.
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime

from e2e import scenario_code_regression as cr
from e2e import scenario_deployment as dep
from e2e._helpers import call, run

import iw_engine
from iw_engine.api.bundle import export_bundle
from iw_engine.runtime import ScriptedPlanner, load_playbook
from iw_engine.runtime.session import GateDecision, InvestigationSession

PLAYBOOK = pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"


def _deployment_bundle() -> dict:
    subject, script, fixtures = dep.build()
    return export_bundle(run(subject, script, fixtures))


# ── CLEAN: every served entry carries its kind AND its ts ──────────────────────────────
def test_every_served_entry_carries_kind_and_ts():
    view = _deployment_bundle()["journal"]
    assert view, "the journal view is non-empty"
    assert all(j.get("kind") for j in view), "NO served entry is kind-less (the CLEAN rule)"
    assert all(j.get("ts") for j in view), "EVERY served entry carries a timestamp"
    kinds = {j["kind"] for j in view}
    assert {"phase", "plan", "invocation"} <= kinds, "phase + plan + invocation are all served"
    # the whole journal is served in non-decreasing seq order (annotations share their phase seq)
    seqs = [j["seq"] for j in view]
    assert seqs == sorted(seqs)


# ── the PHASE entry serves goal + verdict + next_actions (were buried in the delta) ─────
def test_phase_entry_serves_goal_and_verdict():
    view = _deployment_bundle()["journal"]
    phases = [j for j in view if j["kind"] == "phase"]
    assert len(phases) == 6
    for j in phases:
        assert j["goal"], "the phase GOAL is served (was only inside the raw delta)"
        assert j["verdict"], "the phase VERDICT is served"
        assert "next_actions" in j and "refs" in j and j.get("narrative")


# ── the PLAN entry serves the tools-available + the authored plan ───────────────────────
def test_plan_entry_serves_tools_available_and_plan():
    view = _deployment_bundle()["journal"]
    plans = [j for j in view if j["kind"] == "plan"]
    assert len(plans) == 6
    frame = next(j for j in plans if j["phase"] == "frame")
    assert frame["available"] and "get_dependencies" in frame["available"]   # what it COULD call
    assert "get_dependencies" in frame["plan_calls"]                          # what it DECIDED
    assert isinstance(frame["plan_ops"], list)


# ── the INVOCATION entry serves intent/provider/outcome/op_count/WHY, in full ───────────
def test_invocation_entry_serves_full_detail_incl_why():
    view = _deployment_bundle()["journal"]
    invs = [j for j in view if j["kind"] == "invocation"]
    assert len(invs) == 9
    for j in invs:
        assert j["intent"] and j["provider"]                 # WHAT + WHO
        assert j["outcome"] and "op_count" in j              # the boundary outcome + volume
        assert j.get("narrative"), "the WHY each tool was called is served"
        assert "effect" in j and "params" in j and "blocked" in j


# ── F1: the served plan carries the to-do CHECKLIST; invocations carry their to-do index ──
def test_plan_entry_serves_todo_checklist_and_invocations_carry_todo():
    view = _deployment_bundle()["journal"]
    plans = [j for j in view if j["kind"] == "plan"]
    frame = next(j for j in plans if j["phase"] == "frame")
    assert frame["todos"], "the plan serves its to-do checklist (F1)"
    td = frame["todos"][0]
    assert {"objective", "calls", "ops", "status"} <= set(td) and td["status"] == "pending"
    # the served checklist flattens back to the served plan_calls/plan_ops
    assert [c for t in frame["todos"] for c in t["calls"]] == frame["plan_calls"]
    # every served invocation carries its to-do attribution
    invs = [j for j in view if j["kind"] == "invocation"]
    assert invs and all("todo" in j and j["todo"] is not None for j in invs), \
        "each served invocation carries the to-do it served (F1)"


# ── a scripted-direct-ops run serves its plan (visible) with zero invocations (honest) ──
def test_scripted_direct_ops_bundle_serves_plan_without_invocations():
    # a minimal DIRECT-ops plan (no calls, no fixtures) — the flagship twin now authors reasoned
    # calls, so the layer-less direct-ops path is exercised inline, decoupled from a scenario.
    from e2e._helpers import fact, nid, node, phase

    from iw_engine.domain.enums import NodeType
    from iw_engine.domain.subject import SubjectRef

    subject = SubjectRef(domain="app-incident", id="INC-DIRECT", kind="incident")
    anom = nid(NodeType.ANOMALY, anomaly_id="ANOM-1")
    at = datetime(2026, 7, 19, tzinfo=UTC)
    script = [phase("frame", [node(NodeType.ANOMALY, anomaly_id="ANOM-1"),
                              fact(anom, "onset_value", 0.40, at)],
                    "framed from direct ops", status="advance"),
              phase("investigate", [], "nothing further", status="blocked")]
    view = export_bundle(run(subject, script, None))["journal"]
    assert not [j for j in view if j["kind"] == "invocation"], "no calls made — none served"
    plans = [j for j in view if j["kind"] == "plan"]
    frame = next(j for j in plans if j["phase"] == "frame")
    assert frame["plan_calls"] == [] and frame["plan_ops"], "the direct-ops plan is visible"


# ── a gated session serves the write-GATE question + the human DECISION, whole ──────────
def test_bundle_serves_gate_opened_and_decision():
    from unit.test_session import _layer

    def _clock() -> datetime:
        return datetime(2026, 7, 19, tzinfo=UTC)

    subject, script = cr.build()
    script[3] = script[3].model_copy(
        update={"calls": [call("rollback_release", to_version="v4.11.3")]})
    pb = load_playbook(PLAYBOOK)
    session = InvestigationSession(subject, pb, ScriptedPlanner(script), layer=_layer(),
                                   clock=_clock)
    session.advance()
    session.answer_gate(GateDecision.APPROVE, actor="alice@oncall")
    view = export_bundle(session._engine.result())["journal"]

    opened = [j for j in view if j["kind"] == "gate_opened"]
    assert opened, "the write-gate OPENING is served (was excluded before)"
    assert opened[0]["actions"] and opened[0]["actions"][0]["intent"] == "rollback_release"
    assert opened[0]["hypothesis"] == "hyp:h1" and opened[0]["evidence"]

    decided = [j for j in view if j["kind"] == "gate_decision"]
    assert decided and decided[0]["decision"] == "approve"
    assert decided[0]["actor"] == "alice@oncall" and decided[0]["ts"]

    # the run lifecycle is served too — started at the front, closed at the terminal
    life = [j for j in view if j["kind"] == "lifecycle"]
    events = [j["event"] for j in life]
    assert events and events[0] == "started" and "resumed" in events and events[-1] == "closed"
