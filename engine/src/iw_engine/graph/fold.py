"""fold — the single mutation monopoly (principle 2). A phase returns a PhaseResult delta;
ONLY fold() writes it into the three projections. `rebuild()` replays the journal's
full-delta phase entries to reconstruct graph + hypothesis store from scratch — the proof
that the journal is the durable source of truth (DESIGN §2.4 R-J1).
"""
from __future__ import annotations

from ..domain.edge import Edge
from ..domain.enums import EdgeType, FactState, NodeType, Origin
from ..domain.hypothesis import Hypothesis
from ..domain.phase_result import PhaseResult
from ..domain.registry import edge_allowed, edge_id
from ..hypothesis.store import HypothesisStore
from ..journal.journal import Journal
from .graph import Graph


def _apply_to_graph(result: PhaseResult, graph: Graph) -> None:
    for n in result.nodes_touched:      # nodes first — facts/edges reference node ids
        graph.upsert_node(n)
    for f in result.facts_added:
        graph.add_fact(f)
    for e in result.events_added:
        graph.add_event(e)
    for e in result.edges_added:
        graph.add_edge(e)


def _project_evidence_edges(h: Hypothesis, seq: int, graph: Graph) -> None:
    """Recompute a hypothesis's SUPPORTS/REFUTES graph edges FROM its canonical evidence
    fact-id lists (VALIDATION-VERDICT §B P0 #1 — the Fact is the one addressable evidence
    unit). Each edge is a thin projection: fact.subject -> hypothesis, derived, never
    planner-emitted, so the graph view can never disagree with the store. Runs inside the
    single mutation seam, so journal replay reproduces it bit-for-bit.

    The projection is a full RECONCILIATION, not append-only (audit finding #2): an active
    inferred SUPPORTS/REFUTES edge into this hypothesis whose backing fact-id has LEFT the list
    (the list SHRANK) is TOMBSTONED (state=RETRACTED), so the graph can never keep asserting
    evidence the store no longer holds. Facts not (yet) materialised, or whose subject is not a
    graph node, are simply not projected."""
    desired: set[str] = set()
    to_add: list[tuple[EdgeType, str, str]] = []   # (etype, eid, subj_id) in delta order
    for etype, fact_ids in ((EdgeType.SUPPORTS, h.supporting_facts),
                            (EdgeType.REFUTES, h.refuting_facts)):
        for fid in fact_ids:
            fact = graph.facts.get(fid)
            if fact is None:
                continue
            subj = graph.node(fact.subject_ref)
            if subj is None or subj.type == NodeType.HYPOTHESIS:
                continue
            if not edge_allowed(etype, subj.type, NodeType.HYPOTHESIS):
                continue
            eid = edge_id(etype, subj.id, h.id, Origin.INFERRED)
            desired.add(eid)
            to_add.append((etype, eid, subj.id))
    # retract stale projections FIRST (deterministic id order for replay): an active inferred
    # evidence edge into this hypothesis that is no longer desired lost its backing fact.
    for e in sorted(graph.in_edges(h.id), key=lambda e: e.id):
        if (e.type in (EdgeType.SUPPORTS, EdgeType.REFUTES) and e.origin == Origin.INFERRED
                and e.state == FactState.ACTIVE and e.id not in desired):
            graph.retract_edge(e.id, invalidated_by=h.id)
    # (re)assert the backed edges — add_edge is idempotent by id, so an already-live edge is
    # refreshed and a previously-retracted one is revived if its backing fact returned.
    for etype, eid, subj_id in to_add:
        graph.add_edge(Edge(id=eid, type=etype, src=subj_id, dst=h.id,
                            origin=Origin.INFERRED, confidence=h.confidence,
                            created_by=seq))


def apply_delta(result: PhaseResult, seq: int, graph: Graph, store: HypothesisStore) -> None:
    """THE single graph+hypothesis-store mutation seam. A phase computes a PhaseResult delta;
    this is the only thing that writes it into the projections. Journaling is separate (below)
    so an interactive write-gate can hold a computed-but-unapplied delta pending human approval."""
    _apply_to_graph(result, graph)
    store.apply(result.hypotheses_updated, seq)
    # project the evidence edges of every hypothesis this delta touched, from the store's
    # (now-updated) canonical fact-id lists — the single source of "facts for/against H".
    # Dedup in delta order (never a set — iteration order must be deterministic for replay).
    touched: list[str] = []
    for d in result.hypotheses_updated:
        hid = d.hypothesis.id if d.hypothesis else d.hypothesis_id
        if hid and hid not in touched:
            touched.append(hid)
    for hid in touched:
        h = store.hypotheses.get(hid)
        if h is not None:
            _project_evidence_edges(h, seq, graph)


def fold(result: PhaseResult, seq: int, graph: Graph, store: HypothesisStore,
         journal: Journal) -> None:
    apply_delta(result, seq, graph, store)
    journal.append_phase(seq, result)


def rebuild(journal: Journal) -> tuple[Graph, HypothesisStore]:
    """Replay the journal → (graph, hypothesis store). Must equal the live projections."""
    graph, store = Graph(), HypothesisStore()
    for entry in journal.phase_entries():
        assert entry.delta is not None
        apply_delta(entry.delta, entry.seq, graph, store)
    return graph, store
