"""The per-session event log — one ordered, append-only stream. B8.3 (polling model).

Every event — a user message, an agent message, a gate prompt/decision, a phase/graph delta — is
**appended** with a per-session sequence number. Clients **poll** `since(after_seq)` every few
seconds and apply what's new; join/reconnect = snapshot + resume-from-seq. No push infra — no Redis,
no WebSocket, no SSE — so there is no cross-server fan-out: every server is stateless and just reads
the shared log. The real impl is a durable append-only collection in the read-model store (Mongo),
or Postgres; this in-memory log is the mockable equivalent. (Push — SSE or WebSocket+bus — can be
added later behind this same `seq` interface without touching anything else.)
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EventLog(Protocol):
    def append(self, session_id: str, event: dict) -> int: ...
    def since(self, session_id: str, after_seq: int = 0) -> list[dict]: ...
    def snapshot_seq(self, session_id: str) -> int: ...


class InMemoryEventLog:
    def __init__(self) -> None:
        self._log: dict[str, list[dict]] = {}
        self._seq: dict[str, int] = {}

    def append(self, session_id: str, event: dict) -> int:
        seq = self._seq.get(session_id, 0) + 1
        self._seq[session_id] = seq
        self._log.setdefault(session_id, []).append({**event, "seq": seq})
        return seq

    def since(self, session_id: str, after_seq: int = 0) -> list[dict]:
        return [e for e in self._log.get(session_id, []) if e["seq"] > after_seq]

    def snapshot_seq(self, session_id: str) -> int:
        return self._seq.get(session_id, 0)
