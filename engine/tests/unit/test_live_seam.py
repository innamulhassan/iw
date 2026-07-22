"""Hermetic (no-network) coverage for the LIVE-convergence seam (VALIDATION-VERDICT §A
gaps 2 + 4): the git adapter's content fold (real diff/blame lines, not just counts), the
`ScenarioSource` intent->provider fixture transport, and the LivePlanner's reject+repair
guards that keep a real model's output from ever reaching the reducer as a rejection/crash.
The live LLM run itself lives in scripts/run_live.py (key-gated); this pins the mechanics.
"""
from __future__ import annotations

from datetime import UTC, datetime

from iw_engine.capability import ScenarioSource
from iw_engine.capability.adapters.git import GitAdapter
from iw_engine.domain import registry
from iw_engine.domain.enums import Binding, ConfidenceLevel, NodeType, Source
from iw_engine.domain.operations import AddAssertion
from iw_engine.runtime.live_planner import LivePlanner

T = datetime(2026, 7, 19, 13, 57, tzinfo=UTC)


# ── GAP 2: git returns CONTENT, not just counts ────────────────────────────────
def _facts(ops):
    # git now emits AddAssertion natively (P1b); diff stats/summary are content DESCRIPTORs.
    return {o.name: o.value for o in ops if isinstance(o, AddAssertion)}


def test_git_diff_summary_content_fact_on_commit():
    ops = GitAdapter().normalize({
        "commit": {"sha": "abc123", "repo": "app"},
        "diff": {"at": T, "files_changed": 1, "lines_deleted": 1,
                 "changed_lines": ["+ DROP INDEX idx_order_items_order_id;"]},
    })
    f = _facts(ops)
    assert "DROP INDEX" in f["diff_summary"]           # the actual changed line, folded as a fact
    commit_id = registry.node_id(NodeType.CODE_COMMIT, {"sha": "abc123"})
    assert any(o.subject == commit_id and o.name == "diff_summary"
               for o in ops if isinstance(o, AddAssertion))


def test_git_diff_without_changed_lines_emits_no_summary_golden_safe():
    """The hermetic golden path (no `changed_lines`) must be untouched — counts only."""
    ops = GitAdapter().normalize({
        "commit": {"sha": "abc123", "repo": "app"},
        "diff": {"at": T, "files_changed": 1, "lines_added": 1, "lines_deleted": 0},
    })
    assert "diff_summary" not in _facts(ops)
    assert _facts(ops).keys() >= {"files_changed", "lines_added", "lines_deleted"}


def test_git_diff_attaches_to_change_when_no_commit():
    """A content-only diff (no commit surfaced) folds onto the CHANGE_EVENT that shipped it —
    which is how the DB scenario roots at change_event:chg-9, not a competing commit."""
    ops = GitAdapter().normalize({
        "change": {"change_id": "CHG-9", "change_type": "database"},
        "diff": {"at": T, "lines_deleted": 1, "changed_lines": ["+ DROP INDEX idx;"]},
    })
    chg_id = registry.node_id(NodeType.CHANGE_EVENT,
                              {"change_id": "CHG-9", "change_type": "database"})
    summ = [o for o in ops if isinstance(o, AddAssertion) and o.name == "diff_summary"]
    assert summ and summ[0].subject == chg_id


def test_git_blame_line_content_fact():
    ops = GitAdapter().normalize({
        "blame": {"sha": "abc123", "repo": "app", "file": "TaxCalculator.java", "line": 88,
                  "at": T, "snippet": "return calc.rate(order.getRegion());"},
        "error_signature_hash": "npe-taxcalc",
    })
    blame = [o for o in ops if isinstance(o, AddAssertion) and o.name == "blame_line"]
    assert blame and "TaxCalculator.java:88" in blame[0].value
    assert "return calc.rate" in blame[0].value


# ── GAP 4: intent -> provider -> fixture ───────────────────────────────────────
def test_scenario_source_resolves_any_intent_to_provider_blob():
    ip = {"active_alerts": "prometheus", "instant_query": "prometheus", "get_commit": "git"}
    fx = {"prometheus": {"*": {"service": {"name": "s", "env": "prod"}}}}
    src = ScenarioSource(ip, fx)
    # ANY prometheus intent returns the prometheus blob (the two-vocabulary gap closed)
    assert src.fetch(Binding.REST, "active_alerts", {}) == {"service": {"name": "s", "env": "prod"}}
    assert src.fetch(Binding.REST, "instant_query", {}) == {"service": {"name": "s", "env": "prod"}}
    # a provider with no fixture -> empty (adapter folds to zero ops)
    assert src.fetch(Binding.REST, "get_commit", {}) == {}
    # an intent mapped to no provider -> empty
    assert src.fetch(Binding.REST, "unknown_intent", {}) == {}


def test_scenario_source_phase_override():
    ip = {"instant_query": "prometheus"}
    fx = {"prometheus": {"*": {"metrics": [{"v": 1}]}, "verify": {"metrics": [{"v": 2}]}}}
    src = ScenarioSource(ip, fx)
    assert src.fetch(Binding.REST, "instant_query", {})["metrics"][0]["v"] == 1
    src.phase = "verify"
    assert src.fetch(Binding.REST, "instant_query", {})["metrics"][0]["v"] == 2
    src.phase = "investigate"   # no override -> falls back to "*"
    assert src.fetch(Binding.REST, "instant_query", {})["metrics"][0]["v"] == 1


# ── LivePlanner reject+repair guards (no reducer rejection / crash) ─────────────
def test_belief_channel_repair_measured_and_inferred():
    # a measured source with no reliability -> reliability filled, no confidence (Fact-valid)
    src, lvl, rel = LivePlanner._belief_channel({"source": "prometheus"})
    assert src == Source.PROMETHEUS and lvl is None and rel == 0.9
    # source=llm with no level -> a confidence level, no reliability
    src, lvl, rel = LivePlanner._belief_channel({"source": "llm"})
    assert src == Source.LLM and lvl == ConfidenceLevel.MED and rel is None
    # an unknown source -> treated as the model's own inference (llm)
    src, lvl, rel = LivePlanner._belief_channel({"source": "monitoring"})
    assert src == Source.LLM and lvl is not None and rel is None


def test_illegal_predicate_guard():
    # degraded on the Anomaly is illegal (belongs on the Service) -> repaired away
    assert LivePlanner._illegal_predicate("anomaly:anom-1", "degraded")
    # onset_value on the Anomaly is fine
    assert LivePlanner._illegal_predicate("anomaly:anom-1", "onset_value") is None
    # a fact on a hypothesis node is never allowed (evidence goes via add_supporting/refuting)
    assert LivePlanner._illegal_predicate("hyp:h1", "count")
    assert LivePlanner._illegal_predicate("hypothesis:h1", "count")
    # a service fact is fine
    assert LivePlanner._illegal_predicate("service:orders-api|prod", "red_latency_p99") is None


def test_canon_matches_registry_slug():
    # a model-authored id with wrong casing/spacing resolves to the node the tools created
    assert LivePlanner._canon("anomaly:ANOM-1") == "anomaly:anom-1"
    assert LivePlanner._canon("service:Orders-API|Prod") == "service:orders-api|prod"
    assert LivePlanner._canon("anomaly:anom-1") == "anomaly:anom-1"   # idempotent


def test_git_adapter_binding_is_rest():
    assert GitAdapter().binding == Binding.REST
