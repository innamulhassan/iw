"""End-to-end: FIREWALL / security-rule layer, driven through the REAL engine with mocked
capability outputs (prometheus, splunk, servicenow fixtures). Asserts the OUTCOME, the
differential diagnosis (a link-flap rival is ruled out via clean policy denies, not
drops), and — since the fix is a security change — that the write-gate holds: a capability
write attempted outside the human-gated REMEDIATE phase is blocked by the real
CapabilityLayer, never silently applied.
"""
from __future__ import annotations

import pathlib
from datetime import UTC, datetime

import iw_engine
from iw_engine.capability import CapabilityLayer, MockSource
from iw_engine.capability.adapters import default_adapters
from iw_engine.capability.adapters.ocp import OcpRestartAdapter
from iw_engine.domain.enums import CloseOutcome, EdgeType, Effect, HypothesisStatus, Phase
from iw_engine.graph import rebuild
from iw_engine.runtime import Engine, ScriptedPlanner, load_playbook

from . import scenario_firewall as s5

PLAYBOOK = pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"


def _run(subject, script, fixtures, *, extra_adapters: list | None = None):
    """Like _helpers.run(), but also returns the engine's capability-invocation audit
    trail (RunResult itself doesn't carry it) — needed to assert the write-gate held.
    `extra_adapters` lets a scenario exercise a capability beyond the default 8 (here:
    the real WRITE-effect `OcpRestartAdapter`, which the wiring deliberately keeps out
    of `default_adapters()` — see its docstring — so the write-gate test proves the
    engine blocks it via `CapabilityLayer`'s `effect == Effect.WRITE` check itself,
    not merely because the intent is unregistered)."""
    pb = load_playbook(PLAYBOOK)
    layer = CapabilityLayer(default_adapters() + list(extra_adapters or []),
                            source=MockSource(fixtures))
    clock = lambda: datetime(2026, 7, 19, tzinfo=UTC)  # noqa: E731 deterministic
    engine = Engine(pb, ScriptedPlanner(script), clock=clock, layer=layer)
    res = engine.run(subject)
    return res, engine.invocations


def test_firewall_acl_revert_resolves():
    subject, script, fixtures = s5.build()
    res, invocations = _run(subject, script, fixtures)

    assert res.phases_run == [Phase.FRAME, Phase.TRIAGE, Phase.HYPOTHESIZE, Phase.INVESTIGATE,
                              Phase.REMEDIATE, Phase.VERIFY, Phase.CLOSE]
    assert res.rejections == [], f"unexpected rejected ops: {res.rejections}"
    assert res.close_outcome == CloseOutcome.RESOLVED
    assert res.confirmed is not None and res.confirmed.id == "hyp:h1"

    # differential diagnosis: the link-flap rival was ruled out, not ignored
    assert res.hypothesis_store.hypotheses["hyp:h2"].status == HypothesisStatus.REFUTED
    assert s5.fid(s5.SEG_FRAUD, "packet_loss", s5.T_INV) in \
        res.hypothesis_store.hypotheses["hyp:h2"].refuting_facts
    assert s5.fid(s5.RULE, "deny_count", s5.T_INV) in \
        res.hypothesis_store.hypotheses["hyp:h1"].supporting_facts

    # the graph carries the full typed causal picture, incl. the mocked-capability nodes
    for node_id in [s5.SVC, s5.ANOM, s5.CHG, s5.RULE, s5.SEG_FRAUD, s5.SEG_GEO, s5.SEG_PAY,
                    s5.EXT, s5.H1]:
        assert res.graph.node(node_id) is not None, f"missing node {node_id}"
    caused = res.graph.out_edges(s5.H1, EdgeType.CAUSED_BY)
    assert caused and caused[0].dst == s5.RULE

    # only ONE egress target actually failed at investigation time — the discriminator
    # that rules out a segment-wide/physical-layer explanation (SEG_FRAUD later recovers
    # post-revert, so pin this to the INVESTIGATE-phase snapshot)
    probe_at_inv = {f.subject_ref: f.value for f in res.graph.facts.values()
                    if f.predicate == "probe_success" and f.valid_from == s5.T_INV}
    assert probe_at_inv[s5.SEG_FRAUD] == 0
    assert probe_at_inv[s5.SEG_GEO] == 1
    assert probe_at_inv[s5.SEG_PAY] == 1

    # the symptom fact was superseded on recovery (bi-temporal), not overwritten
    degraded_facts = [f for f in res.graph.facts.values()
                      if f.subject_ref == s5.SVC and f.predicate == "degraded"]
    assert len(degraded_facts) == 2  # True (superseded) + False (active)
    active = [f for f in degraded_facts if f.is_open]
    assert len(active) == 1 and active[0].value is False

    # capability calls actually ran (mocked outputs, not just direct ops) — the headline
    # evidence came from the real adapters
    intents_invoked = {inv.intent for inv in invocations}
    assert {"active_alerts", "find_recent_changes", "fetch_metrics",
            "search_fw_denies"} <= intents_invoked
    assert all(not inv.blocked for inv in invocations)  # nothing improper this run

    # the journal alone rebuilds the graph exactly (source-of-truth guarantee)
    g2, _ = rebuild(res.journal)
    assert g2.to_dict() == res.graph.to_dict()


def test_write_gate_blocks_premature_remediation():
    """A security fix is proposed in REMEDIATE (an UpdateHypothesis, no capability write)
    and only ever applies through the human-approved gate. Prove the gate is real: an
    on-call's premature `ocp__restart` fired in TRIAGE (before REMEDIATE) must be blocked
    by the CapabilityLayer — outcome is unaffected, and the run still resolves cleanly."""
    subject, script, fixtures = s5.build(premature_write=True)
    res, invocations = _run(subject, script, fixtures, extra_adapters=[OcpRestartAdapter()])

    write_invocations = [inv for inv in invocations if inv.intent == "ocp__restart"]
    assert len(write_invocations) == 1
    inv = write_invocations[0]
    assert inv.effect == Effect.WRITE
    assert inv.blocked is True
    assert inv.op_count == 0
    assert inv.reason is not None and "write blocked" in inv.reason

    # the gate is categorical: across the WHOLE run, no write-effect capability ever
    # executed un-blocked (REMEDIATE itself never calls one — the fix stays proposed)
    assert all(i.blocked for i in invocations if i.effect == Effect.WRITE)

    # blocking the stray write didn't corrupt or derail the investigation
    assert res.rejections == []
    assert res.phases_run == [Phase.FRAME, Phase.TRIAGE, Phase.HYPOTHESIZE, Phase.INVESTIGATE,
                              Phase.REMEDIATE, Phase.VERIFY, Phase.CLOSE]
    assert res.close_outcome == CloseOutcome.RESOLVED
    assert res.confirmed is not None and res.confirmed.id == "hyp:h1"
