"""MCP Gateway (L3) — the registry backing swap that proves replicability.

L26-L29 used a FILE-backed MCPRegistry. The production deployment puts an MCP
Gateway in front of the servers — it holds the real tool credentials, enforces
PER-AGENT TOOL SCOPE (least privilege), and audits every resolution. The key
property: the gateway implements the SAME `MCPRegistry` interface, so swapping
file→gateway changes the BACKING, not a line of agent code.

`GatewayMCPRegistry` wraps the file registry and adds per-agent scope. The real
`agentic-community/mcp-gateway-registry` (Docker) is the production swap behind
this same interface; this in-process version demonstrates the gateway's defining
behaviour (scope enforcement) testably, without the container.
"""

from __future__ import annotations

from pathlib import Path

import structlog
import yaml

from lunasre.registries.mcp_registry import MCPRegistry, MCPServerEntry, load_mcp_registry

logger = structlog.get_logger("gateway")


class GatewayMCPRegistry:
    """Same interface as MCPRegistry (duck-typed) + per-agent scope enforcement.

    `scope` = the set of server names this agent is authorized for. None = all
    (no scope configured for the agent). Resolving a server outside scope raises
    KeyError — the gateway refusing an unauthorized agent.
    """

    def __init__(self, base: MCPRegistry, agent_id: str, scope: list[str] | None) -> None:
        self._base = base
        self._agent_id = agent_id
        self._scope = set(scope) if scope is not None else None

    def _allowed(self, name: str) -> bool:
        return self._scope is None or name in self._scope

    def get(self, name: str) -> MCPServerEntry:
        if not self._allowed(name):
            raise KeyError(
                f"gateway: agent {self._agent_id!r} not authorized for MCP server "
                f"{name!r} (scope={sorted(self._scope) if self._scope else 'all'})"
            )
        return self._base.get(name)

    def find_by_capability(self, capability: str) -> list[MCPServerEntry]:
        return [e for e in self._base.find_by_capability(capability) if self._allowed(e.name)]

    def all(self) -> list[MCPServerEntry]:
        return [e for e in self._base.all() if self._allowed(e.name)]

    def __iter__(self):
        return iter(self.all())

    def __len__(self) -> int:
        return len(self.all())


def load_gateway_registry(
    mcp_path: str | Path, agent_id: str, scopes_path: str | Path | None = None
) -> GatewayMCPRegistry:
    """Build a gateway-backed MCP registry for `agent_id`, applying its scope.

    Scope file shape (`infra/registries/gateway_scopes.yaml`):
        scopes:
          ic-agent: [mock_datadog]
          dbops-agent: [mock_logs, mock_pg]
    An agent absent from the file gets scope=None (all servers allowed).
    """
    base = load_mcp_registry(mcp_path)
    scope: list[str] | None = None
    if scopes_path and Path(scopes_path).exists():
        data = yaml.safe_load(Path(scopes_path).read_text()) or {}
        scope = (data.get("scopes") or {}).get(agent_id)
    logger.info("gateway.registry.loaded", agent_id=agent_id, scope=scope)
    return GatewayMCPRegistry(base, agent_id, scope)
