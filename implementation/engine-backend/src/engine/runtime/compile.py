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
from engine.domain import Access, DeclaredCapability, PhaseEffect, PhaseState, Playbook
from engine.domain.outputs import OUTPUT_TYPES
from engine.graph_runtime import FoldRegistry, IncidentGraph

from .phase import WaitingInput, run_phase
from .planner import Planner
from .state import RunState, route_phase


def _make_node(phase, playbook, planner, layer, graph, fold_registry, source_of):
    # a gate_writes phase, once past `interrupt_before` (the operator approved by resuming), runs
    # its write approved
    approved = phase.gate_writes

    def node(state: RunState) -> dict:
        local = dict(state)
        local["graph"] = graph                       # inject the engine-owned graph (not persisted)
        try:
            rec = run_phase(local, phase, playbook, planner, layer, fold_registry,
                            source_of=source_of, approved=approved,
                            decision=state.get("pending_decision"))
        except WaitingInput as wi:                   # SHOU-10 — surface, never crash the LangGraph node
            return {"phase_records": [wi.record.model_dump(mode="json")],
                    "current_phase": phase.id, "status": "waiting_input"}
        # a hard (non-run-remaining) failure persists its record + halts; a run-remaining partial is
        # `blocked` and the run continues (SHOU-11). `failed` is the only status that halts here.
        status = "failed" if rec.state is PhaseState.failed else "running"
        update: dict = {"phase_records": [rec.model_dump(mode="json")], "current_phase": phase.id,
                        "status": status}
        if phase.gate_writes and state.get("pending_decision"):
            update["pending_decision"] = None        # consume the decision once its Step is recorded (MUST-7)
        return update

    return node


def compile_run(playbook: Playbook, planner: Planner, layer: CapabilityLayer,
                graph: Optional[IncidentGraph] = None, fold_registry: Optional[FoldRegistry] = None, *,
                source_of: Optional[Callable[[DeclaredCapability], str]] = None,
                checkpointer=None):
    graph = graph if graph is not None else IncidentGraph()
    g = StateGraph(RunState)
    phases = playbook.phases
    ids = [p.id for p in phases]

    # ── compile-time invariants — fail at LOAD, not mid-run ──────────────────────────────
    bad_gate = [p.id for p in phases if p.effect is PhaseEffect.write and not p.gate_writes]
    if bad_gate:
        raise ValueError(
            f"write phase(s) {bad_gate} must set gate_writes=true — an ungated write would select a "
            f"world-changing capability the gate never pauses for, and govern() would raise "
            f"NeedsApproval mid-node (uncaught) instead of pausing for the operator")
    bad_output = [p.id for p in phases if p.output not in OUTPUT_TYPES]
    if bad_output:
        raise ValueError(
            f"phase(s) {bad_output} declare an unknown output type — must be one of "
            f"{sorted(OUTPUT_TYPES)} (a typo would otherwise fail mid-run with a bare KeyError)")

    # reconcile the playbook's unknown-effect policy into the layer (the single choke point): a
    # `deny` playbook must actually refuse an unknown-effect capability, not fall back to the layer's
    # constructor default of `ask`. Access(...) coercion is mandatory — invoke() compares by identity.
    layer.unknown_access = Access(playbook.unknown_access)

    for ph in phases:
        g.add_node(ph.id, _make_node(ph, playbook, planner, layer, graph, fold_registry, source_of))
    g.add_edge(START, ids[0])

    # edges driven by playbook METADATA, never hardcoded phase ids (MUST-6): a phase declaring
    # `min_confidence` is a confidence LOOP; one declaring `backtrack_to` is a backtrack/terminal
    # phase; everything else advances sequentially. Every exit also routes to END on `waiting_input`.
    def _router(phase_id: str, next_dest, backtrack_to):
        def route(state: RunState) -> str:
            return route_phase(state, playbook, phase_id, next_dest, backtrack_to)
        return route

    for i, ph in enumerate(phases):
        next_dest = ids[i + 1] if i + 1 < len(ids) else LG_END
        router = _router(ph.id, next_dest, ph.backtrack_to)
        if ph.backtrack_to is not None:
            g.add_conditional_edges(ph.id, router,
                                    {"halt": LG_END, "close": LG_END, "backtrack": ph.backtrack_to})
        elif ph.min_confidence is not None:
            g.add_conditional_edges(ph.id, router,
                                    {"halt": LG_END, "loop": ph.id, "advance": next_dest})
        else:
            g.add_conditional_edges(ph.id, router, {"halt": LG_END, "advance": next_dest})

    gate_nodes = [p.id for p in phases if p.gate_writes]
    return g.compile(checkpointer=checkpointer or MemorySaver(),
                     interrupt_before=gate_nodes or None)
