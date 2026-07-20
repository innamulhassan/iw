"""mock_datadog — first MCP server. Tools: drill_into_alert, tail_logs.

Run as an MCP server:
    uv run python -m lunasre.mcp_servers.mock_datadog.server

Smoke-test the tool functions directly (no MCP transport):
    just smoke-datadog
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


def _find_project_root(start: Path) -> Path:
    """Walk up from `start` looking for the project root marker (pyproject.toml)."""
    for parent in [start, *start.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError(f"could not find pyproject.toml ancestor of {start}")


_PROJECT_ROOT = _find_project_root(Path(__file__).resolve())
_ALERTS_PATH = _PROJECT_ROOT / "mock_data" / "alerts.json"


def _load_alerts() -> list[dict[str, Any]]:
    return json.loads(_ALERTS_PATH.read_text())


def drill_into_alert(alert_id: str) -> dict[str, Any]:
    """Return the full alert payload for `alert_id`, or an error dict if not found."""
    alerts = _load_alerts()
    for alert in alerts:
        if str(alert.get("alert_id")) == str(alert_id):
            return alert
    return {
        "error": f"alert {alert_id!r} not found",
        "available_ids": [a["alert_id"] for a in alerts],
    }


_SYNTHETIC_LOGS: dict[str, list[str]] = {
    "payments-api": [
        "2026-06-15T03:13:55Z ERROR connection pool exhausted, max=200",
        "2026-06-15T03:14:00Z ERROR OOM kill: process killed by oom-killer",
        "2026-06-15T03:14:05Z WARN  replica lag > 30s",
    ],
    "user-service-cross-region": [
        "2026-06-15T03:14:50Z WARN  cross-AZ p99 latency 8.2s",
        "2026-06-15T03:14:55Z ERROR timeout calling user-service us-east-1b",
    ],
    "search-api": [
        "2026-06-15T03:14:58Z ERROR 5xx surge since deploy 4f3a2e1",
        "2026-06-15T03:15:01Z WARN  error rate 8x baseline",
    ],
}


def tail_logs(service: str, n: int = 20) -> dict[str, Any]:
    """Return the last `n` synthetic log lines for `service`.

    Phase 1 = synthetic stub so the IC agent (Chunk 2) has something to summarize.
    Phase 2 = mock_logs MCP server with stateful sessions reads real `.jsonl` files.
    """
    lines = _SYNTHETIC_LOGS.get(
        service,
        [f"(no synthetic logs for service {service!r} — Phase 2 will read real .jsonl files)"],
    )
    return {"service": service, "lines": lines[-n:], "phase": "1 — synthetic stub"}


# Register the tools with FastMCP so this module can also run as an MCP server entrypoint.
mcp = FastMCP("mock_datadog")
mcp.tool()(drill_into_alert)
mcp.tool()(tail_logs)


if __name__ == "__main__":
    mcp.run()
