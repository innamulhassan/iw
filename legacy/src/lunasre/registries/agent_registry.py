"""A2A agent registry — runtime discovery (Phase 1: file-based)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class AgentEntry(BaseModel):
    """One A2A agent's discovery metadata."""

    name: str
    description: str
    card_url: str  # /.well-known/agent.json
    capabilities: list[str] = Field(default_factory=list)
    # For specialist agents: the alert type this specialist handles (IC's delegation key).
    triggers_on: str | None = None


class AgentRegistry:
    """Lookup interface for A2A agents. Phase 1 = file-backed."""

    def __init__(self, entries: dict[str, AgentEntry]) -> None:
        self._entries = entries

    def get(self, name: str) -> AgentEntry:
        if name not in self._entries:
            raise KeyError(f"agent {name!r} not in registry")
        return self._entries[name]

    def find_by_capability(self, capability: str) -> list[AgentEntry]:
        return [e for e in self._entries.values() if capability in e.capabilities]

    def find_by_trigger(self, alert_type: str) -> AgentEntry | None:
        """Resolve a specialist agent by alert type (e.g. 'db-failure' -> dbops-agent)."""
        for entry in self._entries.values():
            if entry.triggers_on == alert_type:
                return entry
        return None

    def all(self) -> list[AgentEntry]:
        return list(self._entries.values())

    def __iter__(self):
        return iter(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)


def load_agent_registry(path: str | Path) -> AgentRegistry:
    """Load an agent registry from a YAML file."""
    data = yaml.safe_load(Path(path).read_text())
    entries = {name: AgentEntry(name=name, **spec) for name, spec in data["agents"].items()}
    return AgentRegistry(entries)
