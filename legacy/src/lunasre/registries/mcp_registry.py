"""MCP server registry — runtime discovery (Phase 1: file-based)."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class MCPServerEntry(BaseModel):
    """One MCP server's discovery metadata."""

    name: str
    description: str
    transport: Literal["stdio", "sse", "streamable_http"]
    command: list[str] | None = None  # for stdio
    url: str | None = None  # for sse / streamable_http
    capabilities: list[str] = Field(default_factory=list)
    supports_sessions: bool = False
    auth: str | None = None


class MCPRegistry:
    """Lookup interface for MCP servers. Phase 1 = file-backed; Phase 4 swaps to MCP Gateway."""

    def __init__(self, entries: dict[str, MCPServerEntry]) -> None:
        self._entries = entries

    def get(self, name: str) -> MCPServerEntry:
        if name not in self._entries:
            raise KeyError(f"MCP server {name!r} not in registry")
        return self._entries[name]

    def find_by_capability(self, capability: str) -> list[MCPServerEntry]:
        return [e for e in self._entries.values() if capability in e.capabilities]

    def all(self) -> list[MCPServerEntry]:
        return list(self._entries.values())

    def __iter__(self):
        return iter(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)


def load_mcp_registry(path: str | Path) -> MCPRegistry:
    """Load an MCP registry from a YAML file."""
    data = yaml.safe_load(Path(path).read_text())
    entries = {name: MCPServerEntry(name=name, **spec) for name, spec in data["servers"].items()}
    return MCPRegistry(entries)
