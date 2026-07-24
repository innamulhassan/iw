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

# The DISCOVERED-layer map (owner: "the category is an OUTPUT of the investigation, not a
# pre-label"): the fault CLASS is EARNED from the confirmed root's node TYPE, NEVER read from the
# catalog's pre-assigned layer. Keyed on the real NodeType enum members so a rename breaks loudly;
# an unmapped type falls through to its de-cased value ("message_queue" -> "Message queue"). The
# root a fault-class investigation lands on is the ACTIONABLE cause the doctrine roots at — e.g. a
# DB index-drop roots at the CHANGE_EVENT (-> "Change/Deployment"), NOT the database it saturates,
# so a discovered layer legitimately differs from the incident's catalog label.
_NODE_TYPE_LAYER: dict[NodeType, str] = {
    NodeType.CODE_COMMIT: "Application code",
    NodeType.ERROR_SIGNATURE: "Application code",
    NodeType.CHANGE_EVENT: "Change/Deployment",
    NodeType.DATABASE: "Database",
    NodeType.NETWORK_SEGMENT: "Network",
    NodeType.FIREWALL_RULE: "Firewall / Security",
    NodeType.CACHE: "Caching",
    NodeType.FEATURE_FLAG: "Configuration / Flag",
    NodeType.CERTIFICATE: "TLS / Certificate",
    NodeType.HOST: "Infra",
}


def _layer_for_node_type(nt: NodeType) -> str:
    """The layer NAME earned by a confirmed root's node type — an explicit mapping for the classes
    the doctrine roots at, else the node type de-cased (underscores -> spaces, sentence case)."""
    return _NODE_TYPE_LAYER.get(nt, nt.value.replace("_", " ").capitalize())


def discovered_layer(res: RunResult) -> str | None:
    """The DISCOVERED fault layer — `None` UNTIL a hypothesis is CONFIRMED, then the layer NAME
    derived from that hypothesis's ROOT node TYPE (via `_NODE_TYPE_LAYER`). It is EARNED from the
    investigation's confirmed root, never the catalog's pre-assigned label, so the UI can stop
    ASSUMING the category and show the one the evidence proved. `None` also when the confirmed
    hypothesis has no root_candidate, or its root id resolves to no node in the graph."""
    confirmed = res.hypothesis_store.confirmed()
    if confirmed is None or not confirmed.root_candidate:
        return None
    root = res.graph.nodes.get(confirmed.root_candidate)
    if root is None:
        return None
    return _layer_for_node_type(root.type)


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
        # scripted-direct-ops path too, where no invocations are emitted to infer it from. `todos`
        # is the F1 CHECKLIST: each objective + its call intents + op kinds + status (the UI groups
        # the tool-call cards under their to-do); [] on a legacy journal without the field.
        return {**base, "narrative": e.reasoning, "available": e.available or [],
                "plan_calls": e.plan_calls or [], "plan_ops": e.plan_ops or [],
                "todos": e.todos or []}
    if e.kind == "invocation":
        # every tool call in full: intent/provider/why/outcome/op_count (+ effect/params/blocked),
        # plus the transport provenance (M1: served_by + binding — mock-vs-live on the record). `todo`
        # is the F1 attribution — which plan to-do this call served (None on a legacy journal). The
        # reasoned-step RESULT (`result` — the human 'what came back' line) and PRODUCED facts
        # (`produced` — a per-op summary) ride ONLY when the serving plan authored them (JOURNAL
        # story fidelity), so a call from a no-to-do plan keeps its pre-story shape (goldens stable).
        return {**base, "intent": e.intent, "narrative": e.reasoning,
                "provider": a.get("provider"), "params": a.get("params", {}),
                "effect": a.get("effect"), "outcome": o.get("outcome"),
                "reason": o.get("reason"), "blocked": o.get("blocked"),
                "op_count": o.get("op_count"), "todo": e.todo,
                "served_by": a.get("served_by"), "binding": a.get("binding"),
                **({"result": a["result"]} if "result" in a else {}),
                **({"produced": a["produced"]} if "produced" in a else {})}
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


def phase_rail(playbook) -> list[dict]:
    """The full declared phase rail as DATA for the workbench stepper (M22): every phase id in
    declared order, each flagged `focus` (always shown) vs greyed-until-reached. `focus` is DERIVED
    from the playbook's `writes_allowed` role binding — the pre-action diagnostic phases (those
    before the first write-gated phase) are the in-focus ones, reproducing the UI's former hardcoded
    {frame, investigate} ACTIVE set from playbook data, so a NEW playbook's rail needs no UI edit.

    Session/playbook CONTEXT (like state/pending_gate), served on the snapshot envelope — NOT in the
    batch `export_bundle`, so the 11 goldens stay byte-identical (the stepper renders only in the
    interactive workbench, which reads the snapshot, never the raw bundle)."""
    phases = list(playbook.phases)
    gate_idx = next((i for i, p in enumerate(phases) if p.writes_allowed), len(phases))
    return [{"id": p.id, "focus": i < gate_idx} for i, p in enumerate(phases)]


