"""NodeSpec / EdgeSpec — the declarative catalog the LLM classifies into and the reducer
validates against. This IS the owner's "folder of graph entity classes": each node/edge
type is a spec (identity, allowed props/facts/events/edges, a discriminator rule) rather
than a bespoke Python subclass — one source of truth for both the LLM's allowed-types
schema and host-side validation, so they can never drift.
"""
from __future__ import annotations

from dataclasses import dataclass

from .enums import EdgeType, NodeType, Origin


@dataclass(frozen=True)
class NodeSpec:
    type: NodeType
    tier: str                                  # "L0".."L6" | "signal"
    identity_keys: tuple[str, ...]             # props that form the upsert identity
    static_props: tuple[str, ...] = ()         # allowed IMMUTABLE/identity props (time-varying → Fact)
    fact_predicates: tuple[str, ...] = ()      # allowed fact predicates on this node
    event_types: tuple[str, ...] = ()          # allowed event types
    discriminator: str = ""                    # rule to pick this type vs siblings (LLM-facing)


@dataclass(frozen=True)
class EdgeSpec:
    type: EdgeType
    allowed: tuple[tuple[NodeType, NodeType], ...]   # legal (src_type, dst_type) pairs
    default_origin: Origin = Origin.DISCOVERED
    requires_confidence: bool = False          # inferred/causal edges must carry Confidence + evidence
    derived: bool = False                      # a projection the fold recomputes — the planner may NOT
                                               # emit it directly (evidence edges: VALIDATION-VERDICT §B P0 #1)
    fact_predicates: tuple[str, ...] = ()      # allowed fact predicates ON THE EDGE (edge-borne RED on
                                               # a discovered CALLS/READS_FROM/…; governed like node facts §C2)
