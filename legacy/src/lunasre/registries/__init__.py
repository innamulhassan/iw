"""Registry interface for LunaSRE — runtime discovery of MCP servers and A2A agents.

Phase 1 (Chunk 1, now): file-based YAML loader (this package).
Phase 4: MCP Gateway client behind the same interface — agent code unchanged.

This is the "swap-without-rewrite" replicability proof from ARCHITECTURE.md §4.
"""

from lunasre.registries.agent_registry import (
    AgentEntry,
    AgentRegistry,
    load_agent_registry,
)
from lunasre.registries.gateway import GatewayMCPRegistry, load_gateway_registry
from lunasre.registries.mcp_registry import (
    MCPRegistry,
    MCPServerEntry,
    load_mcp_registry,
)

__all__ = [
    "AgentEntry",
    "AgentRegistry",
    "GatewayMCPRegistry",
    "MCPRegistry",
    "MCPServerEntry",
    "load_agent_registry",
    "load_gateway_registry",
    "load_mcp_registry",
]