def label_dictionary() -> dict:
    """The engine's canonical vocab served to the UI as labels (M25), so the workbench stops
    RE-AUTHORING the vocabulary in client-side maps. Three sub-maps of what the engine authoritatively
    owns:
      - `predicates`: every canonical fact-predicate name -> a default humanized label
      - `relations` : every edge type -> a default humanized label
      - `intents`   : every capability intent -> its capability's purpose (CapabilityMeta.summary)
    The UI layers its own curated labels as OVERRIDES and falls back to a de-underscored raw string,
    so a NEW predicate/edge/intent is served with a sane default automatically (drift-prevention),
    while every current label is preserved exactly.

    SCOPE (reported): the engine authors the VOCAB + per-capability summaries + a default label; it
    does NOT model the UI's finer per-intent purposes or the graph LANE layout (`tiers.ts`), which
    stay UI presentation — moving those into the shared-core ontology is a separate F3-boundary
    decision, out of this pass. Global + static; served on the snapshot envelope like `phase_rail`
    (NOT `export_bundle`), so the 11 goldens stay byte-identical."""
    from ..capability.adapters import default_adapters
    from ..capability.adapters.remediation import RemediationAdapter
    from ..domain.dictionary import DICTIONARY
    from ..domain.enums import EdgeType
    from ..domain.nodes import NODE_SPECS

    def humanize(s: str) -> str:
        return s.replace("_", " ")

    adapters = [*default_adapters(), RemediationAdapter()]
    return {
        "predicates": {name: humanize(name) for name in sorted(DICTIONARY)},
        "relations": {e.value: humanize(e.value) for e in EdgeType},
        "intents": {i: a.meta.summary for a in adapters if getattr(a, "meta", None)
                    for i in sorted(a.intents)},
        # The datum-shape SPECIES per canonical predicate (2026-07-23 primitives §2) — the ONE
        # authority the node-detail view categorizes a fact by (property/state/reading/span). The
        # species rides on the Assertion, is DERIVED-not-stored on the Fact view (the reducer folds a
        # READING op into a Fact and re-derives STATE, so a per-fact species in the bundle could not
        # tell reading from state), and a fact always carries its CANONICAL predicate — so the UI maps
        # `fact.predicate -> species` HERE, authoritatively, rather than re-authoring a client-side
        # guess. A predicate absent from the dictionary (a provisional/quarantined name) has no
        # cataloged species; the UI falls back to STATE (the §9.1 "when in doubt -> state" default).
        # Served on the snapshot envelope only (like `predicates`/`phase_rail`), never in the batch
        # export_bundle, so the 12 goldens stay byte-identical.
        "species": {name: entry.species.value for name, entry in sorted(DICTIONARY.items())},
        # The IDENTITY keys per node type (NodeSpec.identity_keys, §2.1) — the props that MAKE the
        # entity THIS entity. The node card serves props as a flat dict; the UI splits IDENTITY from
        # PROPERTY by this map so identity renders as its own category, never re-authored client-side.
        "identity_keys": {nt.value: list(spec.identity_keys) for nt, spec in NODE_SPECS.items()},
    }


def export_bundle(res: RunResult) -> dict:
    g: Graph = res.graph
    store: HypothesisStore = res.hypothesis_store
    jr: Journal = res.journal
    # the SUBJECT under investigation is the ORIGIN → renders as node #1 (obs 1). P7 step 5:
    # the engine computed it from the playbook's subject_node role binding — no incident
    # convention here (None on a legacy reopen simply flags no node as origin).
    origin_id = res.origin_node
    bundle: dict = {
        "subject": res.subject.model_dump(),
        "outcome": res.close_outcome or "open",
        # the DISCOVERED fault layer (owner: category is an OUTPUT, not a pre-label): null until a
        # hypothesis is CONFIRMED, then EARNED from the confirmed root's node type. Served always
        # (unconditionally null pre-confirmation) so the UI reads one stable field, never the
        # incident's assumed catalog layer.
        "discovered_layer": discovered_layer(res),
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
                       "value": f.value, "unit": f.unit,
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
                    # DETERMINISTIC belief timestamps (stamped from the phase clock, never wall-clock):
                    # when the hypothesis was first proposed and last moved — served for the UI's
                    # "updated HH:MM" line. None on a hypothesis stamped by a clock-less path.
                    "proposed_at": h.proposed_at, "updated_at": h.updated_at,
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
    # SPAN species (2026-07-23 primitives §2.6) — bounded happenings, injected into the graph view
    # ONLY when present, so the pre-span goldens stay byte-identical (mirrors the `provisional`-
    # when-true discipline every fact/event key already follows). A span is rendered as the RAW
    # atom with its `span_phase` ALWAYS exposed (§4.6: an ABANDONED span can never read as
    # 'ongoing'); `subject` may be a NODE or an EDGE id (a Rung-1 hop addresses the discovered
    # CALLS edge), and `correlation_id` (trace_id/BT-id) joins sibling hops to a reified occurrence.
    if g.spans:
        bundle["graph"]["spans"] = [
            {"id": s.id, "subject": s.subject_ref, "name": s.name, "value": s.value,
             "unit": s.unit, "started_at": s.valid_from.isoformat(),
             "ended_at": s.valid_to.isoformat() if s.valid_to else None,
             "span_phase": s.span_phase.value if s.span_phase else None,
             "correlation_id": s.correlation_id,
             "observed_at": s.observed_at.isoformat() if s.observed_at else None,
             "source": s.source.value, "source_native_name": s.source_native_name,
             "state": s.state.value,
             **({"provisional": True} if s.provisional else {})}
            for s in g.spans.values()]
    return bundle
