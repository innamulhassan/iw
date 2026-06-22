"""The planner — what drives a phase's agent loop (B3). The real impl is LLM-backed (swapped in at
P9); tests + the mocked end-to-end use a `ScriptedPlanner`. Keeping it behind a Protocol means the
loop (phase.py) is testable with zero model calls.
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from engine.domain import PhaseSpec


@runtime_checkable
class Planner(Protocol):
    def plan(self, state: dict, phase: PhaseSpec) -> str: ...

    def next_action(self, state: dict, rec, allowed: list[str]) -> Optional[tuple[str, dict]]:
        """The next (need, args), or None when the output is ready. `need` must be in `allowed`."""

    def update_output(self, state: dict, rec) -> dict:
        """Build/refresh the typed phase output from what the graph now shows."""

    def wants_operator(self, state: dict, rec) -> bool:
        """True to pause and ask the operator a question (waiting_input)."""


class ScriptedPlanner:
    """Deterministic planner for tests: a fixed list of (need, args) actions, then a final output."""

    def __init__(self, plan_text: str, actions: list[tuple[str, dict]], output: dict,
                 *, ask_operator_after: Optional[int] = None) -> None:
        self._plan = plan_text
        self._actions = list(actions)
        self._output = output
        self._ask_after = ask_operator_after
        self._i = 0

    def plan(self, state: dict, phase: PhaseSpec) -> str:
        return self._plan

    def next_action(self, state: dict, rec, allowed: list[str]) -> Optional[tuple[str, dict]]:
        if self._i >= len(self._actions):
            return None
        need, args = self._actions[self._i]
        self._i += 1
        return need, args

    def update_output(self, state: dict, rec) -> dict:
        return self._output if self._i >= len(self._actions) else (rec.output or {})

    def wants_operator(self, state: dict, rec) -> bool:
        return self._ask_after is not None and self._i == self._ask_after


class MultiPhasePlanner:
    """Dispatches to a per-phase sub-planner — drives a full multi-phase run in tests / mocked E2E."""

    def __init__(self, by_phase: dict[str, Planner]) -> None:
        self._by = by_phase

    def _sub(self, x) -> Planner:
        # a PhaseSpec exposes `.id`; a PhaseRecord exposes `.phase` — both name the phase
        phase_id = x.phase if hasattr(x, "phase") else x.id
        return self._by[phase_id]

    def plan(self, state: dict, phase: PhaseSpec) -> str:
        return self._sub(phase).plan(state, phase)

    def next_action(self, state: dict, rec, allowed: list[str]) -> Optional[tuple[str, dict]]:
        return self._sub(rec).next_action(state, rec, allowed)

    def update_output(self, state: dict, rec) -> dict:
        return self._sub(rec).update_output(state, rec)

    def wants_operator(self, state: dict, rec) -> bool:
        return self._sub(rec).wants_operator(state, rec)
