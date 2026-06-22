"""RunState (B2) + the conditional-transition predicates (B6).

One run = one LangGraph thread. RunState is the working set every phase reads + writes; LangGraph
checkpoints it after each step. The B6 routers make the run adaptive: loop root-cause while
confidence is low, backtrack from verify if recovery didn't hold.
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Optional, TypedDict

from engine.domain import Playbook, SubjectRef

END = "__end__"  # sentinel: verify-close → END when recovered (B6)


class RunState(TypedDict, total=False):
    subject: SubjectRef
    playbook_ref: str                                  # "incident-triage@1.0.0" (pinned)
    graph: Any                                         # IncidentGraph — the shared world (Part D)
    phase_records: Annotated[list, operator.add]       # one per phase entered (append-reduced)
    messages: Annotated[list, operator.add]            # operator + agent conversation
    current_phase: str
    # the gate-pause signal is read from LangGraph's `next` (interrupt_before), not a state field
    status: str                                        # running|waiting_approval|waiting_input|done|failed


# ── helpers over phase_records (records may be dicts or PhaseRecord models) ──────────
def _phase_of(rec: Any) -> Optional[str]:
    return rec.get("phase") if isinstance(rec, dict) else getattr(rec, "phase", None)


def _output_of(rec: Any) -> Optional[dict]:
    out = rec.get("output") if isinstance(rec, dict) else getattr(rec, "output", None)
    if out is not None and not isinstance(out, dict) and hasattr(out, "model_dump"):
        return out.model_dump()
    return out


def _latest_output(state: RunState, phase_id: str) -> Optional[dict]:
    for rec in reversed(state.get("phase_records", [])):
        if _phase_of(rec) == phase_id:
            return _output_of(rec)
    return None


def attempt(state: RunState, phase_id: str) -> int:
    """Nth entry of this phase — a re-entered phase opens a new attempt (B6 → `…:root-cause:2`)."""
    return sum(1 for r in state.get("phase_records", []) if _phase_of(r) == phase_id) + 1


def top_confidence(state: RunState) -> float:
    """Best candidate confidence from the latest root-cause output (handles {value,basis} or a float)."""
    out = _latest_output(state, "root-cause")
    if not out:
        return 0.0
    vals: list[float] = []
    for c in out.get("candidates") or []:
        conf = c.get("confidence")
        if isinstance(conf, dict):
            vals.append(float(conf.get("value", 0.0)))
        elif isinstance(conf, (int, float)):
            vals.append(float(conf))
    return max(vals, default=0.0)


def recovered(state: RunState) -> bool:
    out = _latest_output(state, "verify-close")
    return bool(out and out.get("recovered"))


def min_confidence_of(playbook: Playbook, phase_id: str = "root-cause") -> float:
    for ph in playbook.phases:
        if ph.id == phase_id and ph.min_confidence is not None:
            return ph.min_confidence
    return 0.0


# ── the B6 routers ──────────────────────────────────────────────────────
def route_after_root_cause(state: RunState, playbook: Playbook) -> str:
    """gather more, or proceed: loop root-cause while top confidence < min_confidence."""
    return "root-cause" if top_confidence(state) < min_confidence_of(playbook) else "remediation"


def route_after_verify(state: RunState) -> str:
    """backtrack with new evidence, or close: verify → root-cause if not recovered, else END."""
    return END if recovered(state) else "root-cause"
