"""The capability layer — governed, mockable access to the tools. An Adapter is a pure pair:
the tool's `normalize(raw) -> Operation[]` (deterministic, binding-agnostic) plus its DATA
descriptors (`provider`, `intents`, `effect`, `binding`). The live fetch is the ONE
side-effecting seam and lives behind `Source` (see `sources.py`) — swapped for a fixture
loader to mock (DESIGN §2.5 R-K1).

The layer resolves an intent to its adapter, enforces the read/write boundary (writes only in
an approved gate — the human-approval invariant), and records every invocation for the audit
trail. `serve()` is the collapsed, GATE-FIRST path (VALIDATION-VERDICT §C.3 / §D): resolve →
gate → transport.fetch(binding, ...) → normalize, so a blocked write never reaches the
transport (fixing the write-before-gate bug). `invoke()` is the same governance over a
PRE-FETCHED raw payload (the seam unit tests feed directly).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import Binding, Effect
from ..domain.operations import Operation
from .sources import (
    McpSource,
    MockSource,
    RestSource,
    RoutedSource,
    ScenarioSource,
    Source,
)

__all__ = [
    "Adapter",
    "CapabilityCall",
    "CapabilityLayer",
    "Invocation",
    "McpSource",
    "MockSource",
    "RestSource",
    "RoutedSource",
    "ScenarioSource",
    "Source",
]


def _summarize_ops(ops: list[Operation]) -> str:
    """A one-line, human-readable summary of what a tool call folded into the graph — the 'out'
    side of the trace (e.g. '2 entities · 12 facts · 1 change'). Reads off the op class names so it
    needs no coupling to the concrete op types."""
    from collections import Counter
    c = Counter(type(o).__name__ for o in ops)
    parts: list[str] = []
    n = c.get("AddNode", 0)
    if n:
        parts.append(f"{n} entit{'y' if n == 1 else 'ies'}")
    if c.get("AddFact"):
        parts.append(f"{c['AddFact']} fact{'s' if c['AddFact'] != 1 else ''}")
    if c.get("AddEvent"):
        parts.append(f"{c['AddEvent']} event{'s' if c['AddEvent'] != 1 else ''}")
    if c.get("AddEdge"):
        parts.append(f"{c['AddEdge']} link{'s' if c['AddEdge'] != 1 else ''}")
    if c.get("ProposeHypothesis"):
        parts.append(f"{c['ProposeHypothesis']} hypothesis")
    if c.get("UpdateHypothesis"):
        parts.append(f"{c['UpdateHypothesis']} hypothesis update{'s' if c['UpdateHypothesis'] != 1 else ''}")
    return " · ".join(parts) or "no new data"


class CapabilityCall(BaseModel):
    """A capability the planner wants invoked (JUDGMENT chooses the intent; the layer
    resolves it to a tool and produces typed data ops)."""

    model_config = ConfigDict(extra="forbid")

    intent: str
    params: dict = Field(default_factory=dict)


@runtime_checkable
class Adapter(Protocol):
    provider: str
    intents: frozenset[str]
    effect: Effect
    binding: Binding

    def normalize(self, raw: dict) -> list[Operation]: ...


class Invocation(BaseModel):
    """One audit record of a capability call."""

    model_config = ConfigDict(extra="forbid")

    intent: str
    provider: str
    effect: Effect
    op_count: int
    blocked: bool = False
    reason: str | None = None
    # what went IN (the query the reasoner issued) and a one-line summary of what came OUT
    # (what the tool folded into the graph) — so the UI reads like a real agent trace: query + result.
    params: dict = Field(default_factory=dict)
    summary: str = ""
    # agent-trace span (obs 9: "when tool ran, how long, tool vs workflow"). Wall-clock timing,
    # stamped by the engine around serve() — ephemeral (never journaled, never in export_bundle),
    # so goldens stay deterministic. `kind` distinguishes tool | workflow | llm | handoff.
    kind: str = "tool"
    started_at: str | None = None          # ISO wall-clock when the call began
    duration_ms: float | None = None       # how long the fetch+normalize took


class CapabilityLayer:
    def __init__(self, adapters: list[Adapter], source: Source | None = None) -> None:
        self.adapters = list(adapters)
        self.source = source
        self._by_intent: dict[str, Adapter] = {}
        for a in adapters:
            for i in a.intents:
                self._by_intent[i] = a

    def resolve(self, intent: str) -> Adapter | None:
        return self._by_intent.get(intent)

    # ── gate + audit (shared by both call paths) ──────────────────────────────
    def _gate(self, a: Adapter | None, intent: str, *, allow_write: bool) -> Invocation | None:
        """Return a blocked Invocation if the call must not proceed, else None."""
        if a is None:
            return Invocation(intent=intent, provider="?", effect=Effect.READ, op_count=0,
                              blocked=True, reason=f"no capability for intent '{intent}'")
        if a.effect == Effect.WRITE and not allow_write:
            # the human-approval invariant: a write cannot execute outside an approved gate
            return Invocation(intent=intent, provider=a.provider, effect=a.effect, op_count=0,
                              blocked=True, reason="write blocked — no approved gate")
        return None

    def _fold(self, a: Adapter, intent: str, raw: dict, params: dict | None = None
              ) -> tuple[list[Operation], Invocation]:
        ops = a.normalize(raw)
        return ops, Invocation(intent=intent, provider=a.provider, effect=a.effect,
                               op_count=len(ops), params=dict(params or {}),
                               summary=_summarize_ops(ops))

    # ── the two entry points ──────────────────────────────────────────────────
    def invoke(self, intent: str, raw: dict, *, allow_write: bool) -> tuple[list[Operation], Invocation]:
        """Gate + normalize a PRE-FETCHED raw payload."""
        a = self._by_intent.get(intent)
        blocked = self._gate(a, intent, allow_write=allow_write)
        if blocked is not None:
            return [], blocked
        return self._fold(a, intent, raw)

    def serve(self, call: CapabilityCall, *, allow_write: bool) -> tuple[list[Operation], Invocation]:
        """The collapsed, GATE-FIRST path: resolve → gate → transport.fetch(binding, ...) →
        normalize. A blocked write returns before any fetch, so no side-effect ever precedes
        the gate."""
        a = self._by_intent.get(call.intent)
        blocked = self._gate(a, call.intent, allow_write=allow_write)
        if blocked is not None:
            return [], blocked
        raw = self.source.fetch(a.binding, call.intent, call.params) if self.source else {}
        return self._fold(a, call.intent, raw, params=call.params)
