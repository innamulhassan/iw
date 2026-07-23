"""export_bundle — flatten a completed run's three projections into ONE JSON document the
React workbench renders (GraphView · JournalTimeline · HypothesisPanel · PhaseController).
Derived purely from the graph/hypothesis-store/journal, so the UI cannot show anything the
engine did not record.
"""
from __future__ import annotations

from collections import Counter

from ..domain.dictionary import is_quarantined
from ..domain.enums import Channel, NodeType
from ..graph.graph import Graph
from ..hypothesis.store import HypothesisStore
from ..journal.journal import Journal, JournalEntry
from ..runtime.engine import RunResult
from ..runtime.postmortem import render_postmortem


def _node_provenance(nid: str, g: Graph) -> dict:
    """Derive a node's provenance (obs 5) by reading REAL per-assertion provenance off the ONE
    assertion collection (P6 store-flip): WHERE it was fetched from (which capability first
    observed it) and WHEN first/last seen. Each assertion carries its own source — nothing here
    is inferred from redundant storage, and because assertions are journaled the projection is
    deterministic + replay-stable. Tie-break on id.

    The DECLARED channel (node-prop declarations, P6 step 2) is excluded: a declaration is
    asserted configuration, not an observation — it carries no observation instant, and the
    node card's source stays the observing datasource (the AppD/Dynatrace entity model).
    Per-prop declared provenance is queryable via `graph.declared_of(nid)`."""
    obs: list[tuple] = []
    for a in g.assertions.values():
        if (a.subject_ref == nid and a.channel is not Channel.DECLARED
                and a.observed_at is not None):
            obs.append((a.observed_at, a.id, a.source.value))
    if not obs:
        return {"source": None, "first_source": None, "first_seen": None, "last_seen": None}
    obs.sort(key=lambda x: (x[0], x[1]))
    return {"source": obs[-1][2], "first_source": obs[0][2],
            "first_seen": obs[0][0].isoformat(), "last_seen": obs[-1][0].isoformat()}


def discovery_counters(g: Graph) -> dict:
    """The PROMOTION SIGNAL (P3 airlock step 5 / DOMAIN-v3 §2.4): derived frequencies that tell
    a human WHICH core-registry edit to make — never made automatically (promotion stays a human
    core-registry edit per the owner's ruling; the engine only counts).

    - `class_hints`: how often each `class_hint` recurs across generic_ci nodes — repeated
      identical hints are the signal that a real NodeType is missing (P2-5).
    - `quarantined_names`: how often each airlock name (`x.<source>.<native>`) recurs across
      provisional facts/events — repeated names are the signal that a DictEntry/alias is missing.

    Pure projection of the graph (itself journal-replayable), so a reopened investigation shows
    the same counts. Keys sorted for determinism."""
    hints = Counter(str(n.props["class_hint"]) for n in g.nodes.values()
                    if n.type is NodeType.GENERIC_CI and n.props.get("class_hint"))
    names: Counter[str] = Counter()
    for f in g.facts.values():
        if f.provisional and is_quarantined(f.predicate):
            names[f.predicate] += 1
    for ev in g.events.values():
        if ev.provisional and is_quarantined(ev.type):
            names[ev.type] += 1
    return {"class_hints": dict(sorted(hints.items())),
            "quarantined_names": dict(sorted(names.items()))}


def _journal_entry(e: JournalEntry) -> dict:
    """Flatten one journal entry for the workbench timeline + the audit. The owner's CLEAN +
    COMPOSABLE rule: EVERY entry carries its `kind` AND its `ts`, and each kind serves its FULL
    per-kind fields — one coherent shape that the UI, the audit and the fold all read without
    special-casing. No kind is dropped and no detail is collapsed (the pre-goal bundle served
    only phase+step, kind-less and ts-less, and excluded invocation/gate_opened/lifecycle/plan
    entirely — the exact provenance the owner now wants served)."""
    base = {"kind": e.kind, "seq": e.seq,
            "ts": e.ts.isoformat() if e.ts else None,
            "phase": e.phase_id, "actor": e.actor}
    a = e.action or {}
    o = e.observation or {}
    if e.kind == "phase":
        d = e.delta
        return {**base, "narrative": e.reasoning,
                "goal": d.goal_restated if d else None,
                "next_actions": list(d.next_actions) if d else [],
                "verdict": d.verdict.status.value if d else None,
                "refs": e.refs}
    if e.kind == "plan":
        # the planner's PLAN + the TOOLS AVAILABLE (the access surface) — visible on the
        # scripted-direct-ops path too, where no invocations are emitted to infer it from.
        return {**base, "narrative": e.reasoning, "available": e.available or [],
                "plan_calls": e.plan_calls or [], "plan_ops": e.plan_ops or []}
    if e.kind == "invocation":
        # every tool call in full: intent/provider/why/outcome/op_count (+ effect/params/blocked).
        return {**base, "intent": e.intent, "narrative": e.reasoning,
                "provider": a.get("provider"), "params": a.get("params", {}),
                "effect": a.get("effect"), "outcome": o.get("outcome"),
                "reason": o.get("reason"), "blocked": o.get("blocked"),
                "op_count": o.get("op_count")}
    if e.kind == "gate_opened":
        # the write-GATE question: proposed action + serving hypothesis + evidence.
        return {**base, "intent": e.intent, "narrative": e.reasoning,
                "gate_id": a.get("gate_id"), "actions": a.get("actions", []),
                "hypothesis": o.get("hypothesis"), "evidence": o.get("evidence", [])}
    if e.kind == "lifecycle":
        return {**base, "event": e.reasoning, "outcome": e.decision, "detail": e.action}
    if e.kind == "phase_review":
        # the between-phases DIRECTION review: WHAT the phase did (summary) + the proposed advance
        # (to_phase) + the leading hypothesis + discovered ids. Never in a golden (batch has no
        # session), so serving it here is additive-only.
        return {**base, "narrative": e.reasoning, "review_id": a.get("review_id"),
                "to_phase": a.get("to_phase"), "verdict": a.get("verdict"),
                "hypothesis": o.get("hypothesis"), "facts": o.get("facts", []),
                "nodes": o.get("nodes", [])}
    if e.kind == "review_decision":
        # the human DIRECTION answer: approve/refine/deny + WHO + the proposed advance.
        return {**base, "source": e.source.value if e.source else None,
                "decision": e.decision, "narrative": e.reasoning,
                "review_id": a.get("review_id"), "to_phase": a.get("to_phase"),
                "action": e.action, "observation": e.observation}
    if e.kind in ("step", "gate_decision", "message"):
        # the human's role: the gate DECISION (approve/refine/deny) + WHO + the operator turn.
        return {**base, "source": e.source.value if e.source else None,
                "decision": e.decision, "intent": e.intent, "narrative": e.reasoning,
                "action": e.action, "observation": e.observation}
    # rejection / repair (record kinds) — served with their raw detail, never dropped.
    return {**base, "narrative": e.reasoning, "detail": e.action or e.observation}


