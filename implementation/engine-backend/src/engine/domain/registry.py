"""The capability registry — how intents become governed calls. 04-data-model §6.2.

Three rows: Provider (a source) · DeclaredCapability (what it exposes) · CapabilityPolicy (what the
agent may do with it). A NEW capability lands `pending_review` + `deny` until a human reviews it.
"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import Field

from .common import Base
from .enums import Access, Effect, PolicyStatus, ProviderKind, ProviderStatus


class Provider(Base):
    id: str
    name: Optional[str] = None
    kind: ProviderKind                        # skill|mcp_local|mcp_remote|a2a_agent|api
    connection: dict[str, Any] = Field(default_factory=dict)   # varies by kind (jsonb)
    trusted: bool = False
    status: ProviderStatus = ProviderStatus.registered
    last_synced: Optional[str] = None


class DeclaredCapability(Base):
    id: str                                   # provider__action
    provider: str
    description: Optional[str] = None
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    effect_hint: Effect = Effect.unknown      # read | write | unknown
    intents: list[str] = Field(default_factory=list)   # which playbook `needs` it satisfies (03-design C2/C4)


class CapabilityPolicy(Base):
    capability_id: str
    effect: Effect                            # read | write | unknown
    access: Access                            # allow | ask | deny ← what the agent may do
    status: PolicyStatus = PolicyStatus.pending_review   # a NEW capability lands here first
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[str] = None
