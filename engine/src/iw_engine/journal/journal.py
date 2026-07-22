"""The journal — append-only, human-readable, the SOURCE OF TRUTH (principle 2). Every
phase entry carries the FULL PhaseResult delta as its payload (DESIGN §2.4 R-J1), so
replaying the journal rebuilds the graph + hypothesis store exactly; `refs` is a derived index.
Persisted as NDJSON with a schema-version header; a trailing partial line is skipped on
load (crash-safety, R-J4).
"""
from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..domain.enums import Phase, Source
from ..domain.phase_result import PhaseResult

SCHEMA_VERSION = 1


def _utcnow() -> datetime:
    return datetime.now(UTC)


class JournalEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int
    ts: datetime
    kind: Literal["phase", "step"] = "phase"
    phase_id: Phase | None = None
    actor: str = "engine"                    # WHO produced this entry (engine, or a human approver)
    source: Source | None = None             # provenance of a decision entry (Source.HUMAN on a gate answer)
    intent: str | None = None
    reasoning: str | None = None            # the narrative / thought
    delta: PhaseResult | None = None        # FULL delta (phase entries) — enables replay
    action: dict | None = None              # step: {capability, args}
    observation: dict | None = None         # step: {summary, evidence_ref}
    decision: str | None = None
    refs: dict = Field(default_factory=dict)  # derived index: {nodes, edges, facts, events, hypotheses}


class Journal:
    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self.entries: list[JournalEntry] = []
        self._seq = 0
        self._clock = clock or _utcnow

    def reserve_seq(self) -> int:
        self._seq += 1
        return self._seq

    def append(self, entry: JournalEntry) -> JournalEntry:
        self.entries.append(entry)
        return entry

    def append_phase(self, seq: int, result: PhaseResult, actor: str = "engine") -> JournalEntry:
        refs = {
            "nodes": [n.id for n in result.nodes_touched],
            "edges": [e.id for e in result.edges_added],
            "facts": [f.id for f in result.facts_added],
            "events": [e.id for e in result.events_added],
            "hypotheses": [
                (d.hypothesis.id if d.hypothesis else d.hypothesis_id)
                for d in result.hypotheses_updated
            ],
        }
        return self.append(JournalEntry(
            seq=seq, ts=self._clock(), kind="phase", phase_id=result.phase_id,
            actor=actor, reasoning=result.narrative, delta=result, refs=refs))

    def append_step(self, seq: int, phase_id: Phase, intent: str, reasoning: str,
                    action: dict, observation: dict, decision: str, *,
                    actor: str = "engine", source: Source | None = None) -> JournalEntry:
        return self.append(JournalEntry(
            seq=seq, ts=self._clock(), kind="step", phase_id=phase_id, actor=actor,
            source=source, intent=intent, reasoning=reasoning, action=action,
            observation=observation, decision=decision))

    def step_entries(self) -> list[JournalEntry]:
        return [e for e in self.entries if e.kind == "step"]

    def read(self) -> list[JournalEntry]:
        return list(self.entries)

    def phase_entries(self) -> list[JournalEntry]:
        return [e for e in self.entries if e.kind == "phase" and e.delta is not None]

    # ── NDJSON persistence (R-J4: versioned + partial-line-safe) ──────────────
    def to_ndjson(self) -> str:
        lines = [json.dumps({"schema_version": SCHEMA_VERSION})]
        lines += [e.model_dump_json() for e in self.entries]
        return "\n".join(lines) + "\n"

    @classmethod
    def from_ndjson(cls, text: str, clock: Callable[[], datetime] | None = None) -> Journal:
        j = cls(clock=clock)
        raw = text.split("\n")
        # a partial final write leaves a truncated last line — drop anything unparseable at the tail
        parsed: list[dict] = []
        for i, line in enumerate(raw):
            line = line.strip()
            if not line:
                continue
            try:
                parsed.append(json.loads(line))
            except json.JSONDecodeError:
                if i == len(raw) - 1 or all(not r.strip() for r in raw[i + 1:]):
                    break          # trailing partial line — safe to skip
                raise
        if parsed and "schema_version" in parsed[0]:
            parsed = parsed[1:]
        for d in parsed:
            j.append(JournalEntry.model_validate(d))
        j._seq = max((e.seq for e in j.entries), default=0)
        return j
