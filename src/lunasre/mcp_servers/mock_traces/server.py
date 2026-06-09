"""mock_traces — distributed-trace MCP server (used by NetOps).

One-shot tools (no session lifecycle — traces are fetched whole):
    get_trace(service) -> the trace tree for a service's recent incident
    find_slow_spans(service, threshold_ms) -> spans slower than threshold

Reads mock_data/traces/<service>.json. Run:
    uv run python -m lunasre.mcp_servers.mock_traces.server
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP


def _find_project_root(start: Path) -> Path:
    for parent in [start, *start.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError(f"could not find pyproject.toml ancestor of {start}")


_PROJECT_ROOT = _find_project_root(Path(__file__).resolve())
_TRACES_DIR = _PROJECT_ROOT / "mock_data" / "traces"


def _load_trace(service: str) -> dict[str, Any] | None:
    path = _TRACES_DIR / f"{service}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def get_trace(service: str) -> dict[str, Any]:
    """Return the recent incident trace for `service` (root span + children)."""
    trace = _load_trace(service)
    if trace is None:
        available = [p.stem for p in _TRACES_DIR.glob("*.json")] if _TRACES_DIR.exists() else []
        return {"error": f"no trace for service {service!r}", "available_services": available}
    return trace


def find_slow_spans(service: str, threshold_ms: int = 1000) -> dict[str, Any]:
    """Return spans for `service` whose duration_ms exceeds `threshold_ms`."""
    trace = _load_trace(service)
    if trace is None:
        return {"error": f"no trace for service {service!r}"}
    slow = [
        {
            "span": s.get("name"),
            "duration_ms": s.get("duration_ms"),
            "service": s.get("service"),
            "status": s.get("status"),
        }
        for s in trace.get("spans", [])
        if s.get("duration_ms", 0) > threshold_ms
    ]
    slow.sort(key=lambda s: s["duration_ms"], reverse=True)
    return {
        "service": service,
        "threshold_ms": threshold_ms,
        "slow_span_count": len(slow),
        "slow_spans": slow,
    }


mcp = FastMCP("mock_traces")
mcp.tool()(get_trace)
mcp.tool()(find_slow_spans)


if __name__ == "__main__":
    mcp.run()
