"""P8 · the mocked end-to-end — INC-4821 through the whole system, asserting AC1–AC9.

This is the stop-point gate before real integration: the entire engine runs against mocks (no real
model / sources / stores) and every acceptance criterion in the PRD (§10) holds.
"""
from __future__ import annotations

import pytest

from engine.capability import resolve_intent
from engine.domain import (
    AssessResult,
    Node,
    PhaseEffect,
    RemediationResult,
    RootCauseResult,
    SubjectRef,
    VerifyResult,
)
from engine.graph_runtime import UNKNOWN, render_slice
from engine.session import InMemoryRunLock, SessionManager
from fixtures.mock_engine import build_engine, build_layer

SUBJECT = {"domain": "app-incident", "id": "INC-4821", "kind": "incident"}


def _run_to_completion(playbook):
    eng = build_engine(playbook)
    eng.start(SUBJECT, thread_id="INC-4821")
    paused = eng.state("INC-4821")
    assert paused["next"] == ["remediation"]          # AC2 — paused at the gate before the write
    eng.resume("INC-4821")                             # AC3 — resumes from the checkpoint
    return eng


# ── AC7 — INC-4821 runs end-to-end (the headline) ──────────────────────
def test_ac7_inc4821_end_to_end(playbook):
    eng = _run_to_completion(playbook)
    recs = {r["phase"]: r for r in eng.state("INC-4821")["values"]["phase_records"]}
    assert set(recs) == {"assess", "root-cause", "remediation", "verify-close"}
    assert all(r["state"] == "done" for r in recs.values())

    AssessResult.model_validate(recs["assess"]["output"])
    rc = RootCauseResult.model_validate(recs["root-cause"]["output"])
    assert rc.candidates[0].node == "stor:pay-vol"
    assert rc.candidates[0].confidence.value == 0.9
    assert any("rev47" in r.hyp for r in rc.ruled_out)       # rev47 ruled out
    rem = RemediationResult.model_validate(recs["remediation"]["output"])
    assert rem.actions[0].technique == "failover"
    ver = VerifyResult.model_validate(recs["verify-close"]["output"])
    assert ver.recovered is True


# ── AC2 — every write gated + carries a rollback; closing is human ──────
def test_ac2_write_gated_rollback_and_human_close(playbook):
    eng = _run_to_completion(playbook)
    recs = {r["phase"]: r for r in eng.state("INC-4821")["values"]["phase_records"]}
    action = RemediationResult.model_validate(recs["remediation"]["output"]).actions[0]
    assert action.gated is True and action.rollback            # gated + reversible
    assert action.temporary is True and action.revert_when     # temporary → must revert
    assert VerifyResult.model_validate(recs["verify-close"]["output"]).closed_by == "j.rivera"  # human close


# ── AC1 — a read-only phase provably cannot select a write ──────────────
def test_ac1_read_only_cannot_write(playbook):
    layer = build_layer()
    assert resolve_intent("remediation-action", PhaseEffect.read_only, layer.registry) == []
    assert [c.id for c in resolve_intent("remediation-action", PhaseEffect.write, layer.registry)]


# ── AC5 — a capability the playbook never names is reached only via the registry; needs are intents
def test_ac5_playbook_names_intents_not_tools(playbook):
    for ph in playbook.phases:
        assert all("__" not in need for need in ph.needs)      # intents, never provider__action


# ── AC6 — the graph renders a bounded slice even for a 147-node incident ─
def test_ac6_render_slice_bounded(playbook):
    eng = _run_to_completion(playbook)
    g = eng.graph
    for i in range(147 - len(g)):
        g.upsert_node(Node.model_validate({"id": f"host:{i}", "kind": "system", "type": "compute"}))
    sl = render_slice(g, "app:payments-api")
    assert sl["total"] == 147 and sl["rendered"] <= 30         # bounded regardless of size


# ── AC8 — the graph never invents or silently overwrites ────────────────
def test_ac8_graph_guards(playbook):
    eng = _run_to_completion(playbook)
    g = eng.graph
    assert g.get("ghost:node")["status"] == UNKNOWN            # unknown id → unknown, never invented
    with pytest.raises(ValueError):
        g.annotate("app:payments-api", "label", "suspect", evidence_ref="")   # annotate needs evidence


# ── AC4 + AC9 — session invariants (one thread, queued input, answered-once, lease steal) ──
def test_ac4_ac9_session_invariants():
    mgr = SessionManager()
    subject = SubjectRef(**SUBJECT)
    a = mgr.create_or_join(subject, "alice")
    b = mgr.create_or_join(subject, "bob")
    assert a is b                                              # AC9 — one thread per incident
    mgr.enqueue_input(a, {"text": "also check the cache"})
    assert mgr.drain_inputs(a)                                 # AC4 — queued, not a 2nd run
    first = mgr.answer_gate(a, "g1", "approve", "alice")
    assert mgr.answer_gate(a, "g1", "deny", "bob") == first    # AC4 — answered-once, first wins


def test_ac9_crashed_owner_lease_stolen():
    clock = {"t": 0.0}
    lock = InMemoryRunLock(clock=lambda: clock["t"])
    lock.acquire("s", "owner-A", ttl=10)                       # owner crashes (no heartbeats)
    clock["t"] = 11
    assert lock.acquire("s", "owner-B") is not None            # AC9 — another server resumes
