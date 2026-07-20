"""Tests for the mock_logs STATEFUL-session MCP server (Phase 2).

The whole point of mock_logs is that a session_id from open_log_session survives
across grep/tail/close calls — which only works if the server SUBPROCESS stays
alive between calls. These tests prove:

  1. The lifecycle works over a long-lived MCPLiveSession (open -> grep -> tail -> close).
  2. The SAME lifecycle FAILS over spawn-per-call (each call = fresh subprocess =
     empty session store) — the contrast that justifies MCPLiveSession existing.

No LLM is involved — these exercise the L3 transport + server logic directly.
"""

from __future__ import annotations

import json

import pytest

from lunasre.agents.base import load_agent_config, resolve_mcp_servers
from lunasre.runtime.mcp_session import MCPLiveSession

# payments-api log window (see mock_data/logs/payments-api.jsonl).
_WINDOW_START = "2026-06-15T03:13:30Z"
_WINDOW_END = "2026-06-15T03:14:30Z"
_SERVICE = "payments-api"


def _mock_logs_server():
    config = load_agent_config("dbops-agent")  # dbops uses mock_logs
    servers = resolve_mcp_servers(config)
    assert servers, "dbops should resolve mock_logs"
    return servers[0]


@pytest.mark.asyncio
async def test_mock_logs_exposes_four_session_tools():
    async with MCPLiveSession(_mock_logs_server()) as live:
        names = {t["name"] for t in live.tool_schemas}
    assert names == {"open_log_session", "grep", "tail", "close_log_session"}


@pytest.mark.asyncio
async def test_stateful_session_lifecycle_over_live_session():
    """open -> grep -> tail -> close, all reusing one session_id on one live session."""
    async with MCPLiveSession(_mock_logs_server()) as live:
        # 1. open
        opened = json.loads(
            await live.call(
                "open_log_session",
                {"window_start": _WINDOW_START, "window_end": _WINDOW_END, "service": _SERVICE},
            )
        )
        session_id = opened["session_id"]
        assert opened["service"] == _SERVICE
        assert opened["loaded_line_count"] > 0  # window covers several lines

        # 2. grep reuses the opened window — finds the connection-pool + OOM lines
        grepped = json.loads(
            await live.call("grep", {"session_id": session_id, "pattern": "connection"})
        )
        assert grepped["match_count"] >= 1
        assert any("connection pool exhausted" in m for m in grepped["matches"])

        # 3. tail reuses the same session
        tailed = json.loads(await live.call("tail", {"session_id": session_id, "n": 3}))
        assert tailed["count"] == 3

        # 4. close
        closed = json.loads(await live.call("close_log_session", {"session_id": session_id}))
        assert closed["status"] == "closed"

        # 5. after close, grep on the dead session_id errors
        after = json.loads(await live.call("grep", {"session_id": session_id, "pattern": "x"}))
        assert "error" in after


@pytest.mark.asyncio
async def test_grep_for_oom_finds_the_oom_kill_line():
    async with MCPLiveSession(_mock_logs_server()) as live:
        opened = json.loads(
            await live.call(
                "open_log_session",
                {"window_start": _WINDOW_START, "window_end": _WINDOW_END, "service": _SERVICE},
            )
        )
        grepped = json.loads(
            await live.call("grep", {"session_id": opened["session_id"], "pattern": "OOM"})
        )
        assert grepped["match_count"] >= 1
        assert any("oom-killer" in m.lower() for m in grepped["matches"])


@pytest.mark.asyncio
async def test_spawn_per_call_breaks_stateful_session():
    """CONTRAST: a fresh subprocess per call cannot see a prior call's session_id.

    This is WHY DBOps must use MCPLiveSession, not the spawn-per-call call_mcp_tool.
    """
    from lunasre.agents.base import call_mcp_tool

    server = _mock_logs_server()
    # open in one (spawned-then-torn-down) subprocess
    opened = json.loads(
        await call_mcp_tool(
            server,
            "open_log_session",
            {"window_start": _WINDOW_START, "window_end": _WINDOW_END, "service": _SERVICE},
        )
    )
    session_id = opened["session_id"]
    # grep in a DIFFERENT subprocess — its _SESSIONS dict is empty → unknown session
    grepped = json.loads(
        await call_mcp_tool(server, "grep", {"session_id": session_id, "pattern": "OOM"})
    )
    assert "error" in grepped
    assert "unknown session_id" in grepped["error"]
