"""W2 — the honest audit trail (completeness pass, audit 2026-07-23): the reject/repair loop.

Two drops in the system used to vanish while every other one was first-class:
  * M5 — the per-phase `op_ceiling` head-slice (`plan.ops[:ceiling]`) cut the planner's over-cap
    ops with NO journal entry and NO feedback to the next plan.
  * M6 — the LIVE planner's own repairs (off-catalog tool, unparseable/illegal op, coerced
    verdict) reached only the verbose log / dev summary — never the journal, never the next
    PlanContext.rejections, while the reducer's drops rode the better feedback path.

These pin the fix: both now emit their declared-but-formerly-dead journal kinds (M7 —
`rejection` for M5, `repair` for M6), and both feed the next plan, unifying the planner's
enforcement channel with the reducer's. Deterministic scripts truncate nothing and repair
nothing, so the 11 goldens stay byte-identical (asserted by the golden suite).
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime

from e2e import scenario_nochange
from e2e._helpers import fact, node, phase, propose

import iw_engine
from iw_engine.domain.enums import NodeType, Source
from iw_engine.journal import Journal
from iw_engine.runtime import Engine, ScriptedPlanner, load_playbook
from iw_engine.runtime.live_planner import LivePlanner
from iw_engine.runtime.planner import PlanContext

PLAYBOOK = pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"
T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
ANOM = "anomaly:anom-1"


def _clock() -> datetime:
    return datetime(2026, 7, 19, tzinfo=UTC)


class _Capture:
    """Records ctx.rejections seen at the start of each plan() (the next-plan feedback probe)."""

    def __init__(self, script: list) -> None:
        self._inner = ScriptedPlanner(script)
        self.seen: list[list] = []

    def plan(self, ctx: PlanContext):
        self.seen.append(list(ctx.rejections))
        return self._inner.plan(ctx)


def _frame_over_cap() -> list:
    """FRAME authors 3 ops; with op_ceiling[frame]=2 the third (severity_score) is cut. The
    surviving node + onset fact still satisfy the frame gate (min_facts>=1), so it advances."""
    return [
        node(NodeType.ANOMALY, anomaly_id="ANOM-1"),
        fact(ANOM, "onset_value", 42, T0, source=Source.PROMETHEUS),
        fact(ANOM, "severity_score", 3, T0, source=Source.PROMETHEUS),   # dropped by the ceiling
    ]


# ── M5: the op_ceiling truncation is journaled + fed back ──────────────────────
def test_op_ceiling_truncation_is_journaled_as_a_rejection():
    pb = load_playbook(PLAYBOOK)
    pb.tunables.op_ceiling = {pb.entry_phase: 2}
    eng = Engine(pb, ScriptedPlanner([phase("frame", ops=_frame_over_cap(),
                                            narrative="frame over the cap")]), clock=_clock)
    eng.start(scenario_nochange.build()[0])
    eng.step()

    rej = [e for e in eng.journal.entries if e.kind == "rejection"]
    assert len(rej) == 1
    r = rej[0]
    assert r.phase_id == "frame"
    assert r.action["op_kind"] == "op_ceiling"
    assert r.action["dropped"] == ["AddAssertion"]         # the one severity_score fact over cap
    assert "op_ceiling[frame]=2" in r.reasoning and "dropped 1 over the cap" in r.reasoning
    # an annotation, not a numbered step: it SHARES the phase seq (numbering/goldens untouched)
    assert r.seq == eng.journal.phase_entries()[0].seq


def test_op_ceiling_below_the_cap_emits_no_rejection():
    """The clean case: within the cap, nothing is dropped and no record is made (contract stays
    empty when clean, like the reducer-rejection surface)."""
    pb = load_playbook(PLAYBOOK)                            # real ceilings (frame=60) — no cut
    eng = Engine(pb, ScriptedPlanner([phase("frame", ops=[
        node(NodeType.ANOMALY, anomaly_id="ANOM-1"),
        fact(ANOM, "onset_value", 42, T0, source=Source.PROMETHEUS),
    ], narrative="within the cap")]), clock=_clock)
    eng.start(scenario_nochange.build()[0])
    eng.step()
    assert [e for e in eng.journal.entries if e.kind == "rejection"] == []


def test_op_ceiling_drop_feeds_the_next_plan():
    pb = load_playbook(PLAYBOOK)
    pb.tunables.op_ceiling = {pb.entry_phase: 2}
    script = [
        phase("frame", ops=_frame_over_cap(), narrative="frame over the cap"),
        phase("investigate", ops=[propose("h1", "chg-9 is the root", "high", root=ANOM)],
              narrative="second phase"),
    ]
    planner = _Capture(script)
    eng = Engine(pb, planner, clock=_clock)
    eng.start(scenario_nochange.build()[0])
    eng.step()
    eng.step()

    assert planner.seen[0] == []                            # nothing dropped before frame ran
    fed = planner.seen[1]
    assert any(r.op_kind == "op_ceiling" for r in fed)
    assert any("dropped 1 over the cap" in r.reason for r in fed)


# ── M6: the LIVE planner's repairs ride PlanOutput.repairs ─────────────────────
def _live_ctx(pb):
    spec = pb.phase(pb.entry_phase)
    return PlanContext(subject=scenario_nochange.build()[0], phase=pb.entry_phase,
                       phase_spec=spec, goal=spec.goal, tunables=pb.tunables)


def test_live_plan_output_carries_its_repairs():
    """Each drop the planner makes mapping raw LLM output -> PlanOutput is now on the output
    (the engine journals + feeds these), not just in the cumulative dev log."""
    pb = load_playbook(PLAYBOOK)
    lp = LivePlanner(client=None, catalog_text="# CAT", tools_text="# TOOLS",
                     tool_intents={"fetch_logs"}, verbose=False)
    raw = {
        "calls": [{"intent": "not_a_tool"}],       # off-catalog -> dropped
        "ops": [{"op": "bogus_op"}],               # unknown kind -> dropped
        "verdict": "advance",                      # bare string -> coerced
        "narrative": "n",
    }
    out = lp._to_plan_output(_live_ctx(pb), raw)
    assert out.calls == [] and out.ops == []
    assert any("off-catalog tool intent" in r for r in out.repairs)
    assert any("dropped op" in r and "bogus_op" in r for r in out.repairs)
    assert any("coerced bare-string verdict" in r for r in out.repairs)
    # a clean plan carries no repairs (empty when nothing was repaired)
    clean = lp._to_plan_output(_live_ctx(pb), {"narrative": "n", "verdict": {"status": "advance"}})
    assert clean.repairs == []


def test_scripted_plan_output_has_no_repairs():
    """The deterministic twin repairs nothing — the reason the scripted/golden path is untouched."""
    assert phase("frame", ops=[], narrative="x").repairs == []


# ── M6: the engine journals planner repairs + feeds them to the next plan ──────
def test_engine_journals_planner_repairs_and_feeds_them_back():
    class _Repairing:
        def __init__(self, script: list) -> None:
            self._inner = ScriptedPlanner(script)
            self.seen: list[list] = []

        def plan(self, ctx: PlanContext):
            self.seen.append(list(ctx.rejections))
            out = self._inner.plan(ctx)
            if ctx.phase == "frame":
                return out.model_copy(update={
                    "repairs": ["[frame] dropped off-catalog tool intent: 'nope'"]})
            return out

    script = [
        phase("frame", ops=[
            node(NodeType.ANOMALY, anomaly_id="ANOM-1"),
            fact(ANOM, "onset_value", 42, T0, source=Source.PROMETHEUS),
        ], narrative="frame"),
        phase("investigate", ops=[propose("h1", "chg-9 is the root", "high", root=ANOM)],
              narrative="inv"),
    ]
    planner = _Repairing(script)
    eng = Engine(load_playbook(PLAYBOOK), planner, clock=_clock)
    eng.start(scenario_nochange.build()[0])
    eng.step()
    eng.step()

    reps = [e for e in eng.journal.entries if e.kind == "repair"]
    assert len(reps) == 1
    assert reps[0].phase_id == "frame"
    assert "off-catalog tool intent" in reps[0].reasoning
    assert reps[0].seq == eng.journal.phase_entries()[0].seq     # shares the phase seq
    # fed into the NEXT plan's rejections (unified with reducer drops)
    assert planner.seen[0] == []
    assert any(r.op_kind == "repair" and "off-catalog" in r.reason for r in planner.seen[1])


# ── M7: the two formerly-dead Literal kinds are now emitted + durable ──────────
def test_rejection_and_repair_kinds_are_live_and_roundtrip():
    jr = Journal(clock=_clock)
    r = jr.append_rejection(1, "frame", op_kind="op_ceiling", reason="cut 2", dropped=["AddNode"])
    p = jr.append_repair(1, "frame", detail="[frame] coerced verdict")
    assert r.kind == "rejection" and p.kind == "repair"
    # both survive the NDJSON round-trip — the durable schema honestly carries what it advertises
    rt = Journal.from_ndjson(jr.to_ndjson())
    assert {e.kind for e in rt.entries} == {"rejection", "repair"}
    assert rt.entries[0].action == {"op_kind": "op_ceiling", "dropped": ["AddNode"]}
