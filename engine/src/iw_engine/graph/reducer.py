"""The reducer — layer-2 enforcement (DESIGN §2.1 R-G1 / §2.5 R-K2). Turns the planner's
typed ops into materialised Node/Fact/Edge/Event/HypDelta, validating what a grammar
cannot express: registry membership, edge legality as (src_type, edge_type, dst_type),
referential integrity, per-node predicate legality, mandatory confidence on causal edges,
numeric bounds. Partial-accept: an illegal op is rejected (recorded), the rest apply;
nodes are processed first so same-batch facts/edges resolve. The LLM's coarse confidence
rubric is mapped to a numeric band here (never a naked float from the model).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..domain import dictionary, registry
from ..domain.assertion import Assertion, channel_for_source
from ..domain.common import Confidence
from ..domain.edge import Edge
from ..domain.enums import (
    ConfidenceLevel,
    EdgeClass,
    EdgeType,
    HypothesisStatus,
    NodeType,
    Origin,
    Source,
    SpanPhase,
    Species,
)
from ..domain.event import Event
from ..domain.fact import Fact
from ..domain.hypothesis import HypAction, HypDelta, Hypothesis, Prediction
from ..domain.node import Node
from ..domain.operations import (
    AddAssertion,
    AddEdge,
    AddNode,
    Merge,
    NoEvidence,
    Operation,
    ProposeHypothesis,
    Retract,
    Retype,
    UpdateHypothesis,
)
from ..domain.phase_result import Rejection, Remap, Retraction
from ..domain.playbook import Tunables
from . import graph as graph_mod
from . import resolver

__all__ = ["Materialized", "Rejection", "materialize"]   # Rejection re-exported (home: domain)


@dataclass
class Materialized:
    nodes: list[Node] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    spans: list[Assertion] = field(default_factory=list)   # SPAN species — raw atoms (§2.6)
    edges: list[Edge] = field(default_factory=list)
    hyp_deltas: list[HypDelta] = field(default_factory=list)
    retractions: list[Retraction] = field(default_factory=list)
    remaps: list[Remap] = field(default_factory=list)      # identity graduations (P5 §9.2)
    rejections: list[Rejection] = field(default_factory=list)


def _level_conf(level: ConfidenceLevel, tun: Tunables, basis: str) -> Confidence:
    return Confidence(value=tun.confidence_band[level.value], basis=basis or level.value)


def materialize(ops: list[Operation], seq: int, graph: graph_mod.Graph, tunables: Tunables,
                *, anomaly_ref: str | None = None,
                no_weight_intents: frozenset[str] | set[str] = frozenset()) -> Materialized:
    """`no_weight_intents` (P3 airlock step 1 / part4-capability §4): intents whose LAST boundary
    outcome was `error` or `blocked` — the engine passes them so a NoEvidence op naming one is
    REJECTED. An errored/blocked call observed NOTHING: letting it become an honest-null 'we
    looked and it was clean' fact is exactly the fabricated-negative-evidence poison. Only a
    clean-empty read (the provider answered, nothing to fold) may become null evidence."""
    out = Materialized()
    batch_types: dict[str, NodeType] = {}
    batch_nodes: dict[str, Node] = {}    # node records minted in THIS batch (first mint wins)
    batch_edges: set[str] = set()
    batch_aliases: dict[str, str] = {}   # "scheme:id" → node id, claimed earlier in THIS batch
    # would-be id → canonical id, for AddNodes RESOLVED away in THIS batch (P5 step 3): an
    # adapter batch is `AddNode(props)` + facts/edges keyed to `node_id(props)`; when the node
    # folds into an existing entity, its paired refs must follow it, not dangle.
    batch_redirects: dict[str, str] = {}

    def alias_target(key: str) -> str | None:
        """Current binding of an alias key — batch claims first, then the graph's index; a
        binding whose node graduated earlier in this batch follows the redirect."""
        t = batch_aliases.get(key) or graph.alias_index.get(key)
        return batch_redirects.get(t, t) if t is not None else None

    def node_record(nid: str) -> Node | None:
        """The graph's (pre-batch, authoritative) record, else this batch's mint."""
        return graph.node(nid) or batch_nodes.get(nid)

    def auto_merge(i: int, op_kind: str, prov_ids: list[str], target: str,
                   derived: dict[str, str]) -> None:
        """LATE ALIAS BINDING (P5 step 5 — DOMAIN-v3 §9.2): the canonical identity for one or
        more provisional twins just arrived — fold each in via a journaled merge record; the
        fold's remap re-homes their facts/events/edges deterministically."""
        for p in sorted(prov_ids):
            if p == target or p in batch_redirects:
                continue
            linked = sorted(k for k in (resolver.alias_key(s, v) for s, v in derived.items())
                            if alias_target(k) == p)
            out.remaps.append(Remap(kind="merge", old_id=p, new_id=target, reason=
                              f"late alias binding via {', '.join(linked) or 'explicit merge'}"))
            batch_redirects[p] = target
            for k, v in batch_aliases.items():
                if v == p:
                    batch_aliases[k] = target

    def resolve_ref(ref: str) -> str:
        """P5 entity resolution for op references (assertion subject, edge endpoint, scope):
        a ref may name the entity by a tool credential — the `"scheme:id"` alias spelling
        (DOMAIN-v3 §2.1: "an observation arriving keyed only appd:app_id=… resolves to the
        existing entity") — or by a twin id resolved away earlier in this batch. A GRADUATED id
        (merged/retyped/resolved away in an earlier phase) resolves through the graph's
        id_remaps table — §9.2's "the old id becomes an alias". Unresolvable refs return
        unchanged and reject downstream as unknown, exactly as today."""
        if known(ref):
            return ref
        for table in (batch_redirects, graph.id_remaps, batch_aliases, graph.alias_index):
            hit = table.get(ref)
            if hit is not None:
                return hit
        return ref

    def register_aliases(i: int, op_kind: str, aliases: dict[str, str], nid: str) -> None:
        """Claim `aliases` for node `nid` (first binding wins). A claim already bound to a
        DIFFERENT node is a journaled CONTRADICTION (DOMAIN-v3 §9.2: aliases append freely;
        conflict = journaled contradiction surfaced to the planner, not silent overwrite) —
        recorded on the rejections channel as a notice; the op itself still materializes."""
        for scheme, val in aliases.items():
            key = resolver.alias_key(scheme, val)
            bound = alias_target(key)
            if bound is None:
                batch_aliases[key] = nid
            elif bound != nid:
                out.rejections.append(Rejection(op_index=i, op_kind=op_kind, reason=
                    f"alias contradiction: {key} already identifies {bound}; not rebound to "
                    f"{nid} (first binding wins) — node materialized, alias claim recorded"))

    def type_of(nid: str) -> NodeType | None:
        if nid in batch_types:
            return batch_types[nid]
        n = graph.node(nid)
        return n.type if n else None

    def edge_type_of(eid: str) -> EdgeType | None:
        """The EdgeType of a known edge subject — the graph's edge first, else this batch's mint —
        so an edge-borne assertion can be governed by that edge type's `fact_predicates` allow-list
        (M30 / §C2), parallel to `applies_to` on a node subject."""
        e = graph.edges.get(eid)
        if e is None:
            e = next((be for be in out.edges if be.id == eid), None)
        return e.type if e is not None else None

    def known(nid: str) -> bool:
        # subjects may be nodes OR edges — the reducer's known() learns edge subjects so
        # edge-borne assertions (a discovered CALLS carrying RED) are finally reachable
        # (DOMAIN-v3 §2.6 / F11). An edge is known if it exists in the graph or was added
        # earlier in this same batch.
        return (nid in batch_types or graph.node(nid) is not None
                or nid in batch_edges or nid in graph.edges)

    def emit_assertion(op: AddAssertion, i: int, op_kind: str) -> None:
        """Materialize one AddAssertion into the graph store, canonicalizing its name via the
        dictionary (P2 §2.3): the emitted native name is resolved to its canonical spelling (7->1
        merges by name, 1->N splits by unit) and the vendor's own name is preserved on
        `source_native_name`. The dictionary's `applies_to` REPLACES the per-type
        `fact_predicates`/`event_allowed` membership check — the single name authority.

        NAME QUARANTINE (P3 airlock, DOMAIN-v3 §2.4 row 1): an unknown name (not a canonical,
        alias, or split input) is NOT rejected-and-erased — it lands under the quarantine
        spelling `x.<source>.<native>`, flagged `provisional`, species as the op inferred it,
        journaled with the delta and counted toward promotion. `applies_to` is skipped for a
        quarantined name (an unknown name has no constraints YET — promotion, a human
        core-registry edit, is what assigns them). Referential integrity is NOT relaxed: an
        unknown subject still rejects.

        Fact/Event IDENTITY stays keyed on the NATIVE name, so relabelling never moves an id:
        provenance ordering, hypothesis store supporting/refuting fact-id refs, and supersession chains are
        byte-stable — the only materialized change is the `predicate`/`type` label + the recorded
        `source_native_name`. `op_kind` is the original op's kind so a rejection still reads
        `add_fact`/`add_event`."""
        is_event = op.species is Species.EVENT
        subj_word, name_word = ("entity", "event") if is_event else ("subject", "predicate")
        subject = resolve_ref(op.subject)   # P5: alias-keyed subjects land on the canonical
        if not known(subject):
            out.rejections.append(Rejection(op_index=i, op_kind=op_kind, reason=f"unknown {subj_word} {op.subject}"))
            return
        nt = type_of(subject)
        native = op.source_native_name or op.name
        canonical = dictionary.resolve(op.source, op.name, op.unit)
        provisional = canonical is None
        if provisional:
            canonical = dictionary.quarantine_name(op.source, native)   # never erased, never silent
        # applies_to governs NODE-borne assertions; an edge-borne assertion (a discovered CALLS
        # carrying RED) is governed IN PARALLEL by that edge type's own `fact_predicates` allow-list
        # (§C2 — M30 closes the ungoverned lane the reducer used to leave open: a KNOWN predicate
        # illegal on the edge now REJECTS, exactly like an illegal predicate on a node). A quarantined
        # (unknown) name carries no allow-list on EITHER surface until a human core-registry promotion
        # assigns it one, so it lands provisional here regardless (checked below).
        if not provisional:
            if nt is not None:
                if not dictionary.applies_to_ok(canonical, nt):
                    out.rejections.append(Rejection(op_index=i, op_kind=op_kind, reason=
                                                    f"{name_word} '{canonical}' not allowed on {nt.value}"))
                    return
            else:
                et = edge_type_of(subject)   # known() passed + nt is None ⇒ subject is an edge
                if et is not None and canonical not in registry.edge_spec(et).fact_predicates:
                    out.rejections.append(Rejection(op_index=i, op_kind=op_kind, reason=
                        f"{name_word} '{canonical}' not allowed on edge {et.value}"))
                    return
        if not provisional:
            # SHAPE QUARANTINE (P3 step 6 / DOMAIN-v3 §9.1 — the airlock's second lane): a KNOWN
            # name with an invalid shape (unit mismatch, reading without stat+window) lands
            # PROVISIONAL **plus** a journaled rejection notice — never silently accepted (the
            # mismatch is on record, feeding the planner) and never erased (the observation
            # itself survives, dimmed).
            shape_why = dictionary.shape_violation(
                canonical, unit=op.unit, stat=op.stat, species=op.species,
                has_window=op.window is not None)
            if shape_why is not None:
                provisional = True
                out.rejections.append(Rejection(
                    op_index=i, op_kind=op_kind,
                    reason=f"shape quarantine '{canonical}': {shape_why} — landed provisional"))
        if is_event:
            eid = registry.event_id(subject, canonical, op.occurred_at)
            payload = op.value if isinstance(op.value, dict) else {}
            out.events.append(Event(
                id=eid, entity_ref=subject, type=canonical, occurred_at=op.occurred_at,
                observed_at=op.observed_at, payload=payload, source=op.source,
                source_native_name=native, provisional=provisional, created_by=seq))
            return

        if op.species is Species.SPAN:
            # SPAN fold (2026-07-23 primitives §2.6/§4/§8.1). The ENGINE derives span_phase — never
            # the LLM: OPEN while in-flight (no `ended_at`), CLOSED once `valid_to` (ended_at)
            # arrives. ABANDONED is the journaled TTL reaper's deterministic decision (§4.6), never
            # a wall-clock read here. The OPEN datum and the later CLOSED datum share ONE
            # `started_at` -> ONE span_id, so the close overwrites the open IN PLACE
            # (two-phase-then-frozen) — no supersession chain, unlike a STATE tile. `subject` is
            # already resolved above and may be a NODE or an EDGE (Rung-1 hops address the edge);
            # `correlation_id` (trace_id/BT-id) joins sibling hops to a Rung-2 occurrence (§4.4).
            phase = SpanPhase.CLOSED if op.valid_to is not None else SpanPhase.OPEN
            channel = op.channel or channel_for_source(op.source)
            conf = (_level_conf(op.confidence_level, tunables, f"inferred {canonical}")
                    if op.confidence_level is not None else None)
            reliability = op.source_reliability
            if reliability is None and op.source != Source.LLM:
                reliability = tunables.source_reliability.get(op.source.value)
            sid = registry.span_id(subject, native, op.valid_from)
            try:
                out.spans.append(Assertion(
                    id=sid, subject_ref=subject, name=canonical, value=op.value, unit=op.unit,
                    species=Species.SPAN, channel=channel, valid_from=op.valid_from,
                    valid_to=op.valid_to, observed_at=op.observed_at, span_phase=phase,
                    correlation_id=op.correlation_id, source=op.source, source_native_name=native,
                    confidence=conf, source_reliability=reliability, evidence=op.evidence,
                    provisional=provisional, created_by=seq))
            except (ValueError, AssertionError) as exc:
                # mirror the Fact path's defensive reject-and-continue (the atom enforces its
                # span/belief invariants by raising; a slipped-through malformed span rejects,
                # never crashes the run).
                out.rejections.append(Rejection(op_index=i, op_kind=op_kind,
                                                reason=f"invalid span: {exc}"))
            return

        conf = (_level_conf(op.confidence_level, tunables, f"inferred {canonical}")
                if op.confidence_level is not None else None)
        # INV-9: a MEASURED assertion whose payload stated no reliability gets the per-source
        # default from the playbook tunables (adapters carry no hardcoded constants). An
        # LLM-sourced assertion is inferred (confidence channel) and never takes a reliability.
        reliability = op.source_reliability
        if reliability is None and op.source != Source.LLM:
            reliability = tunables.source_reliability.get(op.source.value)
        fid = registry.fact_id(subject, native, op.valid_from)
        supersedes = None
        for ef in graph.facts_of(subject):
            if ef.predicate == canonical and ef.is_open and ef.id != fid:
                supersedes = ef.id
                break
        try:
            out.facts.append(Fact(
                id=fid, subject_ref=subject, predicate=canonical, value=op.value,
                unit=op.unit, valid_from=op.valid_from, valid_to=op.valid_to,
                observed_at=op.observed_at, source=op.source, source_native_name=native,
                confidence=conf, source_reliability=reliability, evidence=op.evidence,
                supersedes=supersedes, provisional=provisional, created_by=seq))
        except (ValueError, AssertionError) as exc:
            # The Fact model enforces its invariants by raising (R-C4 belief-channel). The
            # LivePlanner pre-repairs these, but if one slips through we reject the op and
            # continue — consistent with every other malformed op — instead of crashing the run.
            out.rejections.append(Rejection(op_index=i, op_kind=op_kind, reason=f"invalid fact: {exc}"))

    # ── pass 1: nodes (so facts/edges in the same batch can reference them) ────
    # A Hypothesis is BOTH a hypothesis store entry and a graph node (NodeType.HYPOTHESIS) so causal
    # edges (CAUSED_BY hyp->cause, SUPPORTS/REFUTES node->hyp) can reference it (R-G2).
    for i, op in enumerate(ops):
        if isinstance(op, AddNode):
            # P5 identity hardening + ENTITY RESOLUTION (DOMAIN-v3 §2.1; audit 4 S1.4). Order:
            #   A. the computed id already exists (graph or batch)     → plain upsert;
            #   B. it does not, but EXACTLY ONE same-type entity is bound to one of this
            #      arrival's tool credentials                          → RESOLVE onto it (the
            #      split-brain kill: a shared sys_id/app_id/repo/workload links two display
            #      names of one entity — fold in, never mint a twin). Identity-key props of
            #      the arrival are DROPPED from the merge (identity is write-once on the
            #      canonical; the alias, not the key, is what unified them);
            #   C. identity keys missing and no credential resolves    → REJECTION, never a
            #      degenerate `type:` id (0 hits) / never a guess (≥2 hits: ambiguous).
            # Cross-type alias hits never unify (a claim across types is a contradiction,
            # recorded by register_aliases). Deterministic throughout: replay folds the same
            # DELTA, so resolution shapes what enters the journal, never how it replays.
            missing = registry.missing_identity_keys(op.type, op.props)
            derived = resolver.aliases_from_props(op.type, op.props)
            cid = None if missing else registry.node_id(op.type, op.props)
            hits: list[str] = []
            for scheme, val in derived.items():
                t = alias_target(resolver.alias_key(scheme, val))
                if t is not None and t not in hits and type_of(t) is op.type:
                    hits.append(t)
            prov_hits = [t for t in hits
                         if (n := node_record(t)) is not None and n.provisional]
            canon_hits = [t for t in hits if t not in prov_hits]
            provisional = False
            if cid is not None and (cid in batch_types or graph.node(cid) is not None):
                target, props = cid, op.props                       # A: existing canonical
                auto_merge(i, op.op.value, prov_hits, cid, derived)
            elif len(canon_hits) == 1 and canon_hits[0] != cid:
                target = canon_hits[0]                              # B: resolve, don't twin
                keys = registry.node_spec(op.type).identity_keys
                props = {k: v for k, v in op.props.items() if k not in keys}
                if cid is not None and cid not in graph.id_remaps:
                    # paired same-batch refs follow the fold NOW; the journaled resolve record
                    # makes the redirect permanent (P5 step 4) — a later phase citing the
                    # would-be twin id still lands on the canonical after any replay.
                    batch_redirects[cid] = target
                    matched = sorted(k for k in
                                     (resolver.alias_key(s, v) for s, v in derived.items())
                                     if alias_target(k) == target)
                    out.remaps.append(Remap(kind="resolve", old_id=cid, new_id=target,
                                            reason=f"alias resolution via {', '.join(matched)}"))
                auto_merge(i, op.op.value, prov_hits, target, derived)
            elif cid is not None:
                target, props = cid, op.props                       # fresh mint (0/ambiguous)
                if not canon_hits:
                    # §9.2 LATE ALIAS BINDING: the canonical identity for provisional twin(s)
                    # bound to this arrival's credentials just landed — fold them in.
                    auto_merge(i, op.op.value, prov_hits, cid, derived)
            elif not canon_hits and len(prov_hits) == 1:
                target, props = prov_hits[0], op.props              # accumulate on the twin
                provisional = True
            elif not hits and derived:
                # §9.2: canonical identity NOT yet known, but the observation carries a tool
                # credential — mint a PROVISIONAL entity (quarantine-flagged) instead of
                # rejecting; a later Merge (auto or explicit) graduates it.
                target = resolver.provisional_node_id(op.type, derived)
                props, provisional = op.props, True
            else:
                why = (f"ambiguous alias resolution ({', '.join(sorted(hits))}) — " if hits
                       else "")
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value, reason=
                    f"{why}missing identity key(s) {', '.join(missing)} for {op.type.value} — "
                    "refusing a degenerate id"))
                continue
            # lift the identity-backbone props into the entity's alias block — per-tool ids
            # become identity surface the graph indexes, not inert cargo. `source` rides the
            # record (P6 step 2 / P1a decision 3): the fold turns each prop into a DECLARED
            # assertion attributed to the adapter that supplied it.
            out.nodes.append(Node(id=target, type=op.type, props=props, aliases=derived,
                                  provisional=provisional, source=op.source, created_by=seq))
            batch_types[target] = op.type
            batch_nodes.setdefault(target, out.nodes[-1])
            register_aliases(i, op.op.value, derived, target)
        elif isinstance(op, ProposeHypothesis):
            hid = f"hyp:{op.hid}"
            out.nodes.append(Node(id=hid, type=NodeType.HYPOTHESIS,
                                  props={"statement": op.statement}, created_by=seq))
            batch_types[hid] = NodeType.HYPOTHESIS

    # ── pass 2: facts / events / edges / hypotheses ───────────────────────────
    for i, op in enumerate(ops):
        if isinstance(op, AddNode):
            continue

        # AddAssertion is the ONE atom op (F4 retired the AddFact/AddEvent compat shims — the live
        # planner emits AddAssertion natively, adapters + scenario twins already did).
        if isinstance(op, AddAssertion):
            emit_assertion(op, i, op.op.value)

        elif isinstance(op, AddEdge):
            if registry.edge_spec(op.type).derived:
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value, reason=
                    f"{op.type.value} is a derived evidence edge — attach the fact via "
                    "add_supporting/add_refuting on the hypothesis, not a direct edge"))
                continue
            src, dst = resolve_ref(op.src), resolve_ref(op.dst)   # P5: alias-keyed endpoints
            if not known(src) or not known(dst):
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value, reason=
                                                f"edge endpoint not in graph ({op.src}->{op.dst})"))
                continue
            st, dt = type_of(src), type_of(dst)
            if st and dt and not registry.edge_allowed(op.type, st, dt):
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value, reason=
                                                f"illegal edge {st.value}-{op.type.value}->{dt.value}"))
                continue
            spec = registry.edge_spec(op.type)
            # P3 TYPE AIRLOCK (DOMAIN-v3 §2.4 row 2): everything P3 newly admits is PROVISIONAL —
            # a generic_ci substituted into a structural pair (edge_airlocked; origin FORCED to
            # discovered: it is an observation about an unclassified CI, whatever the op claimed)
            # or a CAUSED_BY blaming a generic_ci (declared pair, origin stays inferred per spec).
            # Pre-P3 generic_ci bookkeeping pairs (AFFECTS/CHANGED_BY/REMEDIATED_BY) are untouched.
            declared = bool(st and dt and (st, dt) in spec.allowed)
            airlocked = (NodeType.GENERIC_CI in (st, dt)
                         and (not declared or op.type is EdgeType.CAUSED_BY))
            origin = op.origin or spec.default_origin
            if airlocked and op.type is not EdgeType.CAUSED_BY:
                origin = Origin.DISCOVERED
            conf = None
            if op.confidence_level is not None:
                conf = _level_conf(op.confidence_level, tunables, f"{op.type.value} edge")
            elif spec.requires_confidence:
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value, reason=
                                                f"edge {op.type.value} requires confidence"))
                continue
            if airlocked and conf is not None:
                # provisional knowledge is admitted, never at full weight (the airlock's penalty)
                conf = Confidence(value=round(conf.value * tunables.discovery_penalty, 4),
                                  basis=f"{conf.basis} [provisional: generic_ci endpoint]")
            # OBSERVED-edge belief, engine-earned + symmetric with the atom (2026-07-23 §5.2/§5.4):
            # an edge carries confidence XOR source_reliability. When it did NOT resolve a confidence
            # (a declared/discovered spine edge, not an inferred/causal claim), the engine fills its
            # reliability — DECLARED trusted ~1.0, DISCOVERED graded < 1 — never both (the never-both
            # rule the Edge model also validates). A DISCOVERED STRUCTURAL edge below the floor lands
            # provisional (§5.2 class 1: discovered topology is an observation, time-boxed + gradable).
            reliability = None
            provisional = airlocked
            if conf is None:
                reliability = (1.0 if origin is Origin.DECLARED
                               else tunables.discovered_edge_reliability)
                if (origin is Origin.DISCOVERED and spec.edge_class is EdgeClass.STRUCTURAL
                        and reliability < tunables.provisional_edge_floor):
                    provisional = True
            eid = registry.edge_id(op.type, src, dst, origin)
            out.edges.append(Edge(id=eid, type=op.type, src=src, dst=dst, origin=origin,
                                  props=op.props, confidence=conf, source_reliability=reliability,
                                  evidence=op.evidence, provisional=provisional, created_by=seq))
            batch_edges.add(eid)   # so a later same-batch edge-borne assertion resolves (F11)

        elif isinstance(op, ProposeHypothesis):
            conf = _level_conf(op.confidence_level, tunables, op.statement[:80] or "proposed")
            h = Hypothesis(
                id=f"hyp:{op.hid}", statement=op.statement, root_candidate=op.root_candidate,
                causal_chain=op.causal_chain, confidence=conf, supporting_facts=op.supporting,
                refuting_facts=op.refuting,
                predictions=[Prediction(statement=p) for p in op.predictions], created_by=seq)
            out.hyp_deltas.append(HypDelta(action=HypAction.CREATE, hypothesis=h))

        elif isinstance(op, UpdateHypothesis):
            action = HypAction.RERANK
            new_status = None
            if op.new_status:
                new_status = HypothesisStatus(op.new_status)
                action = {HypothesisStatus.CONFIRMED: HypAction.CONFIRM,
                          HypothesisStatus.REFUTED: HypAction.REFUTE,
                          HypothesisStatus.SUPERSEDED: HypAction.SUPERSEDE}.get(
                              new_status, HypAction.RERANK)
            elif op.add_supporting or op.add_refuting:
                action = HypAction.ATTACH_EVIDENCE
            conf = (_level_conf(op.confidence_level, tunables, op.basis or "rerank")
                    if op.confidence_level is not None else None)
            out.hyp_deltas.append(HypDelta(
                action=action, hypothesis_id=f"hyp:{op.hid}", new_status=new_status,
                confidence=conf, add_supporting=op.add_supporting, add_refuting=op.add_refuting,
                add_chain=op.add_chain, basis=op.basis))

        elif isinstance(op, Retract):
            # P3 step 6 (R-J3): a tombstone must name something that EXISTS — in the graph, or
            # materialized earlier in this same batch (the fold applies retractions after adds,
            # so the ordering is replay-deterministic either way).
            known_target = (op.target in graph.facts or op.target in graph.events
                            or op.target in graph.edges
                            or any(f.id == op.target for f in out.facts)
                            or any(e.id == op.target for e in out.events)
                            or any(e.id == op.target for e in out.edges))
            if not known_target:
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value,
                                                reason=f"unknown retract target {op.target}"))
                continue
            # PROVENANCE/lineage is IMMUTABLE (2026-07-23 §5.2 class 2): a release *was* built from a
            # commit — that never un-happens. A lineage edge is superseded-on-rebuild, NEVER
            # retracted-as-wrong, so a retract naming one is refused (the CAUSAL/EVIDENTIAL/STRUCTURAL
            # layers stay freely refutable — only the five lineage predicates are frozen).
            tgt_edge = (graph.edges.get(op.target)
                        or next((e for e in out.edges if e.id == op.target), None))
            if tgt_edge is not None and registry.is_immutable_edge(tgt_edge.type):
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value, reason=(
                    f"{tgt_edge.type.value} is an immutable provenance/lineage edge — "
                    "superseded-on-rebuild, never retracted-as-wrong")))
                continue
            out.retractions.append(Retraction(target=op.target, invalidated_by=op.invalidated_by,
                                              reason=op.reason))

        elif isinstance(op, Merge):
            # P5 step 5 (R-J5 / §9.2): the explicit graduation lane. provisional→canonical
            # ONLY — canonical entities never merge; the fold's remap re-homes every reference.
            old, new = resolve_ref(op.provisional_id), resolve_ref(op.canonical_id)
            old_n, new_n = node_record(old), node_record(new)
            if old_n is None or new_n is None:
                miss = op.provisional_id if old_n is None else op.canonical_id
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value,
                                                reason=f"unknown merge entity {miss}"))
            elif old == new:
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value, reason=
                    f"merge source and target are already the same entity ({new})"))
            elif not old_n.provisional:
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value, reason=
                    f"merge is provisional→canonical only — {old} is canonical and canonical "
                    "entities never merge (R-J5/§9.2)"))
            elif new_n.provisional:
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value, reason=
                    f"merge target {new} is itself provisional — graduate it first"))
            elif old_n.type is not new_n.type:
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value, reason=
                    f"cannot merge across types ({old_n.type.value} → {new_n.type.value})"))
            else:
                out.remaps.append(Remap(kind="merge", old_id=old, new_id=new,
                                        reason=op.reason))
                batch_redirects[old] = new
                for k, v in batch_aliases.items():
                    if v == old:
                        batch_aliases[k] = new

        elif isinstance(op, Retype):
            # P5 step 6 (DOMAIN-v3 §2.4 row 2 / §9.2 — closes audit 4 S2.4): generic_ci
            # graduates to the real type its class_hint promised. Mint the canonical entity,
            # journal a retype Remap; the fold re-homes every reference and the old id stays
            # resolvable forever (an alias graduation — write-once identity never violated).
            old = resolve_ref(op.target)
            old_n = node_record(old)
            merged = {**(old_n.props if old_n else {}), **op.props}
            missing = registry.missing_identity_keys(op.new_type, merged)
            if old_n is None:
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value,
                                                reason=f"unknown retype target {op.target}"))
                continue
            if old_n.type is not NodeType.GENERIC_CI:
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value, reason=
                    f"retype applies to the generic_ci escape hatch only — {old} is a "
                    f"canonical {old_n.type.value} and typed identity never re-keys"))
                continue
            if op.new_type in (NodeType.GENERIC_CI, NodeType.HYPOTHESIS):
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value, reason=
                    f"cannot retype to {op.new_type.value}"))
                continue
            if missing:
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value, reason=
                    f"missing identity key(s) {', '.join(missing)} for {op.new_type.value} — "
                    "a retype must supply the real type's identity"))
                continue
            new_id = registry.node_id(op.new_type, merged)
            if node_record(new_id) is not None:
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value, reason=
                    f"retype target id {new_id} already exists — canonical entities never "
                    "merge (R-J5); retype mints, it does not fold"))
                continue
            derived = resolver.aliases_from_props(op.new_type, merged)
            node = Node(id=new_id, type=op.new_type, props=merged, aliases=derived,
                        created_by=seq)
            out.nodes.append(node)
            batch_types[new_id] = op.new_type
            batch_nodes.setdefault(new_id, node)
            register_aliases(i, op.op.value, derived, new_id)
            out.remaps.append(Remap(kind="retype", old_id=old, new_id=new_id,
                                    reason=op.reason))
            batch_redirects[old] = new_id
            for k, v in batch_aliases.items():
                if v == old:
                    batch_aliases[k] = new_id

        elif isinstance(op, NoEvidence):
            if op.intent in no_weight_intents:
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value, reason=
                    f"no_evidence '{op.intent}' rejected — that capability call errored or was "
                    "blocked (an error carries no evidentiary weight; only a clean-empty read "
                    "can become null evidence)"))
                continue
            scope = resolve_ref(op.scope)   # P5: an alias-keyed scope resolves like any ref
            subj = scope if known(scope) else anomaly_ref
            if subj is None:
                out.rejections.append(Rejection(op_index=i, op_kind=op.op.value,
                                                reason="no scope/anomaly to attach null-result"))
                continue
            pred = f"no_evidence:{op.intent}"     # reserved meta-predicate (bypasses catalog check)
            fid = registry.fact_id(subj, pred, op.at)
            out.facts.append(Fact(
                id=fid, subject_ref=subj, predicate=pred, value={"scope": op.scope, "basis": op.basis},
                valid_from=op.at, observed_at=op.at, source=Source.ENGINE, source_reliability=1.0,
                created_by=seq))

    if tunables.derive_transitions:
        _derive_transition_events(out, seq, graph)
    return out


