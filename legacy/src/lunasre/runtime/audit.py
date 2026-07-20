"""Governance (L9) — append-only audit log of every agent action.

Every tool call and every A2A delegation is recorded with WHO (agent_id, verified
from its workload-identity token where present), WHAT (action + target), and
fingerprints of args/result (not full payloads — keeps the log compact + avoids
storing sensitive data verbatim). This is the audit trail an OWASP Agentic Top-10
review + a bank's compliance team require.

SQLite-backed (local, zero-dependency, testable). A production deployment swaps
the backing for an append-only store (e.g. an immutable log / SIEM) behind the
same `AuditLog` interface — the call sites don't change.

Distinct from memory (incident knowledge) + checkpointer (runtime state):
audit = an immutable record of who-did-what, for governance.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from lunasre.agents.base import PROJECT_ROOT


def _fingerprint(value: Any) -> str:
    """Short stable hash of a value — record THAT a thing happened + its shape,
    without storing the (possibly sensitive) payload verbatim."""
    try:
        blob = json.dumps(value, sort_keys=True, default=str)
    except (TypeError, ValueError):
        blob = str(value)
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:16]


def default_audit_path() -> Path:
    d = PROJECT_ROOT / ".lunasre"
    d.mkdir(exist_ok=True)
    return d / "audit.db"


class AuditLog:
    """Append-only audit log. Implemented over SQLite; one shared file across all
    agent processes (each specialist server + the IC process write to it)."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = str(db_path or default_audit_path())
        self._init_schema()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT,
                    agent_id TEXT,
                    action TEXT,
                    target TEXT,
                    args_fp TEXT,
                    result_fp TEXT,
                    ok INTEGER
                )
                """
            )

    def record(
        self,
        *,
        agent_id: str,
        action: str,
        target: str,
        args: Any = None,
        result: Any = None,
        ok: bool = True,
    ) -> None:
        from datetime import UTC, datetime

        with self._conn() as conn:
            conn.execute(
                "INSERT INTO audit (ts, agent_id, action, target, args_fp, result_fp, ok)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(UTC).isoformat(timespec="seconds"),
                    agent_id,
                    action,
                    target,
                    _fingerprint(args) if args is not None else None,
                    _fingerprint(result) if result is not None else None,
                    1 if ok else 0,
                ),
            )

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT ts, agent_id, action, target, args_fp, result_fp, ok"
                " FROM audit ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM audit").fetchone()["c"]


# Process-wide singleton (cheap; one sqlite file). Call sites use audit().
_AUDIT: AuditLog | None = None


def audit() -> AuditLog:
    global _AUDIT
    if _AUDIT is None:
        _AUDIT = AuditLog()
    return _AUDIT
