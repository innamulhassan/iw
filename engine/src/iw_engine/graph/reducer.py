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
from ..domain.common import Confidence
from ..domain.edge import Edge
from ..domain.enums import (
    ConfidenceLevel,
    EdgeType,
    HypothesisStatus,
    NodeType,
    Origin,
    Source,
    Species,
)
from ..domain.event import Event
from ..domain.fact import Fact
from ..domain.hypothesis import HypAction, HypDelta, Hypothesis, Prediction
from ..domain.node import Node
from ..domain.operations import (
    AddAssertion,
    AddEdge,
    AddEvent,
    AddFact,
    AddNode,
    Merge,
    NoEvidence,
    Operation,
    ProposeHypothesis,
    Retract,
    UpdateHypothesis,
)
from ..domain.phase_result import Rejection, Remap, Retraction
from ..domain.playbook import Tunables
from ..domain.shim import assertion_from_event, assertion_from_fact
from . import graph as graph_mod
from . import resolver

__all__ = ["Materialized", "Rejection", "materialize"]   # Rejection re-exported (home: domain)


@dataclass
class Materialized:
    nodes: list[Node] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
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
        # applies_to on nodes; edge subjects carry no NodeType (nt is None) so edge-borne
        # assertions bypass the type check (edge-predicate legality is §C2 / a later phase);
        # quarantined names carry no applies_to until a human promotes them.
        if not provisional and nt is not None and not dictionary.applies_to_ok(canonical, nt):
            out.rejections.append(Rejection(op_index=i, op_kind=op_kind, reason=
                                            f"{name_word} '{canonical}' not allowed on {nt.value}"))
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
            # become identity surface the graph indexes, not inert cargo.
            out.nodes.append(Node(id=target, type=op.type, props=props, aliases=derived,
                                  provisional=provisional, created_by=seq))
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

        # AddFact/AddEvent are compat shims mapped onto the AddAssertion atom; AddAssertion is
        # materialized natively. All three flow through emit_assertion → identical graph output.
        if isinstance(op, AddAssertion):
            emit_assertion(op, i, op.op.value)

        elif isinstance(op, AddFact):
            emit_assertion(assertion_from_fact(op), i, op.op.value)

        elif isinstance(op, AddEvent):
            emit_assertion(assertion_from_event(op), i, op.op.value)

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
            eid = registry.edge_id(op.type, src, dst, origin)
            out.edges.append(Edge(id=eid, type=op.type, src=src, dst=dst, origin=origin,
                                  props=op.props, confidence=conf, evidence=op.evidence,
                                  provisional=airlocked, created_by=seq))
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

    return out
