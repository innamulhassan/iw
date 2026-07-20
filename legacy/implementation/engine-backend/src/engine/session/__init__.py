"""P5 · live session (B8) — the per-session lock (B8.2), the append-only event log (B8.3, polled),
and the session manager (lifecycle + B8.4 edge cases). A thin layer: a lock serializes the one run,
an event log + client polling syncs the many clients, the durable stores hold the truth."""
from __future__ import annotations

from .eventlog import EventLog, InMemoryEventLog
from .lock import InMemoryRunLock, RunLock
from .manager import FreeChat, NotAuthorized, NotWriter, Session, SessionManager, session_id_for

__all__ = [
    "RunLock", "InMemoryRunLock",
    "EventLog", "InMemoryEventLog",
    "SessionManager", "Session", "FreeChat", "NotAuthorized", "NotWriter", "session_id_for",
]
