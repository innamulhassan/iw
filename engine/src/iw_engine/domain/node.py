"""Node — a typed graph vertex. `props` holds STATIC/identity attributes only; anything
time-varying is a Fact (DESIGN §2.1 R-G5). `id` is derived deterministically from the
type + identity_keys (registry.node_id) so upserts are idempotent.

P6 step 2: in the GRAPH, props are backed by DECLARED-channel assertions (identity keys →
IDENTITY species, the rest → PROPERTY) — the record's `props` dict is the materialized
read view over them, rebuilt by the graph at every upsert/remap, never dual-authored.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from .enums import NodeType, Source


class Node(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    type: NodeType
    props: dict = Field(default_factory=dict)   # static/identity props only
    # P6 step 2 (P1a decision 3, groundwork from P1b's AddNode.source): WHO declared this
    # arrival's props — threaded by the reducer from AddNode.source, carried on the delta so
    # replay attributes per-prop provenance identically. None = planner/engine-authored.
    source: Source | None = None
    # P5 (DOMAIN-v3 §2.1): per-tool ids as IDENTITY SURFACE, not prop cargo — {scheme: id},
    # e.g. {"servicenow": "<sys_id>", "appd": "<app_id>", "git": "<repo>", "k8s": "<workload>"}.
    # Derived by the reducer from the identity-backbone props (graph/resolver.py); the graph
    # maintains the scheme:id → node-id index. First binding wins per scheme (write-once flavor).
    aliases: dict[str, str] = Field(default_factory=dict)
    # P5/§9.2 late alias binding: True for an entity minted from an alias credential only,
    # before its canonical identity keys are known. A provisional entity is the ONLY legal
    # Merge source (canonical entities never merge).
    provisional: bool = False
    created_by: int                             # journal seq — lineage
