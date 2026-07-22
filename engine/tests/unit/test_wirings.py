"""P6 convergence wirings (the small deferred items from the P5/doctrine lane):

1. live_planner parses `merge`/`retype` — P5's identity graduations were doctrine-advertised
   but the live parser silently DROPPED them (the "live parser drops it" bug class).
2. session._is_write_call keys on `layer.effect_for(adapter, intent)` — PER-INTENT
   (part4-capability §1), so a mixed adapter's write intent gates and its reads don't.
(3. the mapping-params S1.5 fix is tested in test_mapping.py.)
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime
from typing import ClassVar

from e2e import scenario_code_regression as s1
from e2e._helpers import call

import iw_engine
from iw_engine.capability import CapabilityLayer
from iw_engine.capability.adapters import default_adapters
from iw_engine.domain.enums import Binding, Effect, NodeType, Source
from iw_engine.domain.operations import AddEvent, Merge, Operation, Retype
from iw_engine.runtime import ScriptedPlanner, load_playbook
from iw_engine.runtime.live_planner import LivePlanner
from iw_engine.runtime.session import InvestigationSession, SessionState

PLAYBOOK = pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"


def _lp() -> LivePlanner:
    return LivePlanner(client=None, catalog_text="", tools_text="", tool_intents=set(),
                       verbose=False)


# ── 1. merge/retype reach the live grammar ─────────────────────────────────────
def test_live_parser_accepts_merge():
    op, err = _lp()._parse_op({"op": "merge", "provisional_id": "service:pay~appd",
                               "canonical_id": "service:payments-api|prod",
                               "reason": "shared app_id binding"})
    assert err is None and isinstance(op, Merge)
    assert op.provisional_id == "service:pay~appd"
    assert op.canonical_id == "service:payments-api|prod"


def test_live_parser_accepts_retype():
    op, err = _lp()._parse_op({"op": "retype", "target": "generic_ci:ci-9",
                               "new_type": "message_queue",
                               "props": {"queue_name": "orders", "cluster": "kafka-1"},
                               "reason": "class_hint corroborated"})
    assert err is None and isinstance(op, Retype)
    assert op.new_type is NodeType.MESSAGE_QUEUE and op.props["queue_name"] == "orders"


def test_live_parser_repairs_bad_merge_and_retype():
    """Malformed payloads REPAIR (drop with a reason), never raise — INV-7."""
    op, err = _lp()._parse_op({"op": "merge", "provisional_id": "only-half"})
    assert op is None and err is not None
    op, err = _lp()._parse_op({"op": "retype", "target": "x", "new_type": "not_a_type"})
    assert op is None and err is not None


# ── 2. the write-gate keys on the PER-INTENT effect ────────────────────────────
class _MixedAdapter:
    """One adapter, mixed intents: a READ default with a per-intent WRITE override —
    exactly the ocp/ocp__restart shape part4 §1 folded together."""

    provider = "ocp"
    intents = frozenset({"mixed__status", "mixed__restart"})
    effect = Effect.READ
    effects: ClassVar[dict[str, Effect]] = {"mixed__restart": Effect.WRITE}  # per-intent override
    binding = Binding.MCP

    def normalize(self, raw: dict) -> list[Operation]:
        return [AddEvent(entity=s1.SVC, type="restarted",
                         occurred_at=s1.T_FIX, observed_at=s1.T_FIX, source=Source.OCP)]


class _EchoMock:
    def fetch(self, binding: Binding, intent: str, params: dict) -> dict:
        return dict(params)


def _session_with(intent: str) -> InvestigationSession:
    subject, script = s1.build()
    script[3] = script[3].model_copy(update={"calls": [call(intent)]})
    layer = CapabilityLayer([*default_adapters(), _MixedAdapter()], source=_EchoMock())
    pb = load_playbook(PLAYBOOK)
    return InvestigationSession(subject, pb, ScriptedPlanner(script), layer=layer,
                                clock=lambda: datetime(2026, 7, 19, tzinfo=UTC))


def test_per_intent_write_gates_but_read_does_not():
    writer = _session_with("mixed__restart")
    writer.advance()
    assert writer.state == SessionState.SUSPENDED, \
        "the WRITE-effect intent must open the human gate"
    assert writer.pending_gate["actions"][0]["intent"] == "mixed__restart"

    reader = _session_with("mixed__status")
    reader.advance()
    assert reader.state == SessionState.CLOSED, \
        "a READ intent on the SAME adapter must never gate (per-intent, not per-adapter)"


# ── P7 step 5: the subject/origin node is a PLAYBOOK role binding, not a convention ──
def test_subject_node_binding_drives_the_origin_id():
    from iw_engine.domain.registry import subject_node_id

    # the packaged binding reproduces the old incident convention EXACTLY (goldens hold)
    assert subject_node_id(NodeType.INCIDENT, "INC-7734") == "incident:inc-7734"
    # …but it is DATA: a playbook binding its subject to another type gets that type's
    # identity — no engine edit, no incident-ism
    assert subject_node_id(NodeType.ALERT, "ALT-9") == "alert:alt-9"

    pb = load_playbook(PLAYBOOK)
    assert pb.subject_node is NodeType.INCIDENT      # declared in incident.yaml
    subject, script, fixtures = __import__("e2e.scenario_database", fromlist=["build"]).build()
    from iw_engine.capability import MockSource
    from iw_engine.runtime import Engine
    layer = CapabilityLayer(default_adapters(), source=MockSource(fixtures))
    res = Engine(pb, ScriptedPlanner(script), layer=layer,
                 clock=lambda: datetime(2026, 7, 19, tzinfo=UTC)).run(subject)
    assert res.origin_node == "incident:inc-7734"
    from iw_engine.api.bundle import export_bundle
    flags = {n["id"]: n["origin"] for n in export_bundle(res)["graph"]["nodes"]}
    assert flags["incident:inc-7734"] is True
    assert sum(flags.values()) == 1                  # exactly ONE origin node
