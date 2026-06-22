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

    def resume(self, thread_id: str) -> dict:
        self.app.invoke(None, self._cfg(thread_id))
        return self.state(thread_id)

    def state(self, thread_id: str) -> dict:
        snap = self.app.get_state(self._cfg(thread_id))
        return {"values": dict(snap.values), "next": list(snap.next)}

    def is_paused(self, thread_id: str) -> bool:
        return bool(self.state(thread_id)["next"])
