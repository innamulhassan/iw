"""P4a · runtime — playbook loader (B1) + RunState/transition predicates (B6) — unit tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from engine.domain import PhaseEffect
from engine.runtime import (
    END,
    load_playbook,
    load_playbook_text,
    min_confidence_of,
    recovered,
    route_after_root_cause,
    route_after_verify,
    split_frontmatter,
    top_confidence,
)
from engine.runtime.state import attempt

PLAYBOOK = Path(__file__).parents[2] / "playbooks" / "incident-triage.md"


# ── loader (B1) ─────────────────────────────────────────────────────────
def test_loads_the_four_phases_in_order():
    pb = load_playbook(PLAYBOOK)
    assert pb.pk == ("incident-triage", "1.0.0")
    assert [p.id for p in pb.phases] == ["assess", "root-cause", "remediation", "verify-close"]


def test_phase_effects_and_flags():
    pb = load_playbook(PLAYBOOK)
    by_id = {p.id: p for p in pb.phases}
    assert by_id["assess"].effect is PhaseEffect.read_only
    assert by_id["remediation"].effect is PhaseEffect.write
    assert by_id["remediation"].gate_writes is True
    assert by_id["root-cause"].min_confidence == 0.7
    assert by_id["root-cause"].output == "RootCauseResult"
    assert "topology" in by_id["assess"].needs


def test_defaults_and_governance_parsed():
    pb = load_playbook(PLAYBOOK)
    assert pb.unknown_access == "ask"
    assert pb.defaults.on_failure == "run-remaining"
    assert pb.defaults.retry.max == 3
    assert pb.error_handler.action == "escalate"


def test_graph_schema_and_body_parsed():
    pb = load_playbook(PLAYBOOK)
    assert "depends_on" in pb.graph_schema["edges"]
    assert "system" in pb.graph_schema["node_types"]
    assert pb.body_md and "## assess" in pb.body_md


def test_missing_fence_rejected():
    with pytest.raises(ValueError):
        load_playbook_text("id: x\nno fence here")


def test_unknown_frontmatter_key_rejected():
    bad = "---\nid: x\nversion: 1.0.0\ndomain: d\nsurprise: nope\n---\nbody"
    with pytest.raises(ValueError):
        load_playbook_text(bad)


def test_tool_name_in_needs_rejected_by_loader():
    bad = ("---\nid: x\nversion: 1.0.0\ndomain: d\n"
           "phases:\n  - {id: a, effect: read-only, output: AssessResult, needs: [appd__get_health]}\n"
           "---\nbody")
    with pytest.raises(ValueError):
        load_playbook_text(bad)


def test_split_frontmatter_separates_body():
    fm, body = split_frontmatter("---\nid: x\n---\n## heading\ntext")
    assert fm["id"] == "x"
    assert body == "## heading\ntext"


# ── transition predicates (B6) ──────────────────────────────────────────
def _state_with(records):
    return {"phase_records": records}


def test_attempt_counts_reentries():
    st = _state_with([{"phase": "root-cause"}, {"phase": "remediation"}, {"phase": "root-cause"}])
    assert attempt(st, "root-cause") == 3
    assert attempt(st, "assess") == 1


def test_top_confidence_reads_latest_root_cause():
    st = _state_with([{"phase": "root-cause", "output": {
        "candidates": [{"confidence": {"value": 0.9, "basis": "x"}}, {"confidence": 0.4}]}}])
    assert top_confidence(st) == 0.9
    assert top_confidence(_state_with([])) == 0.0


def test_recovered_reads_verify_output():
    assert recovered(_state_with([{"phase": "verify-close", "output": {"recovered": True}}])) is True
    assert recovered(_state_with([{"phase": "verify-close", "output": {"recovered": False}}])) is False


def test_route_after_root_cause():
    pb = load_playbook(PLAYBOOK)   # min_confidence 0.7
    high = _state_with([{"phase": "root-cause", "output": {"candidates": [{"confidence": {"value": 0.9, "basis": "x"}}]}}])
    low = _state_with([{"phase": "root-cause", "output": {"candidates": [{"confidence": {"value": 0.5, "basis": "x"}}]}}])
    assert route_after_root_cause(high, pb) == "remediation"
    assert route_after_root_cause(low, pb) == "root-cause"     # loop — gather more
    assert min_confidence_of(pb) == 0.7


def test_route_after_verify():
    assert route_after_verify(_state_with([{"phase": "verify-close", "output": {"recovered": True}}])) == END
    assert route_after_verify(_state_with([{"phase": "verify-close", "output": {"recovered": False}}])) == "root-cause"


# ── audit fixes: metadata-driven routing, loop cap, backtrack validation ──
def test_min_confidence_optional_when_absent():
    pb = load_playbook(PLAYBOOK)
    assert min_confidence_of(pb, "assess") is None       # no threshold → not a loop phase (NICE-10)
    assert min_confidence_of(pb, "root-cause") == 0.7


def test_loop_caps_attempts_so_it_never_spins():
    from engine.runtime.state import route_after_loop
    pb = load_playbook(PLAYBOOK)
    low = {"phase": "root-cause", "output": {"candidates": [{"confidence": {"value": 0.1, "basis": "x"}}]}}
    assert route_after_loop(_state_with([low] * 2), pb, "root-cause") == "loop"      # under cap → loop
    assert route_after_loop(_state_with([low] * 6), pb, "root-cause") == "advance"   # capped → advance


def test_playbook_rejects_forward_or_missing_backtrack():
    from engine.domain import PhaseEffect, PhaseSpec, Playbook
    with pytest.raises(ValueError, match="backtrack_to"):
        Playbook(id="x", version="1.0.0", domain="d", phases=[
            PhaseSpec(id="a", effect=PhaseEffect.read_only, output="AssessResult"),
            PhaseSpec(id="b", effect=PhaseEffect.read_only, output="VerifyResult", backtrack_to="nope")])
