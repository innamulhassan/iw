"""Agent memory — incident-pattern store, behind our own interface.

ARCHITECTURE.md §2 L10: "abstract memory through your own interface; back it with
whichever vendor; expect to migrate the backing within 18 months." Memory has no
standard (Letta / Mem0 / Zep / LangMem all compete), so the RIGHT pattern is an
interface we own + a swappable backing.

- `MemoryStore` — the interface (Protocol). IC depends on THIS, not on a vendor.
- `SqliteMemoryStore` — the L29.P backing (local, zero-dependency, testable).
- A `LettaMemoryStore` (or Mem0/Zep) is a drop-in future swap behind the same
  interface — IC code would not change. That is the whole point.

Distinct from the LangGraph checkpointer: the checkpointer persists RUNTIME STATE
(resume a crashed run); memory persists INCIDENT KNOWLEDGE (recall across runs).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from lunasre.agents.base import PROJECT_ROOT


class IncidentMemory(BaseModel):
    """One remembered incident — what recall returns + what store persists."""

    alert_id: str
    alert_type: str | None
    service: str | None
    root_cause: str
    summary: str
    created_at: str = ""


class MemoryStore(Protocol):
    """The interface IC depends on. Any backing (SQLite now, Letta later) implements it."""

    def recall_similar(
        self, alert_type: str | None, service: str | None, k: int = 3
    ) -> list[IncidentMemory]: ...

    def store_incident(self, incident: IncidentMemory) -> None: ...


def default_db_path() -> Path:
    """Default memory DB location (gitignored)."""
    d = PROJECT_ROOT / ".lunasre"
    d.mkdir(exist_ok=True)
    return d / "memory.db"


class SqliteMemoryStore:
    """Local SQLite-backed incident memory. Implements MemoryStore.

    Recall matches on alert_type OR service (most-recent first) — "have we seen
    this kind of incident, or this service, before?" Simple + sufficient for the
    toy; a production backing would add semantic/embedding recall.
    """

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = str(db_path or default_db_path())
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    alert_id TEXT,
                    alert_type TEXT,
                    service TEXT,
                    root_cause TEXT,
                    summary TEXT,
                    created_at TEXT
                )
                """
            )

    def recall_similar(
        self, alert_type: str | None, service: str | None, k: int = 3
    ) -> list[IncidentMemory]:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT alert_id, alert_type, service, root_cause, summary, created_at
                FROM incidents
                WHERE (alert_type = ? AND ? IS NOT NULL)
                   OR (service = ? AND ? IS NOT NULL)
                ORDER BY id DESC
                LIMIT ?
                """,
                (alert_type, alert_type, service, service, k),
            ).fetchall()
        return [IncidentMemory(**dict(r)) for r in rows]

    def store_incident(self, incident: IncidentMemory) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO incidents
                    (alert_id, alert_type, service, root_cause, summary, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    incident.alert_id,
                    incident.alert_type,
                    incident.service,
                    incident.root_cause,
                    incident.summary,
                    incident.created_at,
                ),
            )

    def count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM incidents").fetchone()["c"]
