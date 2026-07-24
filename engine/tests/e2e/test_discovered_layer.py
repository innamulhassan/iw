"""DISCOVERED, not assumed, layer (owner 2026-07-24): the fault category is an OUTPUT of the
investigation, not a pre-label. `bundle.discovered_layer` is null UNTIL a hypothesis is CONFIRMED,
then the layer NAME is EARNED from the confirmed hypothesis's ROOT node TYPE via an explicit
node-type -> layer map — never read from the catalog's pre-assigned layer.

Covered here:
  * null MID-RUN (nothing confirmed yet), then the derived layer once the root is CONFIRMED
  * INC-4821's confirmed root code_commit:abc123 -> "Application code"
  * the node-type -> layer map derives each class (and falls through to a de-cased type)
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime

from e2e import scenario_code_regression as cr
from e2e._helpers import run

import iw_engine
from iw_engine.api.bundle import _layer_for_node_type, discovered_layer, export_bundle
from iw_engine.domain.enums import NodeType as NT
from iw_engine.runtime import ScriptedPlanner, load_playbook
from iw_engine.runtime.session import InvestigationSession, ReviewDecision, SessionState

PLAYBOOK = pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"


def _clock() -> datetime:
    return datetime(2026, 7, 19, tzinfo=UTC)


# ── null mid-run, then the derived layer once the root is confirmed ─────────────────────
def test_discovered_layer_null_midrun_then_derived_from_confirmed_root():
    subject, script = cr.build()
    pb = load_playbook(PLAYBOOK)
    session = InvestigationSession(subject, pb, ScriptedPlanner(script),
                                   clock=_clock, auto_review=False)

    session.advance()                       # runs FRAME, pauses at the frame->investigate review
    assert session.state == SessionState.AWAITING_REVIEW
    # MID-RUN: no hypothesis is confirmed yet — the layer is not assumed from the catalog.
    assert session.snapshot()["discovered_layer"] is None

    # approve straight through to a resolved close (h1 is CONFIRMED in verify)
    while session.state == SessionState.AWAITING_REVIEW:
        session.answer_review(ReviewDecision.APPROVE)
    assert session.state == SessionState.CLOSED and session.outcome == "resolved"

    # EARNED: the confirmed root is code_commit:abc123 -> "Application code".
    assert session.snapshot()["discovered_layer"] == "Application code"


# ── the batch bundle carries the same earned layer for INC-4821 ─────────────────────────
def test_bundle_discovered_layer_is_application_code_for_inc_4821():
    subject, script = cr.build()
    bundle = export_bundle(run(subject, script, None))
    assert bundle["outcome"] == "resolved"
    assert bundle["discovered_layer"] == "Application code"


# ── the layer is DERIVED from the root node type, never read as a pre-label ─────────────
def test_discovered_layer_is_null_without_a_confirmed_hypothesis():
    # a run that never confirms (the refuted variant ends BLOCKED with no CONFIRMED hypothesis)
    subject, script = cr.build(refuted_variant=True)
    res = run(subject, script, None)
    assert res.hypothesis_store.confirmed() is None
    assert discovered_layer(res) is None
    assert export_bundle(res)["discovered_layer"] is None


# ── the map derives each fault class; an unmapped type falls through to a de-cased value ─
def test_node_type_layer_map_derives_and_falls_through():
    assert _layer_for_node_type(NT.CODE_COMMIT) == "Application code"
    assert _layer_for_node_type(NT.ERROR_SIGNATURE) == "Application code"
    assert _layer_for_node_type(NT.CHANGE_EVENT) == "Change/Deployment"
    assert _layer_for_node_type(NT.DATABASE) == "Database"
    assert _layer_for_node_type(NT.NETWORK_SEGMENT) == "Network"
    assert _layer_for_node_type(NT.FIREWALL_RULE) == "Firewall / Security"
    assert _layer_for_node_type(NT.CACHE) == "Caching"
    assert _layer_for_node_type(NT.FEATURE_FLAG) == "Configuration / Flag"
    assert _layer_for_node_type(NT.CERTIFICATE) == "TLS / Certificate"
    assert _layer_for_node_type(NT.HOST) == "Infra"
    # an unmapped type de-cases its enum value (underscores -> spaces, sentence case)
    assert _layer_for_node_type(NT.MESSAGE_QUEUE) == "Message queue"
