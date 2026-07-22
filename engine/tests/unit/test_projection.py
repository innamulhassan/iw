"""Projection-layer tests — graph (bi-temporal, idempotent), journal (full-delta replay),
ledger (apply/rank/promotion), and the fold's replay-equivalence guarantee. No registry
needed: elements are constructed directly.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from iw_engine.domain.common import Confidence
from iw_engine.domain.edge import Edge
from iw_engine.domain.enums import (
    EdgeType,
    FactState,
    GateResult,
    HypothesisStatus,
    NodeType,
    Origin,
    Source,
    VerdictStatus,
)
from iw_engine.domain.event import Event
from iw_engine.domain.fact import Fact
from iw_engine.domain.hypothesis import HypAction, HypDelta, Hypothesis
from iw_engine.domain.node import Node
from iw_engine.domain.phase_result import PhaseResult, PhaseVerdict
from iw_engine.graph import Graph, fold, rebuild
from iw_engine.hypothesis import HypothesisStore
from iw_engine.journal import Journal

T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)


def make_service(name="payments-api", env="prod", seq=1) -> Node:
    return Node(id=f"service:{name}|{env}", type=NodeType.SERVICE,
                props={"service_name": name, "env": env}, created_by=seq)


def make_fact(subject, predicate, value, ts=T0, seq=1, fid=None) -> Fact:
    return Fact(id=fid or f"fact:{subject}:{predicate}:{ts.isoformat()}", subject_ref=subject,
                predicate=predicate, value=value, valid_from=ts, observed_at=ts,
                source=Source.PROMETHEUS, source_reliability=0.95, created_by=seq)


# ── graph ─────────────────────────────────────────────────────────────────────
def test_upsert_is_idempotent_and_merges_props():
    g = Graph()
    g.upsert_node(make_service())
    g.upsert_node(Node(id="service:payments-api|prod", type=NodeType.SERVICE,
                       props={"tier": "gold"}, created_by=2))
    assert len(g) == 1
    n = g.node("service:payments-api|prod")
    assert n.props == {"service_name": "payments-api", "env": "prod", "tier": "gold"}


def test_fact_supersede_closes_the_window():
    g = Graph()
    sid = "service:payments-api|prod"
    g.upsert_node(make_service())
    f1 = make_fact(sid, "red_errors", 0.02, ts=T0, fid="f1")
    g.add_fact(f1)
    f2 = make_fact(sid, "red_errors", 0.40, ts=T0 + timedelta(minutes=5), fid="f2")
    f2 = f2.model_copy(update={"supersedes": "f1"})
    g.add_fact(f2)
    assert g.facts["f1"].state == FactState.SUPERSEDED
    assert g.facts["f1"].valid_to == T0 + timedelta(minutes=5)
    active = g.facts_of(sid)
    assert [f.id for f in active] == ["f2"]


def test_point_in_time_query():
    g = Graph()
    sid = "service:payments-api|prod"
    g.upsert_node(make_service())
    f1 = make_fact(sid, "red_errors", 0.02, ts=T0, fid="f1").model_copy(
        update={"valid_to": T0 + timedelta(minutes=5)})
    f2 = make_fact(sid, "red_errors", 0.40, ts=T0 + timedelta(minutes=5), fid="f2")
    g.add_fact(f1)
    g.add_fact(f2)
    at_early = {f.id for f in g.facts_valid_at(T0 + timedelta(minutes=1))}
    at_late = {f.id for f in g.facts_valid_at(T0 + timedelta(minutes=10))}
    assert at_early == {"f1"}
    assert at_late == {"f2"}


def test_point_in_time_reconstructs_superseded_value():
    """INV-5 regression (2026-07-22 review, finding 1): after a fact is superseded via the
    REAL supersede path (g.add_fact with supersedes=..., not a manual valid_to), the
    point-in-time query must still return the superseded fact for instants inside its
    closed window — else the graph is no longer reconstructable as of incident-start."""
    g = Graph()
    sid = "service:payments-api|prod"
    g.upsert_node(make_service())
    g.add_fact(make_fact(sid, "red_errors", 0.02, ts=T0, fid="f1"))
    f2 = make_fact(sid, "red_errors", 0.40, ts=T0 + timedelta(minutes=5), fid="f2")
    g.add_fact(f2.model_copy(update={"supersedes": "f1"}))
    assert {f.id for f in g.facts_valid_at(T0 + timedelta(minutes=1))} == {"f1"}
    assert {f.id for f in g.facts_valid_at(T0 + timedelta(minutes=10))} == {"f2"}


def test_point_in_time_excludes_retracted():
    """RETRACTED facts (disavowed observations) stay excluded from point-in-time history."""
    g = Graph()
    sid = "service:payments-api|prod"
    g.upsert_node(make_service())
    g.add_fact(make_fact(sid, "red_errors", 0.02, ts=T0, fid="f1"))
    g.retract_fact("f1")
    assert g.facts_valid_at(T0 + timedelta(minutes=1)) == []


def test_supersede_clamps_backdated_window():
    """Finding 18: a back-dated correction (new.valid_from < old.valid_from) must not
    persist an inverted window (valid_to < valid_from). The close is clamped to
    old.valid_from — a zero-length window: no instant at which the old value was true."""
    g = Graph()
    sid = "service:payments-api|prod"
    g.upsert_node(make_service())
    g.add_fact(make_fact(sid, "degraded", True, ts=T0 + timedelta(minutes=5), fid="f1"))
    corrected = make_fact(sid, "degraded", True, ts=T0, fid="f2").model_copy(
        update={"supersedes": "f1"})
    g.add_fact(corrected)
    old = g.facts["f1"]
    assert old.state == FactState.SUPERSEDED
    assert old.valid_to == old.valid_from            # clamped, not inverted
    assert old.valid_to >= old.valid_from            # the Fact window invariant holds
    # the zero-length window means f1 is the truth at NO instant; f2 covers from T0 on
    assert {f.id for f in g.facts_valid_at(T0 + timedelta(minutes=6))} == {"f2"}


def test_edges_and_traversal():
    g = Graph()
    g.upsert_node(make_service("checkout"))
    g.upsert_node(make_service("payments-api"))
    e = Edge(id="edge:depends_on:a->b:declared", type=EdgeType.DEPENDS_ON,
             src="service:checkout|prod", dst="service:payments-api|prod",
             origin=Origin.DECLARED, created_by=1)
    g.add_edge(e)
    assert g.neighbors("service:checkout|prod", EdgeType.DEPENDS_ON) == ["service:payments-api|prod"]
    assert g.in_edges("service:payments-api|prod")[0].type == EdgeType.DEPENDS_ON


def test_graph_roundtrip():
    g = Graph()
    g.upsert_node(make_service())
    g.add_fact(make_fact("service:payments-api|prod", "red_errors", 0.4, fid="f1"))
    g2 = Graph.from_dict(g.to_dict())
    assert g2.to_dict() == g.to_dict()


def test_fact_belief_channel_invariant():
    """R-C4 (VALIDATION-VERDICT §B P0 #3): which belief channel is meaningful is fixed by
    provenance — a MEASURED fact carries source_reliability, an INFERRED (source=llm) fact
    carries confidence, and carrying the wrong one (or neither/both) is a hard error."""
    base = dict(id="fact:x", subject_ref="service:s|prod", predicate="red_errors", value=0.4,
                valid_from=T0, observed_at=T0, created_by=1)
    # measured: source_reliability OK; a confidence on it is rejected
    Fact(**base, source=Source.PROMETHEUS, source_reliability=0.9)
    with pytest.raises(ValidationError):
        Fact(**base, source=Source.PROMETHEUS)  # measured but no reliability
    with pytest.raises(ValidationError):
        Fact(**base, source=Source.PROMETHEUS, source_reliability=0.9,
             confidence=Confidence(value=0.9, basis="x"))
    # inferred: confidence OK; source_reliability on it is rejected
    Fact(**base, source=Source.LLM, confidence=Confidence(value=0.7, basis="reasoned"))
    with pytest.raises(ValidationError):
        Fact(**base, source=Source.LLM)  # inferred but no confidence
    with pytest.raises(ValidationError):
        Fact(**base, source=Source.LLM, source_reliability=0.9,
             confidence=Confidence(value=0.7, basis="x"))


def test_reducer_soft_rejects_belief_channel_violation():
    """The Fact model still RAISES on a belief-channel violation (R-C4 invariant, tested above),
    but the reducer must not crash the run when a malformed fact reaches it — it records a
    Rejection and continues, exactly like every other malformed op. This is the engine's
    resilience contract: a single bad op never kills the investigation."""
    from iw_engine.domain.operations import AddFact, AddNode
    from iw_engine.domain.playbook import Tunables
    from iw_engine.graph.reducer import materialize

    g = Graph()
    sid = "service:payments-api|prod"
    tun = Tunables()
    # seed the service node so the fact's subject is known (passes the known() check)
    seed = materialize([AddNode(type=NodeType.SERVICE,
                                props={"service_name": "payments-api", "env": "prod"})],
                       1, g, tun)
    # apply the seed the way the engine does (fold adds nodes to the graph)
    for n in seed.nodes:
        g.upsert_node(n)

    # a fact violating R-C4: a measured source (PROMETHEUS) carrying BOTH belief channels
    bad = AddFact(subject=sid, predicate="red_errors", value=0.4, valid_from=T0, observed_at=T0,
                  source=Source.PROMETHEUS, source_reliability=0.9, confidence_level="high")
    mat = materialize([bad], 2, g, tun)
    assert len(mat.facts) == 0                       # the bad fact did NOT land
    assert len(mat.rejections) == 1                  # it was recorded instead
    assert "invalid fact" in mat.rejections[0].reason


def test_retract_edge_and_event_tombstone():
    """Edge + Event lifecycle (VALIDATION-VERDICT §B P0 #2): a refuted inferred edge and a
    wrong telemetry event are tombstoned (state=RETRACTED), never mutated/deleted — symmetric
    with Fact retraction and defaulting ACTIVE."""
    g = Graph()
    g.upsert_node(make_service("checkout"))
    g.upsert_node(make_service("payments-api"))
    e = Edge(id="edge:caused_by:a->b:inferred", type=EdgeType.CAUSED_BY,
             src="service:checkout|prod", dst="service:payments-api|prod",
             origin=Origin.INFERRED, confidence=Confidence(value=0.6, basis="onset match"),
             created_by=1)
    g.add_edge(e)
    ev = Event(id="evt:1", entity_ref="service:checkout|prod", type="config_changed",
               occurred_at=T0, observed_at=T0, source=Source.OCP, created_by=1)
    g.add_event(ev)
    assert g.edges[e.id].state == FactState.ACTIVE and g.events[ev.id].state == FactState.ACTIVE

    g.retract_edge(e.id, invalidated_by="hyp:h1", at=T0 + timedelta(minutes=5))
    g.retract_event(ev.id, invalidated_by="fact:f9")
    assert g.edges[e.id].state == FactState.RETRACTED
    assert g.edges[e.id].invalidated_by == "hyp:h1"
    assert g.edges[e.id].valid_to == T0 + timedelta(minutes=5)
    assert g.events[ev.id].state == FactState.RETRACTED
    assert g.events[ev.id].invalidated_by == "fact:f9"


# ── ledger ─────────────────────────────────────────────────────────────────────
def _hyp(hid="hyp:h1", conf=0.5, status=HypothesisStatus.PROPOSED):
    return Hypothesis(id=hid, statement="config push dropped header",
                      confidence=Confidence(value=conf, basis="onset matches change"),
                      created_by=1, status=status)


def test_ledger_apply_and_rank():
    led = HypothesisStore()
    led.apply([HypDelta(action=HypAction.CREATE, hypothesis=_hyp("hyp:h1", 0.4))], seq=1)
    led.apply([HypDelta(action=HypAction.CREATE, hypothesis=_hyp("hyp:h2", 0.7))], seq=2)
    led.apply([HypDelta(action=HypAction.ATTACH_EVIDENCE, hypothesis_id="hyp:h1",
                        add_refuting=["f9"])], seq=3)
    assert led.leading().id == "hyp:h2"
    assert "f9" in led.hypotheses["hyp:h1"].refuting_facts


def test_recreate_preserves_refuted_and_evidence():
    """Track-4 #1 regression (audit §1.3): a live planner re-proposing (CREATE) an id that is
    already REFUTED must NOT resurrect it as PROPOSED, wipe its refuting evidence, or reset its
    created_by audit stamp — a refuted verdict is indestructible (it IS evidence)."""
    store = HypothesisStore()
    store.apply([HypDelta(action=HypAction.CREATE, hypothesis=_hyp("hyp:h1", 0.6))], seq=1)
    store.apply([HypDelta(action=HypAction.ATTACH_EVIDENCE, hypothesis_id="hyp:h1",
                          add_refuting=["f9"])], seq=2)
    store.apply([HypDelta(action=HypAction.REFUTE, hypothesis_id="hyp:h1")], seq=3)
    assert store.hypotheses["hyp:h1"].status == HypothesisStatus.REFUTED

    # the destructive case: a fresh PROPOSED CREATE re-using the SAME id (the REPEAT loop)
    store.apply([HypDelta(action=HypAction.CREATE,
                          hypothesis=_hyp("hyp:h1", 0.9, status=HypothesisStatus.PROPOSED))], seq=4)
    h = store.hypotheses["hyp:h1"]
    assert h.status == HypothesisStatus.REFUTED          # NOT reset to proposed — indestructible
    assert "f9" in h.refuting_facts                      # accumulated evidence survived
    assert h.confidence.value == 0.6                     # terminal record untouched (no-op)
    assert h.created_by == 1                             # original audit stamp kept


def test_recreate_of_live_hid_merges_not_overwrites():
    """A re-CREATE of a still-LIVE hid is an update in place, never a destructive reset: the
    accumulated status + evidence survive, descriptive fields refresh, evidence only grows."""
    store = HypothesisStore()
    store.apply([HypDelta(action=HypAction.CREATE, hypothesis=_hyp("hyp:h1", 0.4))], seq=1)
    store.apply([HypDelta(action=HypAction.ATTACH_EVIDENCE, hypothesis_id="hyp:h1",
                          add_supporting=["f1"], new_status=HypothesisStatus.SUPPORTED)], seq=2)
    store.apply([HypDelta(action=HypAction.CREATE,
                          hypothesis=_hyp("hyp:h1", 0.7).model_copy(
                              update={"statement": "refined theory"}))], seq=3)
    h = store.hypotheses["hyp:h1"]
    assert h.status == HypothesisStatus.SUPPORTED        # preserved, not reset to proposed
    assert "f1" in h.supporting_facts                    # accumulated evidence survives
    assert h.statement == "refined theory"               # descriptive fields refresh
    assert h.confidence.value == 0.7
    assert h.created_by == 1 and 3 in h.updated_by       # audit preserved + extended


def test_ledger_promotion_gate():
    led = HypothesisStore()
    led.apply([HypDelta(action=HypAction.CREATE, hypothesis=_hyp("hyp:h1", 0.9))], seq=1)
    led.apply([HypDelta(action=HypAction.CREATE, hypothesis=_hyp("hyp:h2", 0.3))], seq=2)
    from iw_engine.domain.playbook import Tunables
    tun = Tunables(confidence_gate=0.8, delta=0.15)
    # ANY alive rival blocks — even a weak one must be refuted, not out-scored
    # (2026-07-22 review, finding 2: the old >=gate filter let sub-gate rivals slip by)
    assert led.promotion_ok(tun) is False
    # a strong rival blocks promotion too
    led.apply([HypDelta(action=HypAction.RERANK, hypothesis_id="hyp:h2",
                        confidence=Confidence(value=0.85, basis="rival evidence"))], seq=3)
    assert led.promotion_ok(tun) is False
    # only REFUTING the rival clears the field and unblocks promotion
    led.apply([HypDelta(action=HypAction.REFUTE, hypothesis_id="hyp:h2")], seq=4)
    assert led.promotion_ok(tun) is True


def test_ledger_promotion_blocked_by_equal_rivals():
    """Review scenario: two 0.9 rivals — margin 0 < delta AND a live rival — must not promote."""
    led = HypothesisStore()
    led.apply([HypDelta(action=HypAction.CREATE, hypothesis=_hyp("hyp:h1", 0.9))], seq=1)
    led.apply([HypDelta(action=HypAction.CREATE, hypothesis=_hyp("hyp:h2", 0.9))], seq=2)
    from iw_engine.domain.playbook import Tunables
    assert led.promotion_ok(Tunables(confidence_gate=0.8, delta=0.15)) is False


def test_ledger_promotion_blocked_by_sub_gate_rival():
    """Review scenario: an unrefuted 0.75 rival under a 0.8 gate must block promotion."""
    led = HypothesisStore()
    led.apply([HypDelta(action=HypAction.CREATE, hypothesis=_hyp("hyp:h1", 0.9))], seq=1)
    led.apply([HypDelta(action=HypAction.CREATE, hypothesis=_hyp("hyp:h2", 0.75))], seq=2)
    from iw_engine.domain.playbook import Tunables
    assert led.promotion_ok(Tunables(confidence_gate=0.8, delta=0.15)) is False


# ── fold / journal replay-equivalence ──────────────────────────────────────────
def _phase_result() -> PhaseResult:
    sid = "service:payments-api|prod"
    return PhaseResult(
        phase_id="frame",
        goal_restated="normalize the signal",
        nodes_touched=[make_service()],
        facts_added=[make_fact(sid, "red_errors", 0.4, fid="f1")],
        edges_added=[],
        hypotheses_updated=[HypDelta(action=HypAction.CREATE, hypothesis=_hyp("hyp:h1", 0.5))],
        narrative="payments-api error rate jumped to 40% at 14:00.",
        verdict=PhaseVerdict(status=VerdictStatus.ADVANCE,
                             confidence=Confidence(value=0.6, basis="clear onset"),
                             gate_result=GateResult.PASS),
    )


def test_stale_evidence_edge_is_retracted_when_list_shrinks():
    """Audit finding #2: the fold's SUPPORTS/REFUTES projection is a RECONCILIATION, not
    append-only. When a hypothesis's supporting/refuting fact-id list SHRINKS, the now-unbacked
    derived edge must be TOMBSTONED (state=RETRACTED) so the graph never asserts evidence the
    store no longer holds — else the graph view can disagree with the hypothesis store."""
    from iw_engine.domain.registry import edge_id
    from iw_engine.graph.fold import _project_evidence_edges

    g = Graph()
    sid = "service:payments-api|prod"
    g.upsert_node(make_service())
    g.add_fact(make_fact(sid, "red_errors", 0.4, fid="f1"))
    g.upsert_node(Node(id="hyp:h1", type=NodeType.HYPOTHESIS,
                       props={"statement": "x"}, created_by=1))
    eid = edge_id(EdgeType.SUPPORTS, sid, "hyp:h1", Origin.INFERRED)

    backed = _hyp("hyp:h1").model_copy(update={"supporting_facts": ["f1"]})
    _project_evidence_edges(backed, 1, g)
    assert g.edges[eid].state == FactState.ACTIVE          # projected while backed by f1

    # the list shrinks: f1 no longer backs h1 — the derived edge must be tombstoned, not left stale
    unbacked = _hyp("hyp:h1").model_copy(update={"supporting_facts": []})
    _project_evidence_edges(unbacked, 2, g)
    assert g.edges[eid].state == FactState.RETRACTED
    assert g.edges[eid].invalidated_by == "hyp:h1"

    # and it REVIVES (idempotent by id) if the backing fact returns to the list
    _project_evidence_edges(backed, 3, g)
    assert g.edges[eid].state == FactState.ACTIVE


def test_fold_and_replay_equivalence():
    clock = lambda: T0  # noqa: E731 - deterministic ts
    g, led, jr = Graph(), HypothesisStore(), Journal(clock=clock)
    seq = jr.reserve_seq()
    fold(_phase_result(), seq, g, led, jr)
    # rebuild purely from the journal
    g2, led2 = rebuild(jr)
    assert g2.to_dict() == g.to_dict()
    assert {k: v.model_dump() for k, v in led2.hypotheses.items()} == \
           {k: v.model_dump() for k, v in led.hypotheses.items()}


def test_journal_ndjson_roundtrip_skips_partial_tail():
    clock = lambda: T0  # noqa: E731
    jr = Journal(clock=clock)
    fold(_phase_result(), jr.reserve_seq(), Graph(), HypothesisStore(), jr)
    text = jr.to_ndjson()
    # simulate a crash mid-write: a truncated trailing line
    corrupted = text + '{"seq": 2, "ts": "2026'
    jr2 = Journal.from_ndjson(corrupted)
    assert len(jr2.entries) == 1
    assert jr2.entries[0].delta.facts_added[0].value == 0.4


def test_journal_crash_resume_truncated_tail_preserves_seq_watermark():
    """Crash-resume (R-J4): write two entries, then truncate the TRAILING line mid-write
    (the crash cut the last entry in half). from_ndjson must (1) skip the partial tail,
    (2) keep every complete entry, and (3) reset the seq watermark to the surviving max —
    so the next reserve_seq() re-issues the LOST seq instead of skipping or colliding."""
    clock = lambda: T0  # noqa: E731
    jr = Journal(clock=clock)
    jr.append_phase(jr.reserve_seq(), _phase_result())     # seq 1 — survives
    jr.append_phase(jr.reserve_seq(), _phase_result())     # seq 2 — will be truncated
    text = jr.to_ndjson()
    lines = text.rstrip("\n").split("\n")
    truncated = "\n".join([*lines[:-1], lines[-1][: len(lines[-1]) // 2]])

    jr2 = Journal.from_ndjson(truncated)
    assert [e.seq for e in jr2.entries] == [1]             # partial entry 2 dropped
    assert jr2.reserve_seq() == 2                          # watermark = surviving max + 1


def test_journal_clean_resume_preserves_seq_watermark():
    """A clean roundtrip resumes numbering AFTER the last persisted entry (no reuse)."""
    clock = lambda: T0  # noqa: E731
    jr = Journal(clock=clock)
    jr.append_phase(jr.reserve_seq(), _phase_result())
    jr.append_phase(jr.reserve_seq(), _phase_result())
    jr2 = Journal.from_ndjson(jr.to_ndjson())
    assert [e.seq for e in jr2.entries] == [1, 2]
    assert jr2.reserve_seq() == 3


def test_journal_mid_file_corruption_raises_not_skips():
    """Only a TRAILING partial line is crash-forgivable. A corrupted line in the MIDDLE
    of the file (real damage, not a mid-write crash) must raise loudly — silently
    dropping an interior entry would corrupt the source of truth."""
    import json as _json

    clock = lambda: T0  # noqa: E731
    jr = Journal(clock=clock)
    jr.append_phase(jr.reserve_seq(), _phase_result())
    jr.append_phase(jr.reserve_seq(), _phase_result())
    lines = jr.to_ndjson().rstrip("\n").split("\n")
    lines[1] = lines[1][: len(lines[1]) // 2]              # damage entry 1, entry 2 intact after it
    with pytest.raises(_json.JSONDecodeError):
        Journal.from_ndjson("\n".join(lines) + "\n")
