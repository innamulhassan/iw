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
    Phase,
    Source,
    VerdictStatus,
)
from iw_engine.domain.operations import (
    AddEdge,
    AddEvent,
    AddFact,
    AddNode,
    NoEvidence,
    ProposeHypothesis,
    UpdateHypothesis,
)
from iw_engine.domain.phase_result import PhaseVerdict
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
         valid_to: datetime | None = None) -> AddFact:
    lvl = ConfidenceLevel(level) if level else None
    return AddFact(subject=subject, predicate=predicate, value=value, unit=unit, valid_from=at,
                   valid_to=valid_to, observed_at=at, source=source,
                   source_reliability=None if lvl else reliability, confidence_level=lvl)


def fid(subject: str, predicate: str, at: datetime) -> str:
    return registry.fact_id(subject, predicate, at)


def event(entity: str, etype: str, at: datetime, *, source: Source = Source.OCP, **payload) -> AddEvent:
    return AddEvent(entity=entity, type=etype, occurred_at=at, observed_at=at,
                    payload=payload, source=source)


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
    return PlanOutput(phase=Phase(p), calls=calls or [], ops=ops or [], narrative=narrative,
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
