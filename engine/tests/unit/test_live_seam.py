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


# ── P7: projections drive reasoning — the focus slice + governed traversals in the prompt ──
def _spine_graph():
    """svc -> db structural spine; anomaly AFFECTS svc; chg touched db (CHANGED_BY,
    discovered); one healthy far pod that must land in the collapsed count, plus a
    hypothesis node (excluded from traversal by governance)."""
    from iw_engine.domain.common import Confidence
    from iw_engine.domain.edge import Edge
    from iw_engine.domain.enums import EdgeType, NodeType, Origin
    from iw_engine.domain.fact import Fact
    from iw_engine.domain.node import Node
    from iw_engine.domain.registry import edge_id
    from iw_engine.graph.graph import Graph

    g = Graph()
    for nid, t in (("anomaly:anom-1", NodeType.ANOMALY),
                   ("service:orders-api|prod", NodeType.SERVICE),
                   ("database:orders-pg", NodeType.DATABASE),
                   ("change_event:chg-9", NodeType.CHANGE_EVENT),
                   ("pod:far-away", NodeType.POD),
                   ("hyp:h1", NodeType.HYPOTHESIS)):
        g.upsert_node(Node(id=nid, type=t, created_by=1))

    def edge(et, src, dst, origin):
        conf = Confidence(value=0.7, basis="t") if origin == Origin.INFERRED else None
        g.add_edge(Edge(id=edge_id(et, src, dst, origin), type=et, src=src, dst=dst,
                        origin=origin, confidence=conf, created_by=1))

    edge(EdgeType.DEPENDS_ON, "service:orders-api|prod", "database:orders-pg", Origin.DECLARED)
    edge(EdgeType.AFFECTS, "anomaly:anom-1", "service:orders-api|prod", Origin.DISCOVERED)
    edge(EdgeType.CHANGED_BY, "database:orders-pg", "change_event:chg-9", Origin.DISCOVERED)
    edge(EdgeType.CAUSED_BY, "hyp:h1", "change_event:chg-9", Origin.INFERRED)
    g.add_fact(Fact(id="f1", subject_ref="anomaly:anom-1", predicate="onset_value", value=5200,
                    unit="ms", valid_from=T, observed_at=T, source=Source.PROMETHEUS,
                    source_reliability=0.97, created_by=1))
    return g


