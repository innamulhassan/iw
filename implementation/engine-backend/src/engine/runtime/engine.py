"""Engine — a thin wrapper over one compiled run. Owns the per-incident graph + the LangGraph app
and exposes start / resume / state. One Engine per session (one incident = one run = one graph)."""
from __future__ import annotations

from typing import Callable, Optional

from engine.capability import CapabilityLayer
from engine.domain import DeclaredCapability, Playbook
from engine.graph_runtime import FoldRegistry, IncidentGraph

from .compile import compile_run
from .planner import Planner


class Engine:
    def __init__(self, playbook: Playbook, planner: Planner, layer: CapabilityLayer,
                 fold_registry: Optional[FoldRegistry] = None, *,
                 source_of: Optional[Callable[[DeclaredCapability], str]] = None,
                 checkpointer=None) -> None:
        self.graph = IncidentGraph()
        self.app = compile_run(playbook, planner, layer, self.graph, fold_registry,
                               source_of=source_of, checkpointer=checkpointer)

    def _cfg(self, thread_id: str) -> dict:
        return {"configurable": {"thread_id": thread_id}}

    def start(self, subject: dict, thread_id: str) -> dict:
        init = {"subject": subject, "phase_records": [], "messages": [],
                "current_phase": "", "status": "running"}
        self.app.invoke(init, self._cfg(thread_id))
        return self.state(thread_id)

    def resume(self, thread_id: str, decision: Optional[dict] = None) -> dict:
        cfg = self._cfg(thread_id)
        if decision is not None:
            # carry {decision, actor} into the re-entered gate phase so the run records WHO
            # authorized the write (MUST-7) — written to the checkpoint before resuming.
            self.app.update_state(cfg, {"pending_decision": decision})
        self.app.invoke(None, cfg)
        return self.state(thread_id)

    def provide_input(self, thread_id: str, message: dict) -> dict:
        """Feed an operator answer into a waiting_input run and resume (NICE-9 / SHOU-10 plumbing).
        The message appends to RunState.messages (operator.add); mid-phase continuation with the
        real planner lands at P9."""
        cfg = self._cfg(thread_id)
        self.app.update_state(cfg, {"messages": [message], "status": "running"})
        self.app.invoke(None, cfg)
        return self.state(thread_id)

    def add_messages(self, thread_id: str, messages: list) -> None:
        """Merge queued operator inputs into the run state without resuming (NICE-9 — drained at the
        step boundary; RunState.messages is operator.add so they append)."""
        if messages:
            self.app.update_state(self._cfg(thread_id), {"messages": list(messages)})

    def started(self, thread_id: str) -> bool:
        """True once the run has a checkpoint — lets the API decide start-vs-resume on a cache miss
        (SHOU-21: a rehydrated Engine over a shared checkpointer must never re-start a live thread)."""
        return bool(self.state(thread_id)["values"])

    def state(self, thread_id: str) -> dict:
        snap = self.app.get_state(self._cfg(thread_id))
        return {"values": dict(snap.values), "next": list(snap.next)}

    def is_paused(self, thread_id: str) -> bool:
        return bool(self.state(thread_id)["next"])
