"""Compile the playbook → a LangGraph run. B1 (compile) + B5 (gate) + B6 (conditional edges).

The playbook is data; the compiled StateGraph is what runs. One node per phase (each runs the B3
agent loop); sequential edges in declared order; conditional edges at root-cause (loop while
confidence is low) and verify-close (backtrack if not recovered, else END); `interrupt_before` on
every `gate_writes` phase so a write pauses for the operator; a checkpointer makes it resumable.

The engine-owned investigation graph is kept OUT of the checkpointed state (it is the shared
working world, materialised separately) — the persisted state holds only the serialisable record
trail, so the run is durable + resumable with any checkpointer.
"""
from __future__ import annotations

from typing import Callable, Optional

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END as LG_END
from langgraph.graph import START, StateGraph

from engine.capability import CapabilityLayer
from engine.domain import DeclaredCapability, Playbook
from engine.graph_runtime import FoldRegistry, IncidentGraph

from .phase import run_phase
from .planner import Planner
from .state import END as STATE_END
from .state import RunState, route_after_root_cause, route_after_verify


def _make_node(phase, playbook, planner, layer, graph, fold_registry, source_of):
    # a gate_writes phase, once past `interrupt_before` (the operator approved by resuming), runs
    # its write approved
    approved = phase.gate_writes

    def node(state: RunState) -> dict:
        local = dict(state)
        local["graph"] = graph                       # inject the engine-owned graph (not persisted)
        rec = run_phase(local, phase, playbook, planner, layer, fold_registry,
                        source_of=source_of, approved=approved)
        return {"phase_records": [rec.model_dump(mode="json")], "current_phase": phase.id,
                "status": "running"}

    return node


def compile_run(playbook: Playbook, planner: Planner, layer: CapabilityLayer,
                graph: Optional[IncidentGraph] = None, fold_registry: Optional[FoldRegistry] = None, *,
                source_of: Optional[Callable[[DeclaredCapability], str]] = None,
                checkpointer=None):
    graph = graph if graph is not None else IncidentGraph()
    g = StateGraph(RunState)
    phases = playbook.phases
    ids = [p.id for p in phases]

    for ph in phases:
        g.add_node(ph.id, _make_node(ph, playbook, planner, layer, graph, fold_registry, source_of))
    g.add_edge(START, ids[0])

    for i, ph in enumerate(phases):
        if ph.id == "root-cause":
            g.add_conditional_edges(
                ph.id, lambda s: route_after_root_cause(s, playbook),
                {"root-cause": "root-cause", "remediation": "remediation"},
            )
        elif ph.id == "verify-close":
            g.add_conditional_edges(
                ph.id, route_after_verify,
                {STATE_END: LG_END, "root-cause": "root-cause"},
            )
        elif i + 1 < len(ids):
            g.add_edge(ph.id, ids[i + 1])
        else:
            g.add_edge(ph.id, LG_END)

    gate_nodes = [p.id for p in phases if p.gate_writes]
    return g.compile(checkpointer=checkpointer or MemorySaver(),
                     interrupt_before=gate_nodes or None)
