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

from ..domain import registry
from ..domain.common import Confidence
from ..domain.edge import Edge
from ..domain.enums import ConfidenceLevel, HypothesisStatus, NodeType, Source
from ..domain.event import Event
from ..domain.fact import Fact
from ..domain.hypothesis import HypAction, HypDelta, Hypothesis, Prediction
from ..domain.node import Node
from ..domain.operations import (
    AddEdge,
    AddEvent,
    AddFact,
    AddNode,
    NoEvidence,
    Operation,
    ProposeHypothesis,
    UpdateHypothesis,
)
from ..domain.playbook import Tunables
from . import graph as graph_mod


@dataclass
class Rejection:
    op_index: int
    op_kind: str
    reason: str


@dataclass
class Materialized:
    nodes: list[Node] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    hyp_deltas: list[HypDelta] = field(default_factory=list)
    rejections: list[Rejection] = field(default_factory=list)


def _level_conf(level: ConfidenceLevel, tun: Tunables, basis: str) -> Confidence:
    return Confidence(value=tun.confidence_band[level.value], basis=basis or level.value)


def materialize(ops: list[Operation], seq: int, graph: graph_mod.Graph, tunables: Tunables,
                *, anomaly_ref: str | None = None) -> Materialized:
    out = Materialized()
    batch_types: dict[str, NodeType] = {}

    def type_of(nid: str) -> NodeType | None:
        if nid in batch_types:
            return batch_types[nid]
        n = graph.node(nid)
        return n.type if n else None

    def known(nid: str) -> bool:
        return nid in batch_types or graph.node(nid) is not None

    # ── pass 1: nodes (so facts/edges in the same batch can reference them) ────
    # A Hypothesis is BOTH a ledger entry and a graph node (NodeType.HYPOTHESIS) so causal
    # edges (CAUSED_BY hyp->cause, SUPPORTS/REFUTES node->hyp) can reference it (R-G2).
    for op in ops:
        if isinstance(op, AddNode):
            nid = registry.node_id(op.type, op.props)
            out.nodes.append(Node(id=nid, type=op.type, props=op.props, created_by=seq))
            batch_types[nid] = op.type
        elif isinstance(op, ProposeHypothesis):
            hid = f"hyp:{op.hid}"
            out.nodes.append(Node(id=hid, type=NodeType.HYPOTHESIS,
                                  props={"statement": op.statement}, created_by=seq))
            batch_types[hid] = NodeType.HYPOTHESIS

    # ── pass 2: facts / events / edges / hypotheses ───────────────────────────
    for i, op in enumerate(ops):
        if isinstance(op, AddNode):
            continue

        if isinstance(op, AddFact):
            if not known(op.subject):
                out.rejections.append(Rejection(i, op.op.value, f"unknown subject {op.subject}"))
                continue
            nt = type_of(op.subject)
            if nt and not registry.predicate_allowed(nt, op.predicate):
                out.rejections.append(Rejection(i, op.op.value,
                                                f"predicate '{op.predicate}' not allowed on {nt.value}"))
                continue
            conf = (_level_conf(op.confidence_level, tunables, f"inferred {op.predicate}")
                    if op.confidence_level is not None else None)
            fid = registry.fact_id(op.subject, op.predicate, op.valid_from)
            supersedes = None
            for ef in graph.facts_of(op.subject):
                if ef.predicate == op.predicate and ef.is_open and ef.id != fid:
                    supersedes = ef.id
                    break
            try:
                out.facts.append(Fact(
                    id=fid, subject_ref=op.subject, predicate=op.predicate, value=op.value,
                    unit=op.unit, valid_from=op.valid_from, valid_to=op.valid_to,
                    observed_at=op.observed_at, source=op.source, confidence=conf,
                    source_reliability=op.source_reliability, evidence=op.evidence,
                    supersedes=supersedes, created_by=seq))
            except (ValueError, AssertionError) as exc:
                # The Fact model enforces its invariants by raising (e.g. R-C4 belief-channel:
                # an inferred/llm fact must carry confidence, a measured fact must carry
                # source_reliability). The LivePlanner pre-repairs these, but if one slips
                # through we reject the op and continue — consistent with how the reducer
                # already treats every other malformed op — instead of crashing the whole run.
                # The model invariant itself stays intact (direct Fact() still raises).
                out.rejections.append(Rejection(i, op.op.value, f"invalid fact: {exc}"))
                continue

        elif isinstance(op, AddEvent):
            if not known(op.entity):
                out.rejections.append(Rejection(i, op.op.value, f"unknown entity {op.entity}"))
                continue
            nt = type_of(op.entity)
            if nt and not registry.event_allowed(nt, op.type):
                out.rejections.append(Rejection(i, op.op.value,
                                                f"event '{op.type}' not allowed on {nt.value}"))
                continue
            eid = registry.event_id(op.entity, op.type, op.occurred_at)
            out.events.append(Event(
                id=eid, entity_ref=op.entity, type=op.type, occurred_at=op.occurred_at,
                observed_at=op.observed_at, payload=op.payload, source=op.source, created_by=seq))

        elif isinstance(op, AddEdge):
            if registry.edge_spec(op.type).derived:
                out.rejections.append(Rejection(i, op.op.value,
                    f"{op.type.value} is a derived evidence edge — attach the fact via "
                    "add_supporting/add_refuting on the hypothesis, not a direct edge"))
                continue
            if not known(op.src) or not known(op.dst):
                out.rejections.append(Rejection(i, op.op.value,
                                                f"edge endpoint not in graph ({op.src}->{op.dst})"))
                continue
            st, dt = type_of(op.src), type_of(op.dst)
            if st and dt and not registry.edge_allowed(op.type, st, dt):
                out.rejections.append(Rejection(i, op.op.value,
                                                f"illegal edge {st.value}-{op.type.value}->{dt.value}"))
                continue
            spec = registry.edge_spec(op.type)
            origin = op.origin or spec.default_origin
            conf = None
            if op.confidence_level is not None:
                conf = _level_conf(op.confidence_level, tunables, f"{op.type.value} edge")
            elif spec.requires_confidence:
                out.rejections.append(Rejection(i, op.op.value,
                                                f"edge {op.type.value} requires confidence"))
                continue
            eid = registry.edge_id(op.type, op.src, op.dst, origin)
            out.edges.append(Edge(id=eid, type=op.type, src=op.src, dst=op.dst, origin=origin,
                                  props=op.props, confidence=conf, evidence=op.evidence,
                                  created_by=seq))

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

        elif isinstance(op, NoEvidence):
            subj = op.scope if known(op.scope) else anomaly_ref
            if subj is None:
                out.rejections.append(Rejection(i, op.op.value, "no scope/anomaly to attach null-result"))
                continue
            pred = f"no_evidence:{op.intent}"     # reserved meta-predicate (bypasses catalog check)
            fid = registry.fact_id(subj, pred, op.at)
            out.facts.append(Fact(
                id=fid, subject_ref=subj, predicate=pred, value={"scope": op.scope, "basis": op.basis},
                valid_from=op.at, observed_at=op.at, source=Source.ENGINE, source_reliability=1.0,
                created_by=seq))

    return out
