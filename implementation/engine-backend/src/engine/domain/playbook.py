"""Playbook — the stored, versioned, tool-agnostic process. 04-data-model §6.1 + §1.

PK = (id, version) — every change is a new immutable row. Phases declare intents (`needs`) + an
`effect`, NEVER a tool name; the engine maps needs → capabilities from the live registry.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import Field, field_validator

from .common import Base
from .enums import PhaseEffect, PlaybookStatus


class Retry(Base):
    max: int = 3
    backoff: str = "exponential"              # TRANSIENT errors only; permanent → error_handler


class Defaults(Base):
    on_failure: str = "run-remaining"         # finish independent steps, then report
    retry: Retry = Field(default_factory=Retry)


class ErrorHandler(Base):
    action: str = "escalate"
    to: Optional[str] = None
    via: Optional[str] = None


class PhaseSpec(Base):
    """A playbook phase. Tool-agnostic: intent (`needs`) + `effect`, never a tool name."""

    id: str
    effect: PhaseEffect                       # read-only | write
    output: str                               # output schema name (AssessResult, …)
    goal: Optional[str] = None
    needs: list[str] = Field(default_factory=list)   # INTENTS, never tool names
    gate_writes: bool = False
    min_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)

    @field_validator("needs")
    @classmethod
    def _needs_are_intents(cls, v: list[str]) -> list[str]:
        # a tool name smells of `provider__action`; intents are plain kebab words (topology, traces)
        tool_names = [n for n in v if "__" in n]
        if tool_names:
            raise ValueError(f"needs must be intents, not tool names: {tool_names}")
        return v


class Playbook(Base):
    id: str
    version: str
    domain: str                               # lets ONE engine carry a library of playbooks
    status: PlaybookStatus = PlaybookStatus.active
    owner: Optional[str] = None
    body_md: Optional[str] = None             # the §1 markdown
    phases: list[PhaseSpec] = Field(default_factory=list)
    graph_schema: dict[str, Any] = Field(default_factory=dict)  # node_types/edges/facts/labels (B1 register_types)
    schemas: dict[str, Any] = Field(default_factory=dict)     # output_schemas (jsonb)
    defaults: Defaults = Field(default_factory=Defaults)
    unknown_access: str = "ask"
    error_handler: Optional[ErrorHandler] = None
    changelog: Optional[str] = None

    @property
    def pk(self) -> tuple[str, str]:
        return (self.id, self.version)
