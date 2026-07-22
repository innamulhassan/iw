"""P3 airlock step 2 — journaled rejections (DOMAIN-v3 §2.4 row 3: "journaled in the delta,
surfaced in the bundle, fed back to the planner" — the R-K2 bounded-repair-loop promise).

A reducer rejection used to live only in Engine memory (`RunResult.rejections`): absent from
the journal, the bundle, and the next plan — the model saw silent nothing. These tests pin the
three surfaces: the PhaseResult delta carries rejections through the journal (replay-inert),
`export_bundle` lists them, and the NEXT PlanContext hands them to the planner.
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime

from e2e import scenario_nochange
from e2e._helpers import fact, node, phase

import iw_engine
from iw_engine.api.bundle import export_bundle
from iw_engine.domain.enums import NodeType, Source
from iw_engine.graph import rebuild
from iw_engine.journal import Journal
from iw_engine.runtime import Engine, ScriptedPlanner, load_playbook
from iw_engine.runtime.live_planner import LivePlanner
from iw_engine.runtime.planner import PlanContext

PLAYBOOK = pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"
T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
ANOM = "anomaly:anom-1"


def _clock() -> datetime:
    return datetime(2026, 7, 19, tzinfo=UTC)


def _script_with_bad_op():
    """FRAME emits one valid node+fact plus one fact on a subject that does not exist —
    the reducer drops it and must now leave a durable trace everywhere."""
    return [phase("frame", ops=[
        node(NodeType.ANOMALY, anomaly_id="ANOM-1"),
        fact(ANOM, "onset_value", 42, T0, source=Source.PROMETHEUS),
        fact("database:ghost|prod", "conn_pool_util", 0.99, T0, source=Source.PROMETHEUS),
    ], narrative="frame with one doomed op")]


def _run_one_phase(script) -> Engine:
    eng = Engine(load_playbook(PLAYBOOK), ScriptedPlanner(script), clock=_clock)
    eng.start(scenario_nochange.build()[0])
    eng.step()
    return eng


# ── journal: the rejection rides the delta, and replay is unaffected ──────────
def test_rejection_is_journaled_in_the_delta_and_replay_safe():
    eng = _run_one_phase(_script_with_bad_op())

    entry = eng.journal.phase_entries()[0]
    assert [(r.op_kind, r.reason) for r in entry.delta.rejections] == \
           [("add_assertion", "unknown subject database:ghost|prod")]

    # NDJSON roundtrip preserves it (the durable source of truth carries the WHY)
    replayed = Journal.from_ndjson(eng.journal.to_ndjson())
    assert replayed.phase_entries()[0].delta.rejections == entry.delta.rejections

    # replay-inert: a rejection mutates no projection — rebuild still equals the live graph
    g2, _ = rebuild(replayed)
    assert g2.to_dict() == eng.graph.to_dict()


# ── bundle: surfaced, derived from the journal ─────────────────────────────────
def test_rejections_surface_in_export_bundle():
    eng = _run_one_phase(_script_with_bad_op())
    bundle = export_bundle(eng.result())
    assert bundle["rejections"] == [{
        "seq": 1, "phase": "frame", "op_index": 2, "op_kind": "add_assertion",
        "reason": "unknown subject database:ghost|prod",
    }]


def test_clean_run_bundle_has_empty_rejections_key():
    subject, script, fixtures = scenario_nochange.build()
    from e2e._helpers import run
    bundle = export_bundle(run(subject, script, fixtures))
    assert bundle["rejections"] == []          # the key is a stable contract, empty when clean


# ── feedback: the NEXT plan is told what was dropped and why ───────────────────
def test_next_plan_context_carries_previous_rejections():
    class _Capture:
        def __init__(self, script):
            self._inner = ScriptedPlanner(script)
            self.seen: list[list] = []

        def plan(self, ctx):
            self.seen.append(list(ctx.rejections))
            return self._inner.plan(ctx)

    script = [*_script_with_bad_op(), phase("triage", ops=[
        fact(ANOM, "severity_score", 2, T0, source=Source.SERVICENOW),
    ], narrative="second phase")]
    planner = _Capture(script)
    eng = Engine(load_playbook(PLAYBOOK), planner, clock=_clock)
    eng.start(scenario_nochange.build()[0])
    eng.step()
    eng.step()

    assert planner.seen[0] == []                                   # nothing dropped yet
    assert [(r.op_kind, r.reason) for r in planner.seen[1]] == \
           [("add_assertion", "unknown subject database:ghost|prod")]


def test_live_planner_prompt_renders_dropped_ops():
    pb = load_playbook(PLAYBOOK)
    lp = LivePlanner(client=None, catalog_text="# CAT", tools_text="# TOOLS", tool_intents=set())
    spec = pb.phase(pb.entry_phase)
    ctx = PlanContext(subject=scenario_nochange.build()[0], phase=pb.entry_phase,
                      phase_spec=spec, goal=spec.goal, graph_view={}, tunables=pb.tunables)
    assert "OPS DROPPED LAST TURN" not in lp._build_prompt(ctx)     # absent when clean

    from iw_engine.domain.phase_result import Rejection
    ctx.rejections = [Rejection(op_index=2, op_kind="add_fact",
                                reason="unknown subject database:ghost|prod")]
    prompt = lp._build_prompt(ctx)
    assert "OPS DROPPED LAST TURN" in prompt
    assert "unknown subject database:ghost|prod" in prompt
