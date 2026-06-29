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
    pending_decision: dict                             # {decision, actor} carried into a resumed gate phase (MUST-7)
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


def recovered(state: RunState, phase_id: str = "verify-close") -> bool:
    out = _latest_output(state, phase_id)
    return bool(out and out.get("recovered"))


def min_confidence_of(playbook: Playbook, phase_id: str = "root-cause") -> Optional[float]:
    """The phase's confidence threshold, or None when none is configured (so a wired loop with no
    threshold is a no-op, not a 0.0-floored always-satisfied loop — NICE-10)."""
    for ph in playbook.phases:
        if ph.id == phase_id:
            return ph.min_confidence
    return None


MAX_LOOP_ATTEMPTS = 5   # SHOU-9: a never-converging loop ADVANCES (→ write/escalate), never recurses to a crash


# ── the B6 routers — generic, driven by playbook METADATA (MUST-6) ───────
def route_after_loop(state: RunState, playbook: Playbook, phase_id: str,
                     max_attempts: int = MAX_LOOP_ATTEMPTS) -> str:
    """Loop the phase while top confidence < its threshold; cap re-entries so inconclusive evidence
    proceeds (the next phase can escalate) instead of spinning into a recursion error. Returns the
    routing token 'loop' (re-enter) or 'advance' (proceed)."""
    if state.get("status") in ("waiting_input", "failed"):
        return "advance"                                 # halt-ish states fall through (handled by status)
    thr = min_confidence_of(playbook, phase_id)
    if thr is None:                                      # no threshold configured → never loop
        return "advance"
    if attempt(state, phase_id) > max_attempts:
        return "advance"
    return "loop" if top_confidence(state) < thr else "advance"


def route_after_backtrack(state: RunState, phase_id: str, backtrack_to: str) -> str:
    """Close when the phase's `recovered` check holds, else backtrack to the named earlier phase.
    Returns 'close' (→ END) or 'backtrack' (→ backtrack_to)."""
    return "close" if recovered(state, phase_id) else "backtrack"


def route_phase(state: RunState, playbook: Playbook, phase_id: str, next_dest: str,
                backtrack_to: Optional[str] = None) -> str:
    """The single phase-exit router (used by compile). 'halt' if the phase paused for operator input;
    otherwise loop / backtrack / advance driven by the phase's own metadata (min_confidence /
    backtrack_to) — so a differently-named playbook wires correctly with zero code change (MUST-6)."""
    if state.get("status") in ("waiting_input", "failed"):
        return "halt"
    if backtrack_to is not None:
        return route_after_backtrack(state, phase_id, backtrack_to)   # "close" | "backtrack"
    if min_confidence_of(playbook, phase_id) is not None:
        return route_after_loop(state, playbook, phase_id)            # "loop" | "advance"
    return "advance"


# back-compat wrappers (used by the B6 unit tests + the legacy hardcoded two-phase shape)
def route_after_root_cause(state: RunState, playbook: Playbook) -> str:
    return "remediation" if route_after_loop(state, playbook, "root-cause") == "advance" else "root-cause"


def route_after_verify(state: RunState) -> str:
    return END if route_after_backtrack(state, "verify-close", "root-cause") == "close" else "root-cause"
