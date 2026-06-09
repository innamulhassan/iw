"""mock_pg — Postgres-metrics MCP server (available to DBOps as a second source).

One-shot tools:
    query_metric(service, metric) -> a metric time-series snapshot
    connection_pool_status(service) -> current pool stats

Synthetic, deterministic data keyed by service. Run:
    uv run python -m lunasre.mcp_servers.mock_pg.server
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

# Synthetic metric snapshots keyed by (service, metric).
_METRICS: dict[str, dict[str, Any]] = {
    "payments-api": {
        "connections_active": [180, 195, 200, 200, 200],
        "connections_max": 200,
        "mem_rss_gb": [11.0, 12.8, 14.2, 14.4, 14.4],
        "mem_limit_gb": 14.0,
        "replica_lag_s": [2, 8, 32, 47, 55],
    },
}


def query_metric(service: str, metric: str) -> dict[str, Any]:
    """Return a recent time-series snapshot for `service`/`metric`."""
    svc = _METRICS.get(service)
    if svc is None:
        return {"error": f"no metrics for service {service!r}", "available": list(_METRICS.keys())}
    if metric not in svc:
        return {
            "error": f"no metric {metric!r} for {service!r}",
            "available_metrics": list(svc.keys()),
        }
    return {"service": service, "metric": metric, "series": svc[metric]}


def connection_pool_status(service: str) -> dict[str, Any]:
    """Return current connection-pool stats for `service`."""
    svc = _METRICS.get(service)
    if svc is None:
        return {"error": f"no metrics for service {service!r}", "available": list(_METRICS.keys())}
    active = svc["connections_active"][-1]
    mx = svc["connections_max"]
    return {
        "service": service,
        "connections_active": active,
        "connections_max": mx,
        "utilization_pct": round(100 * active / mx, 1),
        "saturated": active >= mx,
        "replica_lag_s": svc["replica_lag_s"][-1],
    }


mcp = FastMCP("mock_pg")
mcp.tool()(query_metric)
mcp.tool()(connection_pool_status)


if __name__ == "__main__":
    mcp.run()