def export_bundle(res: RunResult) -> dict:
    g: Graph = res.graph
    store: HypothesisStore = res.hypothesis_store
    jr: Journal = res.journal
    # the SUBJECT under investigation is the ORIGIN → renders as node #1 (obs 1). P7 step 5:
    # the engine computed it from the playbook's subject_node role binding — no incident
    # convention here (None on a legacy reopen simply flags no node as origin).
    origin_id = res.origin_node
    return {
        "subject": res.subject.model_dump(),
        "outcome": res.close_outcome or "open",
        "phases": list(res.phases_run),
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
                       "invalidated_by": e.invalidated_by,
                       **({"provisional": True} if e.provisional else {})}
                      for e in g.edges.values()],
            # `provisional` (P3 airlock) is emitted ONLY when true: airlock-admitted knowledge
            # is marked (the UI renders it dimly), while every closed-vocabulary fact/event
            # keeps its exact pre-P3 shape — the 11 goldens stay byte-identical by construction.
            "facts": [{"id": f.id, "subject": f.subject_ref, "predicate": f.predicate,
                       "value": f.value, "unit": f.unit, "where": f.where,
                       "at": f.valid_from.isoformat(),
                       "observed_at": f.observed_at.isoformat(),
                       "valid_to": f.valid_to.isoformat() if f.valid_to else None,
                       "source": f.source.value, "source_native_name": f.source_native_name,
                       "state": f.state.value,
                       **({"provisional": True} if f.provisional else {})}
                      for f in g.facts.values()],
            "events": [{"id": e.id, "entity": e.entity_ref, "type": e.type,
                        "at": e.occurred_at.isoformat(), "payload": e.payload,
                        "source": e.source.value, "source_native_name": e.source_native_name,
                        "state": e.state.value,
                        "invalidated_by": e.invalidated_by,
                        **({"provisional": True} if e.provisional else {})}
                       for e in g.events.values()],
        },
        # `confidence` is the ENGINE-EARNED weighted evidence score (P4, DOMAIN-v3 §2.5) —
        # the band the LLM reported survives as the prior inside it and as the `basis` text.
        "hypotheses": [{"id": h.id, "statement": h.statement, "status": h.status.value,
                    "confidence": store.score(h), "basis": h.confidence.basis,
                    "root_candidate": h.root_candidate, "supporting": h.supporting_facts,
                    "refuting": h.refuting_facts,
                    "chain": [c.model_dump(mode="json") for c in h.causal_chain]}
                   for h in store.ranked()],
        # the WHOLE journal, in seq order — EVERY kind (phase · plan · invocation · gate_opened ·
        # gate_decision · message · lifecycle · rejection · repair), each with its kind + ts +
        # full detail. This is the owner's COMPOSABLE record: UI, audit and the fold read the ONE
        # journal without special-casing. The sort is STABLE, so the annotations that share a
        # phase's seq (its plan + invocations, appended before the phase entry) keep their append
        # order — plan, tool calls, then the phase result. (This deliberately replaces the old
        # phases-only, byte-stable-golden projection: goldens grow additively, regenerated here.)
        "journal": [_journal_entry(e) for e in sorted(jr.entries, key=lambda e: e.seq)],
        # every reducer rejection, derived from the JOURNALED deltas (P3 step 2 — R-K2's
        # bounded repair loop): what was dropped, in which phase, and WHY. Never memory-only,
        # so a reopened/replayed investigation shows the same list.
        "rejections": [
            {"seq": e.seq, "phase": e.phase_id,
             "op_index": r.op_index, "op_kind": r.op_kind, "reason": r.reason}
            for e in jr.phase_entries() for r in e.delta.rejections],
        # the airlock's promotion counters (P3 step 5): the discovery signal telling a human
        # WHICH core-registry edit to make. Counted, surfaced, never auto-applied.
        "discovery": discovery_counters(g),
        "postmortem": render_postmortem(res.subject, g, store, jr, res.close_outcome),
    }
