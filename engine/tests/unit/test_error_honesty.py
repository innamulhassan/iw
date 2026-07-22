"""P3 airlock step 1 — the engine CONSUMES the capability boundary outcome
(domain-model-v3-design §2.4 row 3 / part4-capability §4).

Three distinguishable outcomes at the tool boundary, each with distinct downstream semantics:
  data        → ops fold (unchanged);
  clean-empty → an honest no-data read: CAN become NoEvidence (R-P2);
  error       → NO evidentiary weight: must NEVER feed the nochange/refutation path.

These tests pin the convergence: an `error` invocation cannot become refuting evidence while a
clean-empty still can; both are journaled DISTINCTLY (`kind="invocation"`); and the session's
`capability_call` event carries `outcome` so no downstream reader infers "clean" from
op_count == 0 alone (the silent-empty poison).
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime

from e2e import scenario_nochange
from e2e._helpers import call, fact, no_evidence, node, phase

import iw_engine
from iw_engine.capability import CapabilityLayer, MockSource
from iw_engine.capability.adapters import default_adapters
from iw_engine.domain.enums import Binding, NodeType, Source
from iw_engine.runtime import Engine, ScriptedPlanner, load_playbook
from iw_engine.runtime.session import InvestigationSession

PLAYBOOK = pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"
T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
ANOM = "anomaly:anom-1"


def _clock() -> datetime:
    return datetime(2026, 7, 19, tzinfo=UTC)


class _ErrorSource:
    """Transport twin of a vendor 4xx/5xx/timeout: every fetch raises. serve() degrades it to a
    recorded `error` Invocation (never a crash) — the shape under test."""

    def fetch(self, binding: Binding, intent: str, params: dict) -> dict:
        raise RuntimeError("vendor 503")


def _frame_plan(*, with_no_evidence: bool = True):
    ops = [
        node(NodeType.ANOMALY, anomaly_id="ANOM-1"),
        fact(ANOM, "onset_value", 42, T0, source=Source.PROMETHEUS),
    ]
    if with_no_evidence:
        ops.append(no_evidence("find_recent_changes", ANOM, T0,
                               basis="change log came back clean"))
    return phase("frame", calls=[call("find_recent_changes")], ops=ops,
                 narrative="frame with a change-log read")


def _step_once(source) -> Engine:
    pb = load_playbook(PLAYBOOK)
    layer = CapabilityLayer(default_adapters(), source=source)
    eng = Engine(pb, ScriptedPlanner([_frame_plan()]), clock=_clock, layer=layer)
    eng.start(scenario_nochange.build()[0])
    eng.step()
    return eng


# ── error: no evidentiary weight — the NoEvidence path is barred ───────────────
def test_error_invocation_cannot_become_no_evidence():
    eng = _step_once(_ErrorSource())

    inv = eng.invocations[0]
    assert inv.outcome == "error" and not inv.blocked

    # the honest-null fact was NOT fabricated from a failed read…
    assert not any(f.predicate == "no_evidence:find_recent_changes"
                   for f in eng.graph.facts.values())
    # …it was REJECTED, with the reason on record (feeds the planner, never silence)
    rej = [r for r in eng.rejections if r.op_kind == "no_evidence"]
    assert len(rej) == 1
    assert "no evidentiary weight" in rej[0].reason

    # journaled distinctly: a kind="invocation" entry keyed decision="error"
    entries = [e for e in eng.journal.entries if e.kind == "invocation"]
    assert [(e.intent, e.decision) for e in entries] == [("find_recent_changes", "error")]
    assert entries[0].observation["outcome"] == "error"
    assert "vendor 503" in entries[0].observation["reason"]


# ── clean-empty: an honest no-data read — NoEvidence still lands (R-P2) ────────
def test_clean_empty_invocation_still_feeds_no_evidence():
    eng = _step_once(MockSource({"find_recent_changes": {"changes": []}}))

    inv = eng.invocations[0]
    assert inv.outcome == "empty" and not inv.blocked

    nulls = [f for f in eng.graph.facts.values()
             if f.predicate == "no_evidence:find_recent_changes"]
    assert len(nulls) == 1 and nulls[0].subject_ref == ANOM
    assert eng.rejections == []

    # journaled distinctly from an error: decision="empty"
    entries = [e for e in eng.journal.entries if e.kind == "invocation"]
    assert [(e.intent, e.decision) for e in entries] == [("find_recent_changes", "empty")]


def test_error_bar_carries_across_phases_until_a_clean_call():
    """The bar is keyed on the LAST outcome per intent: an intent that errored in an earlier
    phase still cannot become NoEvidence later — but a subsequent clean call clears it."""
    pb = load_playbook(PLAYBOOK)
    script = [
        phase("frame", calls=[call("find_recent_changes")], ops=[
            node(NodeType.ANOMALY, anomaly_id="ANOM-1"),
            fact(ANOM, "onset_value", 42, T0, source=Source.PROMETHEUS),
        ], narrative="errored read"),
        phase("investigate", ops=[
            fact(ANOM, "severity_score", 2, T0, source=Source.SERVICENOW),
            no_evidence("find_recent_changes", ANOM, T0, basis="claimed clean"),
        ], narrative="tries to cash the errored read in as null evidence"),
    ]

    class _FlakySource:
        def fetch(self, binding: Binding, intent: str, params: dict) -> dict:
            raise RuntimeError("boom")

    eng = Engine(pb, ScriptedPlanner(script), clock=_clock,
                 layer=CapabilityLayer(default_adapters(), source=_FlakySource()))
    eng.start(scenario_nochange.build()[0])
    eng.step()      # frame: the read errors
    eng.step()      # investigate: the NoEvidence naming it must be rejected
    assert not any(f.predicate.startswith("no_evidence:") for f in eng.graph.facts.values())
    assert any("no evidentiary weight" in r.reason for r in eng.rejections)


# ── the session event stream surfaces the outcome ──────────────────────────────
def test_session_capability_call_event_carries_outcome():
    subject, script, fixtures = scenario_nochange.build()
    layer = CapabilityLayer(default_adapters(), source=MockSource(fixtures))
    session = InvestigationSession(subject, load_playbook(PLAYBOOK), ScriptedPlanner(script),
                                   layer=layer, clock=_clock)
    session.advance()

    calls = [e for e in session.events() if e["type"] == "capability_call"]
    assert calls and all("outcome" in e for e in calls)
    by_intent = {e["intent"]: e["outcome"] for e in calls}
    assert by_intent["find_recent_changes"] == "empty"    # the no-change class, honestly empty
    assert by_intent["active_alerts"] == "data"


# ── golden protection: invocation entries stay out of the bundle journal view ──
def test_invocation_entries_do_not_change_the_bundle_journal():
    from e2e._helpers import run

    from iw_engine.api.bundle import export_bundle

    subject, script, fixtures = scenario_nochange.build()
    res = run(subject, script, fixtures)
    # the clean-empty find_recent_changes call IS journaled…
    assert any(e.kind == "invocation" and e.decision == "empty" for e in res.journal.entries)
    # …but the bundle's journal keeps its phase/step shape (scripted happy-path preserved)
    bundle = export_bundle(res)
    assert all(j.get("kind") != "invocation" for j in bundle["journal"])
