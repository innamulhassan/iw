"""The phase loop — plan → execute (B3), with the capability layer called in the loop (B4),
the gate (B5, via `approved`), and error handling (E4: retry transients, run-remaining on a
permanent failure).

Each phase is a model-driven agent loop, bounded by the phase's `needs` (intents it may use) and
`effect` (read-only or write). The planner picks a need; the capability layer resolves it to a
governed capability and invokes it; the result is folded into the graph; a Step is logged; the
typed output is refreshed — until the output is sufficient (validates vs its schema).
"""
from __future__ import annotations

from typing import Callable, Optional

from engine.capability import CapabilityLayer, Denied
from engine.domain import DeclaredCapability, PhaseRecord, PhaseSpec, PhaseState, Playbook, Step, SubjectRef
from engine.domain.outputs import OUTPUT_TYPES
from engine.graph_runtime import FoldRegistry

from .errors import PermanentError, TransientError
from .planner import Planner
from .state import RunState, attempt


class WaitingInput(Exception):
    """The planner wants an operator answer mid-phase (status = waiting_input)."""

    def __init__(self, record: PhaseRecord) -> None:
        super().__init__("phase is waiting on operator input")
        self.record = record


def sufficient(output: dict, output_type) -> bool:
    """The output is sufficient when its required fields are present + it validates (B3 stop test)."""
    if not output:
        return False
    try:
        output_type.model_validate(output)
        return True
    except Exception:
        return False


def _default_source_of(cap: DeclaredCapability) -> str:
    return cap.provider


def _coerce_subject(subject) -> SubjectRef:
    return subject if isinstance(subject, SubjectRef) else SubjectRef.model_validate(subject)


def _invoke_with_retry(layer: CapabilityLayer, cap_id: str, args: dict, approved: bool, retry) -> dict:
    """Invoke, retrying transient errors per `defaults.retry` (no real backoff in-process/in-test)."""
    attempts = max(1, retry.max)
    last: Optional[Exception] = None
    for _ in range(attempts):
        try:
            return layer.invoke(cap_id, args, approved=approved)
        except TransientError as exc:
            last = exc
    raise PermanentError(f"{cap_id} failed after {attempts} attempts: {last}")


def run_phase(state: RunState, phase: PhaseSpec, playbook: Playbook, planner: Planner,
              layer: CapabilityLayer, fold_registry: Optional[FoldRegistry] = None, *,
              source_of: Optional[Callable[[DeclaredCapability], str]] = None,
              approved: bool = False, max_iters: int = 50) -> PhaseRecord:
    subject = _coerce_subject(state["subject"])
    rec = PhaseRecord(
        id=f"{subject.id}:{phase.id}:{attempt(state, phase.id)}",
        subject=subject, phase=phase.id, goal=phase.goal or "",
        state=PhaseState.active, plan=planner.plan(state, phase), steps=[],
    )
    output_type = OUTPUT_TYPES[phase.output]
    graph = state.get("graph")
    src_of = source_of or _default_source_of
    retry = playbook.defaults.retry
    run_remaining = playbook.defaults.on_failure == "run-remaining"
    had_failure = False

    for _ in range(max_iters):
        if sufficient(rec.output or {}, output_type):
            break
        if planner.wants_operator(state, rec):
            rec.state = PhaseState.waiting_input
            raise WaitingInput(rec)
        nxt = planner.next_action(state, rec, list(phase.needs))
        if nxt is None:
            break
        need, args = nxt

        # B4 — capability in the loop: resolve (effect boundary) → govern + invoke (gate)
        caps = layer.resolve(need, phase.effect)
        if not caps:
            raise Denied(f"no capability for need {need!r} in a {phase.effect.value} phase")
        cap = caps[0]
        try:
            result = _invoke_with_retry(layer, cap.id, args, approved, retry)
        except PermanentError as exc:                       # E4 — error_handler / on_failure
            had_failure = True
            rec.steps.append(Step(seq=len(rec.steps) + 1, kind="tool_call", capability=cap.id,
                                  input=args, result={"error": str(exc)},
                                  note="permanent failure → error_handler"))
            if run_remaining:
                continue                                    # finish the remaining independent steps
            rec.state = PhaseState.failed
            raise

        # fold the result into the shared graph via the source's adapter
        touched: list[str] = []
        if graph is not None and fold_registry is not None:
            try:
                touched = fold_registry.fold(src_of(cap), result, graph)
            except KeyError:
                touched = []
        evidence = result.get("evidence", []) if isinstance(result, dict) else []

        rec.steps.append(Step(seq=len(rec.steps) + 1, kind="tool_call", capability=cap.id,
                              input=args, result=result, touched=touched, evidence=evidence))
        rec.output = planner.update_output(state, rec)

    if had_failure and run_remaining:
        rec.state = PhaseState.blocked                      # partial — the phase reports blocked
        return rec
    output_type.model_validate(rec.output or {})            # contract check (E) — raises if invalid
    rec.state = PhaseState.done
    return rec