def _derive_transition_events(out: Materialized, seq: int, graph: graph_mod.Graph) -> None:
    """P6 step 5 (part2 §3): a boolean STATE flip derives its transition EVENT in the ENGINE —
    `<name>_started` on a False/absent→True flip, `<name>_cleared` on a True→False flip — so
    adapters/scenarios stop dual-authoring an occurrence twin for every boolean state they
    report. Tunable-gated (`tunables.derive_transitions`, default off — see playbook.py).

    Discipline:
      - dictionary-KNOWN transition names only (closed vocabulary: the engine never fabricates
        a quarantine spelling it invented itself);
      - dedup by EVENT ID against the graph and this batch — an authored twin (same entity,
        name, instant) wins: within a batch the derived one is skipped, across batches the
        idempotent add_event overwrite resolves to the authored record;
      - provisional (airlocked) facts never derive;
      - derived events ride the DELTA (Materialized.events → PhaseResult → journal), so replay
        reproduces them bit-for-bit with zero engine-state dependence, and the record mirrors
        the authored convention (source = the fact's source; occurred_at = the flip instant).
    Threshold flips are deliberately absent: the dictionary carries no threshold values yet."""
    batch_prior: dict[tuple[str, str], object] = {}
    batch_event_ids = {e.id for e in out.events}
    for f in out.facts:
        if not isinstance(f.value, bool) or f.provisional:
            continue
        key = (f.subject_ref, f.predicate)
        prior = batch_prior.get(key)
        if prior is None:
            if f.supersedes and f.supersedes in graph.assertions:
                prior = graph.assertions[f.supersedes].value
            else:
                open_prior = [pf for pf in graph.facts_of(f.subject_ref)
                              if pf.predicate == f.predicate and pf.is_open]
                prior = open_prior[0].value if open_prior else None
        batch_prior[key] = f.value
        if f.value is True and prior is not True:
            name = f"{f.predicate}_started"
        elif f.value is False and prior is True:
            name = f"{f.predicate}_cleared"
        else:
            continue                              # no flip (re-assert / never-started False)
        canonical = dictionary.resolve(f.source, name, None)
        entry = dictionary.DICTIONARY.get(canonical) if canonical else None
        if entry is None or entry.species is not Species.EVENT:
            continue                              # unknown transition name — never fabricated
        eid = registry.event_id(f.subject_ref, canonical, f.valid_from)
        if eid in graph.events or eid in batch_event_ids:
            continue                              # an authored twin exists — it wins
        batch_event_ids.add(eid)
        out.events.append(Event(
            id=eid, entity_ref=f.subject_ref, type=canonical, occurred_at=f.valid_from,
            observed_at=f.observed_at, payload={}, source=f.source,
            source_native_name=canonical, created_by=seq))
