"""Node — a typed graph vertex. `props` holds STATIC/identity attributes only; anything
time-varying is a Fact (DESIGN §2.1 R-G5). `id` is derived deterministically from the
type + identity_keys (registry.node_id) so upserts are idempotent.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .enums import NodeType


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: NodeType
    props: dict = Field(default_factory=dict)   # static/identity props only
    created_by: int                             # journal seq — lineage
