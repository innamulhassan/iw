"""P4 step 4 — correlate_timeline's executable home, wired: every phase whose playbook
allowed_intents declare the abstract `correlate_timeline` intent (investigate in the
core playbook) receives the ENGINE-computed skew-tolerant
change→onset candidates in its PlanContext; phases that do not declare it receive none.
Driven through the REAL engine with the database twin (CHG-9, a ServiceNow-sourced DB
migration 8 minutes before a Prometheus-sourced onset), and rendered into the live
planner's prompt with the R-J2 ordering discipline spelled out.
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import iw_engine
from iw_engine.capability import CapabilityLayer, MockSource
from iw_engine.capability.adapters import default_adapters
from iw_engine.runtime import Engine, ScriptedPlanner, load_playbook
from iw_engine.runtime.live_planner import LivePlanner
from iw_engine.runtime.planner import PlanContext

from . import scenario_database as sdb

PLAYBOOK = pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"


class _Probe:
    """Planner wrapper recording each phase's PlanContext.correlations before delegating."""

    def __init__(self, inner):
        self.inner = inner
        self.seen: dict[str, list[dict]] = {}

    def plan(self, ctx):
        self.seen[ctx.phase] = list(ctx.correlations)
        return self.inner.plan(ctx)


def test_engine_hands_correlations_to_the_declaring_phases_only():
    subject, script, fixtures = sdb.build()
    pb = load_playbook(PLAYBOOK)
    probe = _Probe(ScriptedPlanner(script))
    layer = CapabilityLayer(default_adapters(), source=MockSource(fixtures))
    engine = Engine(pb, probe, clock=lambda: datetime(2026, 7, 19, tzinfo=UTC), layer=layer)
    res = engine.run(subject)

    # the run itself is untouched by the hint (root-cause invariant)
    assert res.close_outcome == "resolved"
    assert res.confirmed is not None and res.confirmed.id == "hyp:h1"

    # phases that do NOT declare correlate_timeline get no candidates
    for p in ("frame", "act", "verify", "close"):
        assert probe.seen[p] == [], f"{p} must not receive correlations"

    # investigate declares it (both loop turns receive candidates; the probe keeps the
    # last) — CHG-9's `implemented` (13:57, servicenow) correlates with the 14:05
    # prometheus onset: 480s lead, OUTSIDE the 330s combined skew bound, so ordering
    # may be asserted (R-J2).
    cands = probe.seen["investigate"]
    assert [c["entity"] for c in cands] == [sdb.CHG], f"investigate correlations: {cands}"
    c = cands[0]
    assert c["type"] == "implemented" and c["lead_s"] == 480.0
    assert c["skew_bound_s"] == 330.0 and c["ordering_certain"] is True


def test_live_planner_renders_the_correlation_hint_with_rj2_discipline():
    pb = load_playbook(PLAYBOOK)
    spec = pb.phase(pb.phases[1].id)               # investigate
    planner = LivePlanner(client=None, catalog_text="# CATALOG", tools_text="# TOOLS",
                          tool_intents=set(), verbose=False)
    ctx = PlanContext(
        subject=sdb.build()[0], phase=spec.id, phase_spec=spec, goal=spec.goal,
        tunables=pb.tunables,
        correlations=[
            {"event": "evt-1", "entity": sdb.CHG, "type": "implemented",
             "occurred_at": "2026-07-19T13:57:00+00:00", "source": "servicenow",
             "lead_s": 480.0, "skew_bound_s": 330.0, "ordering_certain": True},
            {"event": "evt-2", "entity": sdb.CHG, "type": "implemented",
             "occurred_at": "2026-07-19T14:06:00+00:00", "source": "servicenow",
             "lead_s": -60.0, "skew_bound_s": 330.0, "ordering_certain": False},
        ])
    prompt = planner._build_prompt(ctx)
    assert "TEMPORALLY-CORRELATED CHANGE EVENTS" in prompt
    assert "preceded onset" in prompt                          # the certain candidate
    assert "do NOT assert it came first" in prompt             # the within-skew candidate
    # and a phase with no candidates renders no section at all
    assert "TEMPORALLY-CORRELATED" not in planner._build_prompt(
        PlanContext(subject=sdb.build()[0], phase=spec.id, phase_spec=spec,
                    goal=spec.goal, tunables=pb.tunables))
