"""L3 — data & messaging tier (DESIGN §3 / DESIGN-INPUT §B.2).

Database (instance-level) / Schema (sub-object) / MessageQueue / Cache. USE predicates
(conn-pool, replication-lag, consumer-lag, hit-rate) bind to these per DESIGN-INPUT
§B.2's RED-vs-USE registry-enforced split.
"""
from __future__ import annotations

from ..enums import NodeType
from ..spec import NodeSpec

SPECS: tuple[NodeSpec, ...] = (
    NodeSpec(
        type=NodeType.DATABASE,
        tier="L3",
        identity_keys=("db_id",),
        static_props=("db_id", "engine", "ha_role", "endpoint"),
        fact_predicates=(
            "conn_pool_util",
            "active_connections",
            "max_connections",
            "replication_lag",
            "slow_query_rate",
        ),
        event_types=("failover", "connection_storm", "deadlock_spike", "migration_applied"),
        discriminator=(
            "A database instance/cluster — instance-level facts (pool, replication, "
            "failover) attach here. Use Schema when a change/fact targets a specific "
            "table/schema rather than the whole instance."
        ),
    ),
    NodeSpec(
        type=NodeType.SCHEMA,
        tier="L3",
        identity_keys=("db_id", "schema_name"),
        static_props=("schema_name", "db_id"),
        fact_predicates=("table_count", "index_health"),
        event_types=("migration_applied", "index_dropped"),
        discriminator=(
            "A named schema/table-group inside a Database — used when a change "
            "(migration, dropped index) targets that specific schema, e.g. the "
            "database scenario's 'migration dropped index -> full-scans'."
        ),
    ),
    NodeSpec(
        type=NodeType.MESSAGE_QUEUE,
        tier="L3",
        identity_keys=("topic_id",),
        static_props=("topic_id", "broker", "partitions"),
        fact_predicates=("consumer_lag", "dlq_depth", "throughput"),
        event_types=("rebalance", "partition_offline"),
        discriminator=(
            "An async messaging topic/queue. Services PRODUCES_TO/CONSUMES_FROM it "
            "rather than CALLS it directly."
        ),
    ),
    NodeSpec(
        type=NodeType.CACHE,
        tier="L3",
        identity_keys=("cache_id",),
        static_props=("cache_id", "engine"),
        fact_predicates=("hit_rate", "eviction_rate", "mem_utilization"),
        event_types=("failover", "flushed"),
        discriminator=(
            "An in-memory cache/store (Redis/Memcached-like) a Service reads/writes for "
            "low-latency lookups; distinguishes from Database by lacking durability "
            "guarantees."
        ),
    ),
)
