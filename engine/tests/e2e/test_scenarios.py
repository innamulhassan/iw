"""The scenario registry — every use case runnable interactively, the write-gate live.

Guards the server backend the workbench drives (runtime/scenarios.py): the catalog lists all
six layers, and each incident starts an interactive session that suspends at the REMEDIATE
write-gate (Approve / Refine / Deny) and resolves on approval — the UI-SPEC §1/§2 contract.
"""
from __future__ import annotations

import pytest

from iw_engine.domain.enums import Source
from iw_engine.domain.subject import SubjectRef
from iw_engine.runtime.scenarios import build_manager, catalog
from iw_engine.runtime.session import GateDecision, SessionState

CATALOG_IDS = {"INC-4821", "INC-7731", "INC-9001", "INC-7734", "INC-7702", "INC-9100",
               "INC-8801", "INC-8900", "INC-5500", "INC-5600", "INC-5700"}


def test_catalog_lists_every_layer():
    incidents = catalog()
    assert {e["id"] for e in incidents} == CATALOG_IDS
    layers = {e["layer"] for e in incidents}
    assert layers == {"Application code", "Deployment", "Network", "Database",
                      "Firewall / Security", "No-change / Saturation", "Messaging", "Infra",
                      "Caching", "Configuration / Flag", "TLS / Certificate"}
    for e in incidents:
        assert e["id"] and e["title"] and e["layer"] and e["domain"] == "app-incident"


@pytest.mark.parametrize("incident_id", sorted(CATALOG_IDS))
def test_incident_opens_a_write_gate_and_resolves_on_approve(incident_id):
    mgr = build_manager()
    session = mgr.create(SubjectRef(domain="app-incident", id=incident_id, kind="incident"))

    # the interactive run pauses at the human-in-the-loop REMEDIATE write-gate
    assert session.state == SessionState.SUSPENDED
    gate = session.pending_gate
    assert gate is not None and gate["actions"], "expected a proposed remediation action"
    assert gate["actions"][0]["effect"] == "write"
    assert gate["hypothesis"] is not None, "the gate carries the serving hypothesis + evidence"

    # approving the write drives remediate → verify → close
    session.answer_gate(GateDecision.APPROVE)
    assert session.state == SessionState.CLOSED
    assert session.outcome in {"resolved", "mitigated"}


def test_gate_decision_is_journaled_with_approver_and_tool_sequence():
    """DEPTH — deep journal: the event stream carries per-phase reasoning + one distinct
    capability_call per tool call in call order, and answering the write-gate records WHO
    decided (actor + decision + Source.HUMAN) both on the stream and in the durable journal."""
    mgr = build_manager()
    session = mgr.create(SubjectRef(domain="app-incident", id="INC-7734", kind="incident"))
    assert session.state == SessionState.SUSPENDED

    events = session.events()
    assert any(e["type"] == "reasoning" for e in events), "per-phase reasoning is streamed"
    # each capability call is its own event, in call order — FRAME issues find_recent_changes
    # THEN active_alerts (the tool-call sequence, not one lumped event)
    caps = [e["intent"] for e in events if e["type"] == "capability_call"]
    assert caps[:2] == ["find_recent_changes", "active_alerts"]
    assert "list_related_incidents" in caps   # the related-incident prior was pulled

    # approve names the human; it is journaled as a HUMAN step + a gate_decision event
    resume = session.answer_gate(GateDecision.APPROVE, actor="alice@oncall", reason="ship it")
    assert session.state == SessionState.CLOSED
    decisions = [e for e in resume if e["type"] == "gate_decision"]
    assert decisions and decisions[0]["actor"] == "alice@oncall"
    assert decisions[0]["source"] == "human" and decisions[0]["decision"] == "approve"

    steps = session._engine.journal.step_entries()
    assert steps and steps[-1].actor == "alice@oncall"
    assert steps[-1].source == Source.HUMAN and steps[-1].decision == "approve"

    # the exported journal surfaces the human decision alongside the phase narratives
    human = [j for j in session.snapshot()["journal"]
             if j.get("kind") == "step" and j.get("source") == "human"]
    assert human and human[0]["actor"] == "alice@oncall" and human[0]["decision"] == "approve"


def test_unknown_incident_is_rejected():
    with pytest.raises(KeyError):
        build_manager().create(SubjectRef(domain="app-incident", id="INC-NOPE", kind="incident"))
