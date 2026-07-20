"""mock_logs — stateful-session MCP server.

Tools (lifecycle order):
    open_log_session(window_start, window_end, service) -> session_id
    grep(session_id, pattern) -> matching lines
    tail(session_id, n) -> last n lines
    close_log_session(session_id) -> "closed"

State (module-level dict, scoped to this server's process lifetime — that's
why DBOps holds the MCP ClientSession open across multiple tool calls):
    _SESSIONS[session_id] = {window_start, window_end, service, cached_lines}

Run as an MCP server:
    uv run python -m lunasre.mcp_servers.mock_logs.server
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


def _find_project_root(start: Path) -> Path:
    for parent in [start, *start.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError(f"could not find pyproject.toml ancestor of {start}")


_PROJECT_ROOT = _find_project_root(Path(__file__).resolve())
_LOGS_DIR = _PROJECT_ROOT / "mock_data" / "logs"


# Module-level session store. Lives for the server-process lifetime.
_SESSIONS: dict[str, dict[str, Any]] = {}


def _load_log_lines(service: str) -> list[dict[str, Any]]:
    """Read `mock_data/logs/<service>.jsonl` and return parsed line objects.

    Each line is `{"ts": ISO8601, "level": str, "msg": str}`. Missing file → [].
    """
    path = _LOGS_DIR / f"{service}.jsonl"
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for raw in path.read_text().splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            out.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return out


def _within_window(line: dict[str, Any], start: str, end: str) -> bool:
    """ISO-string lexicographic compare — works because ISO-8601 sorts correctly."""
    ts = line.get("ts", "")
    return start <= ts <= end


def open_log_session(window_start: str, window_end: str, service: str) -> dict[str, Any]:
    """Open a stateful log session.

    Loads + filters the service's log file once for the given window; returns a
    session_id that grep/tail reuse without re-reading the file.
    """
    all_lines = _load_log_lines(service)
    cached = [line for line in all_lines if _within_window(line, window_start, window_end)]
    session_id = f"log-{service}-{uuid.uuid4().hex[:8]}"
    _SESSIONS[session_id] = {
        "window_start": window_start,
        "window_end": window_end,
        "service": service,
        "cached_lines": cached,
    }
    return {
        "session_id": session_id,
        "service": service,
        "window_start": window_start,
        "window_end": window_end,
        "loaded_line_count": len(cached),
    }


def grep(session_id: str, pattern: str) -> dict[str, Any]:
    """Substring-match the session's cached lines.

    Pattern is case-insensitive substring match (sufficient for mock).
    """
    session = _SESSIONS.get(session_id)
    if session is None:
        return {
            "error": f"unknown session_id {session_id!r}",
            "open_sessions": list(_SESSIONS.keys()),
        }
    needle = pattern.lower()
    matches = [
        f"{line.get('ts', '?')} {line.get('level', '?')} {line.get('msg', '')}"
        for line in session["cached_lines"]
        if needle in line.get("msg", "").lower() or needle in line.get("level", "").lower()
    ]
    return {
        "session_id": session_id,
        "pattern": pattern,
        "match_count": len(matches),
        "matches": matches,
    }


def tail(session_id: str, n: int = 20) -> dict[str, Any]:
    """Return the last n cached lines for the session."""
    session = _SESSIONS.get(session_id)
    if session is None:
        return {
            "error": f"unknown session_id {session_id!r}",
            "open_sessions": list(_SESSIONS.keys()),
        }
    lines = [
        f"{line.get('ts', '?')} {line.get('level', '?')} {line.get('msg', '')}"
        for line in session["cached_lines"][-n:]
    ]
    return {"session_id": session_id, "lines": lines, "count": len(lines)}


def close_log_session(session_id: str) -> dict[str, Any]:
    """Release the session's cached state."""
    if session_id in _SESSIONS:
        del _SESSIONS[session_id]
        return {"session_id": session_id, "status": "closed"}
    return {"session_id": session_id, "status": "unknown", "error": "no such session"}


mcp = FastMCP("mock_logs")
mcp.tool()(open_log_session)
mcp.tool()(grep)
mcp.tool()(tail)
mcp.tool()(close_log_session)


if __name__ == "__main__":
    mcp.run()