def _ctx_for(g, hypotheses=None):
    import pathlib

    import iw_engine
    from iw_engine.graph.tools import focus_slice
    from iw_engine.runtime.loader import load_playbook
    from iw_engine.runtime.planner import PlanContext

    pb = load_playbook(pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml")
    spec = pb.phase(pb.phases[3].id)
    return PlanContext(
        subject=__import__("iw_engine.domain.subject", fromlist=["SubjectRef"]).SubjectRef(
            domain="app-incident", id="INC-7734", kind="incident"),
        phase=spec.id, phase_spec=spec, goal=spec.goal,
        focus=focus_slice(g, "anomaly:anom-1", pb.tunables.focus_budget,
                          max_facts_per_node=pb.tunables.focus_facts_per_node,
                          frontier_hops=pb.tunables.focus_frontier_hops),
        hypotheses=hypotheses or [], tunables=pb.tunables)


def test_prompt_graph_view_is_the_focus_slice():
    """The live prompt's graph section is the B9.3 focus slice (tiered, budgeted, ruled-out
    surfaced) — the flat full-graph dump (render_graph_full) is gone."""
    from iw_engine.runtime import live_planner as lp

    assert not hasattr(lp, "render_graph_full")   # the fold-and-forget dump is deleted

    g = _spine_graph()
    planner = LivePlanner(client=None, catalog_text="# CAT", tools_text="# TOOLS",
                          tool_intents=set(), verbose=False)
    planner.graph = g
    prompt = planner._build_prompt(_ctx_for(g))
    assert "CURRENT GRAPH — FOCUS SLICE" in prompt
    assert "FOCUS: anomaly:anom-1" in prompt
    assert "[focus] anomaly:anom-1 (anomaly)" in prompt
    assert "onset_value=5200ms" in prompt                     # the evidence card
    # the healthy unimplicated pod is COLLAPSED to a count — it appears NOWHERE by id
    assert "pod:far-away" not in prompt
    assert "+ 1 collapsed" in prompt and "'pod': 1" in prompt


def test_prompt_projections_target_the_ranked_hypotheses():
    """blast_radius/path/walk render per ranked root (the P7 reasoning loop): the leader's
    root connects to the symptom's affected service; a prose root gets the repair hint."""
    g = _spine_graph()
    planner = LivePlanner(client=None, catalog_text="# CAT", tools_text="# TOOLS",
                          tool_intents=set(), verbose=False)
    planner.graph = g
    hyps = [{"id": "hyp:h1", "statement": "chg-9 dropped the index", "status": "proposed",
             "confidence": 0.6, "root_candidate": "change_event:chg-9",
             "supporting": 0, "refuting": 0},
            {"id": "hyp:h2", "statement": "prose root", "status": "proposed",
             "confidence": 0.3, "root_candidate": "the database is slow",
             "supporting": 0, "refuting": 0}]
    prompt = planner._build_prompt(_ctx_for(g, hyps))
    assert "# GRAPH PROJECTIONS" in prompt
    assert "symptom neighbourhood:" in prompt                       # neighbours(focus)
    # path: chg-9 -> (CHANGED_BY, ridden backward) -> db -> (DEPENDS_ON) -> svc
    assert "connection to the symptom's affected node: 2 hop(s): " \
           "change_event:chg-9 -> database:orders-pg -> service:orders-api|prod" in prompt
    # blast_radius: db's failure breaks svc; chg has no structural dependents
    assert "no structural dependents recorded" in prompt
    # walk from the leader root reaches the db@1 and svc@2
    assert "leader evidence neighbourhood (walk <=2 hops from change_event:chg-9): " \
           "database:orders-pg@1, service:orders-api|prod@2" in prompt
    # the prose root is called out for repair, not crashed on
    assert "hyp:h2 root='the database is slow': NOT a node in the graph" in prompt


def test_prompt_projections_absent_without_a_graph_ref():
    g = _spine_graph()
    planner = LivePlanner(client=None, catalog_text="# CAT", tools_text="# TOOLS",
                          tool_intents=set(), verbose=False)
    planner.graph = None            # hermetic default: no live graph ref
    prompt = planner._build_prompt(_ctx_for(g))
    assert "# GRAPH PROJECTIONS" not in prompt
    assert "CURRENT GRAPH — FOCUS SLICE" in prompt   # the slice still renders (engine-computed)


# ── F1: the live planner groups its plan into a CHECKLIST of to-dos (LLM authors them) ──
def test_output_contract_advertises_todo_grouping():
    from iw_engine.runtime import live_planner as lp

    assert '"todos"' in lp._OUTPUT_CONTRACT and "objective" in lp._OUTPUT_CONTRACT
    assert "CHECKLIST of `todos`" in lp._OUTPUT_CONTRACT
    # still domain-neutral (doctrine owns the vocab) — no incident literals leaked in
    for literal in ("onset_value", "anomaly", "healthrule_violations"):
        assert literal not in lp._OUTPUT_CONTRACT


def test_live_planner_groups_plan_into_todos_with_exact_attribution():
    """A model that emits `todos` yields a PlanOutput whose to-dos are authoritative: the flat
    calls/ops derive from them (execution stays 1:1) and each call/op attributes to its to-do."""
    g = _spine_graph()
    planner = LivePlanner(client=None, catalog_text="", tools_text="",
                          tool_intents={"find_recent_changes", "active_alerts"}, verbose=False)
    planner.graph = g
    raw = {
        "reasoning": "two-step frame",
        "todos": [
            {"objective": "pull recent changes",
             "calls": [{"intent": "find_recent_changes", "params": {"ci": "db"}}], "ops": []},
            {"objective": "read alerts + propose the change-first theory",
             "calls": [{"intent": "active_alerts", "params": {}}],
             "ops": [{"op": "propose_hypothesis", "hid": "h1", "statement": "chg-9 dropped the index",
                      "root_candidate": "change_event:chg-9", "confidence_level": "med"}]},
        ],
        "narrative": "framed", "verdict": {"status": "advance", "confidence_level": "med"},
    }
    out = planner._to_plan_output(_ctx_for(g), raw)
    assert [t.objective for t in out.todos] == ["pull recent changes",
                                                "read alerts + propose the change-first theory"]
    # the flat lists derive from the to-dos (the engine executes THESE, 1:1)
    assert [c.intent for c in out.calls] == ["find_recent_changes", "active_alerts"]
    assert [type(o).__name__ for o in out.ops] == ["ProposeHypothesis"]
    # attribution: call 0 → to-do 0, call 1 → to-do 1; the propose op is under to-do 1
    assert out.call_todo_indices() == [0, 1]
    assert out.op_todo_indices() == [1]


def test_live_planner_flat_plan_still_becomes_a_single_todo():
    """Back-compat: a model that ignores `todos` and emits flat calls/ops yields one synthesized
    to-do (the plan still reads as a checklist), the flat path is byte-for-byte unchanged."""
    g = _spine_graph()
    planner = LivePlanner(client=None, catalog_text="", tools_text="",
                          tool_intents={"active_alerts"}, verbose=False)
    planner.graph = g
    raw = {"reasoning": "r", "calls": [{"intent": "active_alerts", "params": {}}], "ops": [],
           "narrative": "n", "verdict": {"status": "advance"}}
    out = planner._to_plan_output(_ctx_for(g), raw)
    assert out.todos == []                     # none authored — flat path
    assert len(out.effective_todos) == 1       # but the plan reads as one to-do
    assert [c.intent for c in out.calls] == ["active_alerts"]
    assert out.call_todo_indices() == [0]


def test_live_planner_reject_repair_inside_a_todo():
    """The reject+repair guard is per-to-do too: an off-catalog call + an unparseable op inside a
    to-do are DROPPED (recorded as repairs), the valid call survives — never a reducer rejection."""
    g = _spine_graph()
    planner = LivePlanner(client=None, catalog_text="", tools_text="",
                          tool_intents={"active_alerts"}, verbose=False)
    planner.graph = g
    raw = {"reasoning": "r",
           "todos": [{"objective": "mixed", "calls": [{"intent": "active_alerts"},
                                                      {"intent": "nonexistent_tool"}],
                      "ops": [{"op": "bogus_op"}]}],
           "narrative": "n", "verdict": {"status": "advance"}}
    out = planner._to_plan_output(_ctx_for(g), raw)
    assert [c.intent for c in out.todos[0].calls] == ["active_alerts"]   # off-catalog dropped
    assert out.todos[0].ops == []                                        # unparseable dropped
    assert any("nonexistent_tool" in r for r in out.repairs)
    assert any("bogus_op" in r for r in out.repairs)


def test_engine_hands_the_focus_slice_every_phase():
    """The engine enriches EVERY PlanContext with the focus slice (B9.3 invariant held), and
    binds the focus to the symptom node from the phase after FRAME onward."""
    import pathlib

    from e2e import scenario_database

    import iw_engine
    from iw_engine.capability import CapabilityLayer, MockSource
    from iw_engine.capability.adapters import default_adapters
    from iw_engine.runtime import Engine, ScriptedPlanner, load_playbook

    class _Probe:
        def __init__(self, inner):
            self.inner, self.seen = inner, []

        def plan(self, ctx):
            self.seen.append((ctx.phase, ctx.focus))
            return self.inner.plan(ctx)

    subject, script, fixtures = scenario_database.build()
    pb = load_playbook(pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml")
    probe = _Probe(ScriptedPlanner(script))
    layer = CapabilityLayer(default_adapters(), source=MockSource(fixtures))
    res = Engine(pb, probe, clock=lambda: datetime(2026, 7, 19, tzinfo=UTC), layer=layer).run(subject)

    assert res.close_outcome == "resolved"                 # the probe changed nothing
    assert [p for p, _ in probe.seen] == ["frame", "investigate", "investigate", "act",
                                          "verify", "close"]
    first = probe.seen[0][1]
    assert first["total"] == 0 and first["focus"] is None  # before FRAME: empty, focus-less
    for phase_id, sl in probe.seen[1:]:
        assert sl["focus"] == "anomaly:anom-1", phase_id   # symptom bound from then on
        assert len(sl["nodes"]) + len(sl["frontier"]) + sl["collapsed_count"] == sl["total"]


# ── SchemaUsability: the planner-prompt INTUITION (§8) + span authoring (§8.1) ──────────
def test_output_contract_advertises_the_six_species_and_a_span_example():
    from iw_engine.runtime import live_planner as lp

    assert "identity|property|state|reading|event|span" in lp._OUTPUT_CONTRACT
    assert '"species":"span"' in lp._OUTPUT_CONTRACT and "correlation_id" in lp._OUTPUT_CONTRACT
    assert "node OR edge id" in lp._OUTPUT_CONTRACT           # a span's subject may be either
    # still domain-neutral — the router is engine MECHANICS; the doctrine owns the incident vocab
    for literal in ("onset_value", "anomaly", "healthrule_violations"):
        assert literal not in lp._OUTPUT_CONTRACT


def test_system_prompt_teaches_the_router_the_handle_ladder_and_the_link_rule():
    from iw_engine.runtime.live_planner import _default_doctrine, render_system

    sysp = render_system(_default_doctrine())
    # the six-category router keyed on temporal EXTENT + the property-vs-state tie-break
    assert "SIX DATUM CATEGORIES" in sysp and "temporal EXTENT" in sysp
    assert "TIE-BREAK property-vs-state" in sysp and "choose STATE" in sysp
    # the ONE handle-ladder rule (metrics/logs/spans identical)
    assert "NEVER inline a raw high-volume stream" in sysp
    assert "metric_query" in sysp and "log_link" in sysp and "trace_id" in sysp
    # the link rule: cite an id of ANY species; the span overlap join; the ABANDONED honesty rule
    assert "CITING its id" in sysp and "ANY species" in sysp
    assert "CONTAINS onset" in sysp and "ABANDONED" in sysp


def test_live_planner_authors_a_span_on_a_node_or_an_edge_subject():
    from iw_engine.domain.enums import Species

    planner = LivePlanner(client=None, catalog_text="", tools_text="", tool_intents=set(),
                          verbose=False)
    # a CLOSED node-subject span (started+ended) with a correlation_id
    op, err = planner._parse_op({
        "op": "add_assertion", "subject": "business_transaction:checkout", "name": "trace",
        "species": "span", "started_at": "2026-07-19T10:00:00+00:00",
        "ended_at": "2026-07-19T10:00:01+00:00", "correlation_id": "trace-abc", "source": "appd"})
    assert err is None and isinstance(op, AddAssertion) and op.species is Species.SPAN
    assert op.valid_from is not None and op.valid_to is not None and op.correlation_id == "trace-abc"
    # an OPEN (in-flight) span: no ended_at -> valid_to stays None (engine sets span_phase=OPEN,
    # never the default_at fallback that would silently CLOSE it)
    op2, err2 = planner._parse_op({
        "op": "add_assertion", "subject": "service:orders-api|prod", "name": "hop",
        "species": "span", "started_at": "2026-07-19T10:00:00+00:00", "source": "appd"})
    assert err2 is None and op2.species is Species.SPAN and op2.valid_to is None
    # a Rung-1 hop addressed to an EDGE subject is legal (no node type to reject against)
    op3, err3 = planner._parse_op({
        "op": "add_assertion", "subject": "edge:calls:svc->db:discovered", "name": "hop",
        "species": "span", "started_at": "2026-07-19T10:00:00+00:00", "source": "appd"})
    assert err3 is None and op3.subject == "edge:calls:svc->db:discovered"


def test_prompt_surfaces_the_change_trail_and_spans_query_grammar():
    """The GRAPH PROJECTIONS render the SchemaUsability query grammar for the symptom's affected
    surface: trail_of (change-trail incl. STATE version-boundaries — §9 fix J) and spans_of
    (span_phase ALWAYS shown — §4.6)."""
    from datetime import UTC, datetime

    from iw_engine.domain.assertion import Assertion
    from iw_engine.domain.enums import Channel, SpanPhase, Species
    from iw_engine.domain.fact import Fact

    g = _spine_graph()
    svc = "service:orders-api|prod"
    ts = datetime(2026, 7, 19, 13, 50, tzinfo=UTC)
    g.add_fact(Fact(id="fv", subject_ref=svc, predicate="tier", value="gold", valid_from=ts,
                    observed_at=ts, source=Source.CMDB, source_reliability=0.9, created_by=1))
    g.add_span(Assertion(id="span:r", subject_ref=svc, name="trace", species=Species.SPAN,
                         channel=Channel.MEASURED, valid_from=ts, valid_to=ts, observed_at=ts,
                         span_phase=SpanPhase.CLOSED, source=Source.APPD, source_reliability=0.9,
                         created_by=1))
    planner = LivePlanner(client=None, catalog_text="# CAT", tools_text="# TOOLS",
                          tool_intents=set(), verbose=False)
    planner.graph = g
    prompt = planner._build_prompt(_ctx_for(g))
    assert f"change-trail of {svc}:" in prompt
    assert "tier=gold" in prompt                 # the STATE version-boundary surfaced (§9 fix J)
    assert f"spans on {svc}:" in prompt
    assert "trace[closed]" in prompt             # span_phase ALWAYS exposed (§4.6)


def test_catalog_advertises_the_query_grammar_and_the_category_split():
    import pathlib

    import iw_engine
    from iw_engine.domain import registry
    from iw_engine.domain.catalog import render_catalog
    from iw_engine.runtime.loader import load_playbook

    pb = load_playbook(pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml")
    cat = render_catalog(registry, pb)
    assert "QUERY GRAMMAR" in cat and "state as-of-T" in cat and "change-trail" in cat
    assert "span_phase ALWAYS shown" in cat
    # the per-type category split (the §2 typed-schema step) rides in the NODE TYPES section
    assert "reading=[" in cat and "span=[" in cat and "state=[" in cat
