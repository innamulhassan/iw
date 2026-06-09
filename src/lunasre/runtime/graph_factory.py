"""LangGraph compile helpers — reusable across agent types.

- `compile_one_node_graph` — the Chunk-2 shape (single investigate node). Kept for
  simple single-purpose agents.
- `compile_supervisor_graph` — the Phase-2 IC shape: investigate -> [conditional
  route] -> delegate -> summarize. This is the real supervisor pattern — the graph
  EDGES are deterministic, the NODES are LLM calls. Phase 3 adds a HITL interrupt
  node; Phase 4 adds a Postgres checkpointer at compile time.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langgraph.graph import END, START, StateGraph


def compile_supervisor_graph(
    state_schema: type,
    investigate_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    route_fn: Callable[[dict[str, Any]], str],
    delegate_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    summarize_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    rca_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    execute_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    interrupt_before: list[str] | None = None,
    checkpointer: Any | None = None,
):
    """Compile the supervisor graph.

    Phase 2 start (no rca_fn):
        START -> investigate -> route ? -> delegate -> summarize -> END
                                       \\-> summarize -> END

    Phase 2 continued (rca_fn): RCA aggregates after the specialist:
        START -> investigate -> route ? -> delegate -> rca -> summarize -> END
                                       \\-> summarize -> END

    Phase 3 (execute_fn + interrupt_before=["execute_remediation"]): a human-in-the-
    loop gate before the side-effecting step. With a checkpointer, the graph PAUSES
    at the interrupt (state persisted per thread_id); a human approves; the run
    resumes from the checkpoint into execute_remediation:
        ... -> summarize -> [INTERRUPT] -> execute_remediation -> END

    `route_fn(state)` returns "delegate" or "summarize" — the supervisor's
    deterministic decision. `checkpointer` makes the graph durable (required for
    interrupts). `interrupt_before` is the list of node names to pause before.
    """
    builder = StateGraph(state_schema)
    builder.add_node("investigate", investigate_fn)
    builder.add_node("delegate", delegate_fn)
    builder.add_node("summarize", summarize_fn)
    builder.add_edge(START, "investigate")
    builder.add_conditional_edges(
        "investigate",
        route_fn,
        {"delegate": "delegate", "summarize": "summarize"},
    )
    if rca_fn is not None:
        builder.add_node("rca", rca_fn)
        builder.add_edge("delegate", "rca")
        builder.add_edge("rca", "summarize")
    else:
        builder.add_edge("delegate", "summarize")

    if execute_fn is not None:
        builder.add_node("execute_remediation", execute_fn)
        builder.add_edge("summarize", "execute_remediation")
        builder.add_edge("execute_remediation", END)
    else:
        builder.add_edge("summarize", END)

    return builder.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupt_before or [],
    )


def compile_one_node_graph(
    state_schema: type,
    node_name: str,
    node_fn: Callable[[dict[str, Any]], Awaitable[dict[str, Any]] | dict[str, Any]],
):
    """Compile a one-node LangGraph: START → node → END.

    Used by IC in Chunk 2 (the `investigate` node). Phase 2 specialists will use
    a richer compile helper (router + conditional edges by alert type).
    """
    builder = StateGraph(state_schema)
    builder.add_node(node_name, node_fn)
    builder.add_edge(START, node_name)
    builder.add_edge(node_name, END)
    return builder.compile()
