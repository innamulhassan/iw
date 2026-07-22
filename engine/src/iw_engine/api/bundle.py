"""export_bundle — flatten a completed run's three projections into ONE JSON document the
React workbench renders (GraphView · JournalTimeline · HypothesisPanel · PhaseController).
Derived purely from the graph/hypothesis-store/journal, so the UI cannot show anything the
engine did not record.
"""
from __future__ import annotations

from ..domain.enums import NodeType
from ..domain.registry import node_id
from ..graph.graph import Graph
from ..hypothesis.store import HypothesisStore
from ..journal.journal import Journal, JournalEntry
from ..runtime.engine import RunResult
from ..runtime.postmortem import render_postmortem


def _node_provenance(nid: str, g: Graph) -> dict:
    """Derive a node's provenance (obs 5) as a pure projection of its facts+events — WHERE it was
    fetched from (which capability first observed it) and WHEN first/last seen. No redundant
    storage: the node's source IS which datasource observed it (the AppD/Dynatrace entity model),
    and because facts are journaled this is deterministic + replay-stable. Tie-break on id."""
    obs: list[tuple] = []
    for f in g.facts.values():
        if f.subject_ref == nid:
            obs.append((f.observed_at, f.id, f.source.value))
    for e in g.events.values():
        if e.entity_ref == nid:
            obs.append((e.observed_at, e.id, e.source.value))
    if not obs:
        return {"source": None, "first_source": None, "first_seen": None, "last_seen": None}
    obs.sort(key=lambda x: (x[0], x[1]))
    return {"source": obs[-1][2], "first_source": obs[0][2],
            "first_seen": obs[0][0].isoformat(), "last_seen": obs[-1][0].isoformat()}


def _journal_entry(e: JournalEntry) -> dict:
    """Flatten one journal entry for the workbench timeline. Phase entries keep their existing
    shape (unchanged goldens); step entries additionally carry the gate decision + approver."""
    if e.kind == "step":
        return {"seq": e.seq, "kind": "step", "phase": e.phase_id.value if e.phase_id else None,
                "actor": e.actor, "source": e.source.value if e.source else None,
                "decision": e.decision, "intent": e.intent, "narrative": e.reasoning,
                "action": e.action}
    return {"seq": e.seq, "phase": e.phase_id.value if e.phase_id else None,
            "actor": e.actor, "narrative": e.reasoning, "refs": e.refs}


def export_bundle(res: RunResult) -> dict:
    g: Graph = res.graph
    store: HypothesisStore = res.hypothesis_store
    jr: Journal = res.journal
    # the ServiceNow incident under investigation is the ORIGIN → renders as node #1 (obs 1)
    origin_id = node_id(NodeType.INCIDENT, {"incident_id": res.subject.id})
    return {
        "subject": res.subject.model_dump(),
        "outcome": res.close_outcome.value if res.close_outcome else "open",
        "phases": [p.value for p in res.phases_run],
        "graph": {
            "nodes": [{"id": n.id, "type": n.type.value, "props": n.props,
                       "origin": n.id == origin_id, **_node_provenance(n.id, g)}
                      for n in g.nodes.values()],
            "edges": [{"id": e.id, "type": e.type.value, "src": e.src, "dst": e.dst,
                       "origin": e.origin.value,
                       "confidence": e.confidence.value if e.confidence else None,
                       "state": e.state.value,
                       "source": e.source.value if e.source else None,
                       "established": e.valid_from.isoformat() if e.valid_from else None,
                       "valid_to": e.valid_to.isoformat() if e.valid_to else None,
                       "invalidated_by": e.invalidated_by}
                      for e in g.edges.values()],
            "facts": [{"id": f.id, "subject": f.subject_ref, "predicate": f.predicate,
                       "value": f.value, "unit": f.unit, "where": f.where,
                       "at": f.valid_from.isoformat(),
                       "observed_at": f.observed_at.isoformat(),
                       "valid_to": f.valid_to.isoformat() if f.valid_to else None,
                       "source": f.source.value, "source_native_name": f.source_native_name,
                       "state": f.state.value}
                      for f in g.facts.values()],
            "events": [{"id": e.id, "entity": e.entity_ref, "type": e.type,
                        "at": e.occurred_at.isoformat(), "payload": e.payload,
                        "source": e.source.value, "source_native_name": e.source_native_name,
                        "state": e.state.value,
                        "invalidated_by": e.invalidated_by}
                       for e in g.events.values()],
        },
        "hypotheses": [{"id": h.id, "statement": h.statement, "status": h.status.value,
                    "confidence": h.confidence.value, "basis": h.confidence.basis,
                    "root_candidate": h.root_candidate, "supporting": h.supporting_facts,
                    "refuting": h.refuting_facts,
                    "chain": [c.model_dump(mode="json") for c in h.causal_chain]}
                   for h in store.ranked()],
        # phase entries (the folded PhaseResult narrative) interleaved by seq with the
        # human-in-the-loop `step` decisions (who approved/denied a gated write, and when) —
        # so the journal shows the human's role, not just the phase the approval unblocked.
        # A batch run produces no step entries, so its journal is unchanged.
        "journal": [_journal_entry(e) for e in sorted(jr.entries, key=lambda e: e.seq)
                    if (e.kind == "phase" and e.delta is not None) or e.kind == "step"],
        # every reducer rejection, derived from the JOURNALED deltas (P3 step 2 — R-K2's
        # bounded repair loop): what was dropped, in which phase, and WHY. Never memory-only,
        # so a reopened/replayed investigation shows the same list.
        "rejections": [
            {"seq": e.seq, "phase": e.phase_id.value if e.phase_id else None,
             "op_index": r.op_index, "op_kind": r.op_kind, "reason": r.reason}
            for e in jr.phase_entries() for r in e.delta.rejections],
        "postmortem": render_postmortem(res.subject, g, store, jr, res.close_outcome),
    }
