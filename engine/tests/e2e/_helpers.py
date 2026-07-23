"""Terse builders for authoring deterministic scenarios (scripted planner outputs)."""
from __future__ import annotations

from datetime import UTC, datetime

from iw_engine.capability import CapabilityCall
from iw_engine.domain import registry
from iw_engine.domain.common import Confidence
from iw_engine.domain.enums import (
    ConfidenceLevel,
    EdgeType,
    NodeType,
    Origin,
    Source,
    Species,
    VerdictStatus,
)
from iw_engine.domain.operations import (
    AddAssertion,
    AddEdge,
    AddNode,
    NoEvidence,
    ProposeHypothesis,
    UpdateHypothesis,
)
from iw_engine.domain.phase_result import PhaseVerdict
from iw_engine.domain.projection import species_for_predicate
from iw_engine.runtime.planner import PlanOutput

BAND = {"low": 0.3, "med": 0.6, "high": 0.9}


def nid(t: NodeType, **props) -> str:
    return registry.node_id(t, props)


def hid(h: str) -> str:
    return f"hyp:{h}"


def node(t: NodeType, **props) -> AddNode:
    return AddNode(type=t, props=props)


def fact(subject: str, predicate: str, value, at: datetime, *, source: Source = Source.PROMETHEUS,
         reliability: float = 0.95, level: str | None = None, unit: str | None = None,
         valid_to: datetime | None = None) -> AddAssertion:
    # Scripted twins emit AddAssertion natively (P1b step 3). The species is inferred by the same
    # §9.1 classifier the P1a shim used for these predicates (no reading shape scripted here → the
    # STATE/DESCRIPTOR split it produced), so the reducer's Fact is byte-identical to the shim era.
    lvl = ConfidenceLevel(level) if level else None
    return AddAssertion(subject=subject, name=predicate, value=value, unit=unit,
                        species=species_for_predicate(predicate), valid_from=at, valid_to=valid_to,
                        observed_at=at, source=source,
                        source_reliability=None if lvl else reliability, confidence_level=lvl,
                        source_native_name=predicate)


def fid(subject: str, predicate: str, at: datetime) -> str:
    return registry.fact_id(subject, predicate, at)


def event(entity: str, etype: str, at: datetime, *, source: Source = Source.OCP,
          **payload) -> AddAssertion:
    return AddAssertion(subject=entity, name=etype, species=Species.EVENT, occurred_at=at,
                        observed_at=at, value=payload, source=source, source_native_name=etype)


def span(subject: str, name: str, started_at: datetime, *, ended_at: datetime | None = None,
         value=None, correlation_id: str | None = None, source: Source = Source.APPD,
         reliability: float = 0.95, unit: str | None = None,
         observed_at: datetime | None = None) -> AddAssertion:
    """A SPAN datum (2026-07-23 primitives §2.6): `[started_at, ended_at)` a subject PARTICIPATES
    in. `ended_at=None` = in-flight (the engine stamps span_phase=OPEN; a later call with the same
    `started_at` + an `ended_at` CLOSES it in place). `subject` may be a NodeId OR an EdgeId (the
    Rung-1 hop addresses the discovered CALLS edge). The engine derives span_phase — never authored."""
    return AddAssertion(subject=subject, name=name, value=value, unit=unit, species=Species.SPAN,
                        valid_from=started_at, valid_to=ended_at,
                        observed_at=observed_at or ended_at or started_at,
                        correlation_id=correlation_id, source=source,
                        source_reliability=reliability, source_native_name=name)


def edge(t: EdgeType, src: str, dst: str, *, origin: str | None = None,
         level: str | None = None) -> AddEdge:
    return AddEdge(type=t, src=src, dst=dst, origin=Origin(origin) if origin else None,
                   confidence_level=ConfidenceLevel(level) if level else None)


def propose(h: str, statement: str, level: str, *, root: str | None = None,
            supporting: list[str] | None = None, refuting: list[str] | None = None,
            predictions: list[str] | None = None) -> ProposeHypothesis:
    return ProposeHypothesis(hid=h, statement=statement, confidence_level=ConfidenceLevel(level),
                             root_candidate=root, supporting=supporting or [],
                             refuting=refuting or [], predictions=predictions or [])


def update(h: str, *, status: str | None = None, level: str | None = None,
           add_supporting: list[str] | None = None, add_refuting: list[str] | None = None,
           basis: str = "") -> UpdateHypothesis:
    return UpdateHypothesis(hid=h, new_status=status,
                            confidence_level=ConfidenceLevel(level) if level else None,
                            add_supporting=add_supporting or [], add_refuting=add_refuting or [],
                            basis=basis)


def no_evidence(intent: str, scope: str, at: datetime, basis: str = "") -> NoEvidence:
    return NoEvidence(intent=intent, scope=scope, at=at, basis=basis)


def verdict(status: str, level: str = "high", basis: str = "phase complete") -> PhaseVerdict:
    return PhaseVerdict(status=VerdictStatus(status), confidence=Confidence(value=BAND[level], basis=basis))


def call(intent: str, **params) -> CapabilityCall:
    return CapabilityCall(intent=intent, params=params)


def phase(p: str, ops: list | None = None, narrative: str = "", *, calls: list | None = None,
          status: str = "advance", level: str = "high",
          next_actions: list[str] | None = None) -> PlanOutput:
    # P7 phase-as-data: a phase id is a playbook string; a scripted typo fails loudly at
    # run time via the ScriptedPlanner's phase-match assertion, not an enum constructor.
    return PlanOutput(phase=p, calls=calls or [], ops=ops or [], narrative=narrative,
                      verdict=verdict(status, level), next_actions=next_actions or [])


def run(subject, script, fixtures: dict | None = None):
    """Shared scenario harness — wires the capability layer + fixtures when a scenario uses
    capability calls (fixtures per intent), else runs the direct-ops path."""
    import pathlib
    from datetime import datetime

    import iw_engine
    from iw_engine.capability import CapabilityLayer, MockSource
    from iw_engine.capability.adapters import default_adapters
    from iw_engine.runtime import Engine, ScriptedPlanner, load_playbook

    pb = load_playbook(pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml")
    # the layer now owns its fetch transport (Source); the mock is the hermetic test transport
    layer = (CapabilityLayer(default_adapters(), source=MockSource(fixtures))
             if fixtures is not None else None)
    def clock():
        return datetime(2026, 7, 19, tzinfo=UTC)
    return Engine(pb, ScriptedPlanner(script), clock=clock, layer=layer).run(subject)


def assert_replay_equivalent(res) -> None:
    """The R-J1 invariant, JOURNAL-v2 strength (part2 §1): replaying the journal's phase
    deltas reproduces BOTH projections — the graph byte-for-byte AND the hypothesis store
    record-for-record (was graph-only before P6 step 3)."""
    from iw_engine.graph import rebuild

    g2, store2 = rebuild(res.journal)
    assert g2.to_dict() == res.graph.to_dict()
    assert {h: v.model_dump() for h, v in store2.hypotheses.items()} == \
           {h: v.model_dump() for h, v in res.hypothesis_store.hypotheses.items()}
