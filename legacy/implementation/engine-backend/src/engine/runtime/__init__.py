"""P4 · the engine on LangGraph (Part B). P4a here: the playbook loader (B1 parse) + RunState (B2) +
the B6 transition predicates. P4b adds the phase loop + StateGraph compile; P4c the gate + failure."""
from __future__ import annotations

from .compile import compile_run
from .engine import Engine
from .errors import PermanentError, TransientError
from .loader import load_playbook, load_playbook_text, split_frontmatter
from .phase import WaitingInput, run_phase, sufficient
from .planner import MultiPhasePlanner, Planner, ScriptedPlanner
from .state import (
    END,
    RunState,
    attempt,
    min_confidence_of,
    recovered,
    route_after_root_cause,
    route_after_verify,
    top_confidence,
)

__all__ = [
    "load_playbook", "load_playbook_text", "split_frontmatter",
    "RunState", "END", "attempt", "top_confidence", "recovered", "min_confidence_of",
    "route_after_root_cause", "route_after_verify",
    "Planner", "ScriptedPlanner", "MultiPhasePlanner", "run_phase", "sufficient", "WaitingInput",
    "compile_run", "Engine", "TransientError", "PermanentError",
]
