"""Closed vocabularies — the enums the whole engine (and the LLM) classifies into.

Principle 4 (closed vocabulary + one escape hatch): the LLM picks a member of these
enums; it never mints a label. `NodeType.GENERIC_CI` is the single escape hatch.
These enums are the source of truth from which the registry, the LLM's JSON schema,
and the system-prompt "allowed types" section are all derived — so they cannot drift.
"""
from __future__ import annotations

from enum import StrEnum  # py311+ built-in: members compare + serialise as their str value


# ── Provenance / trust ────────────────────────────────────────────────────────
class Source(StrEnum):
    PROMETHEUS = "prometheus"
    SPLUNK = "splunk"
    APPD = "appd"
    SERVICENOW = "servicenow"
    CMDB = "cmdb"
    OCP = "ocp"
    ARTIFACTORY = "artifactory"
    GIT = "git"
    BIGPANDA = "bigpanda"     # event aggregation / AIOps correlation (BigPanda, Moogsoft, ...)
    LLM = "llm"
    HUMAN = "human"
    ENGINE = "engine"


class Origin(StrEnum):
    """Why an edge exists — governs how much to trust it (DESIGN §2.1 R-G8)."""

    DECLARED = "declared"      # CMDB / IaC — treated as truth (the structural spine)
    DISCOVERED = "discovered"  # telemetry — an observation, time-boxed
    INFERRED = "inferred"      # LLM / causal — a hypothesis, confidence + evidence mandatory


class Effect(StrEnum):
    READ = "read"
    WRITE = "write"


class Binding(StrEnum):
    """How a capability's live fetch is transported (VALIDATION-VERDICT §C). A per-adapter
    DATA field, not a code fork: the market now ships first-party MCP servers for most tools,
    so `MCP` is the default (one generic `tools/call`); `REST` is the fallback for the two
    without one (Prometheus + local git); `A2A` is reserved for delegating a whole remediation
    sub-task to a vendor's agent (write-side, later). The mock test transport ignores this; the
    live routed transport dispatches on it."""

    MCP = "mcp"    # default — a first-party MCP server (one generic tools/call)
    REST = "rest"  # fallback — a thin raw-REST shim (Prometheus, local git)
    A2A = "a2a"    # reserved — agent-to-agent remediation delegation (write-side, later)


class ConfidenceLevel(StrEnum):
    """Rubric the LLM emits (DESIGN §2.3 R-C4) — kills '0.9 everywhere'."""

    LOW = "low"
    MED = "med"
    HIGH = "high"


class Species(StrEnum):
    """The five temporal species of an Assertion (DOMAIN-v3 §2.2). One provenance envelope,
    differing only on the time axis: what IS this (identity), what do we know ABOUT it
    (descriptor), what is TRUE over time (state), what did we MEASURE (reading), what HAPPENED
    (event). The atom that collapses today's prop/fact/event trichotomy into one record."""

    IDENTITY = "identity"
    DESCRIPTOR = "descriptor"
    STATE = "state"
    READING = "reading"
    EVENT = "event"


class Channel(StrEnum):
    """The belief channel an Assertion's trust is keyed on (DOMAIN-v3 §2.2) — belief keyed on
    channel, NOT Source identity (moves off the Source.LLM==inferred special-case). INFERRED
    carries a confidence; MEASURED/DECLARED/ENGINE carry a source_reliability."""

    MEASURED = "measured"    # a tool/human directly observed it
    INFERRED = "inferred"    # the model reasoned it into being (confidence)
    DECLARED = "declared"    # CMDB / IaC asserted it as truth
    ENGINE = "engine"        # the engine authored it (no_evidence, hypothesis statements)


class Stat(StrEnum):
    """The statistic a READING carries (DOMAIN-v3 §9.1, widened vocabulary). A reading's number
    is a judgment-granularity measurement qualified by its stat + window — ending the
    `red_rate` rpm-vs-baseline ambiguity by making window/stat FIELDS, not name-encoded."""

    GAUGE = "gauge"
    RATE = "rate"
    RATIO = "ratio"
    COUNT = "count"
    COUNTER = "counter"                       # lifetime monotonic
    PERCENTILE = "percentile"
    DISTRIBUTION = "distribution"
    DELTA_VS_BASELINE = "delta_vs_baseline"
    TIMESTAMP = "timestamp"


class FactState(StrEnum):
    """The shared lifecycle vocabulary for every retractable storage shape — Fact AND, since
    the P0 lifecycle fix, Edge and Event (VALIDATION-VERDICT §B P0 #2). One enum, not three:
    a refuted CAUSED_BY edge and a wrong telemetry Event tombstone exactly as a Fact does.
    (SUPERSEDED is meaningful for facts/edges with a valid-time window; events, being
    point-in-time occurrences, only ever go ACTIVE → RETRACTED.)"""

    ACTIVE = "active"
    SUPERSEDED = "superseded"   # a newer value closed this fact's/edge's valid_to
    RETRACTED = "retracted"     # the observation was WRONG (tombstone) — DESIGN §2.4 R-J3


# ── Phases + control ──────────────────────────────────────────────────────────
class Phase(StrEnum):
    FRAME = "frame"
    TRIAGE = "triage"
    HYPOTHESIZE = "hypothesize"
    INVESTIGATE = "investigate"
    REMEDIATE = "remediate"
    VERIFY = "verify"
    CLOSE = "close"


class VerdictStatus(StrEnum):
    ADVANCE = "advance"
    REPEAT = "repeat"
    BACKTRACK = "backtrack"
    BLOCKED = "blocked"
    DONE = "done"


class GateResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class CloseOutcome(StrEnum):
    """DESIGN §2.2 R-P4 — an incident closes resolved OR merely mitigated."""

    RESOLVED = "resolved"    # confirmed hypothesis + verified fix
    MITIGATED = "mitigated"  # impact stopped, no confirmed root cause


class HypothesisStatus(StrEnum):
    PROPOSED = "proposed"
    INVESTIGATING = "investigating"
    SUPPORTED = "supported"
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    SUPERSEDED = "superseded"


class ChainRole(StrEnum):
    CAUSE = "cause"
    CONDITION = "condition"
    EFFECT = "effect"


class ChainLinkKind(StrEnum):
    EVENT = "event"
    FACT = "fact"
    CHANGE = "change"


# ── Node catalog (closed, tiered — DESIGN §2.1 / §3) ──────────────────────────
class NodeType(StrEnum):
    # L0 logical / business
    APPLICATION = "application"
    SERVICE = "service"
    COMPONENT = "component"
    API_ENDPOINT = "api_endpoint"
    TEAM = "team"
    # L1 workload / runtime
    DEPLOYMENT = "deployment"
    REPLICASET = "replicaset"
    POD = "pod"
    CONTAINER = "container"
    PROCESS = "process"
    BATCH_JOB = "batch_job"
    # L2 platform / orchestration
    NAMESPACE = "namespace"
    CLUSTER = "cluster"
    HOST = "host"
    CONFIG_ITEM = "config_item"
    # L3 data & messaging
    DATABASE = "database"
    SCHEMA = "schema"
    MESSAGE_QUEUE = "message_queue"
    CACHE = "cache"
    # L4 network / edge
    LOAD_BALANCER = "load_balancer"
    ROUTE = "route"
    NETWORK_SEGMENT = "network_segment"
    FIREWALL_RULE = "firewall_rule"
    DNS = "dns"
    PROXY = "proxy"
    API_GATEWAY = "api_gateway"
    CDN = "cdn"
    WAF = "waf"
    # L5 change & supply chain
    CODE_COMMIT = "code_commit"
    BUILD_ARTIFACT = "build_artifact"
    RELEASE = "release"
    CHANGE_EVENT = "change_event"
    PULL_REQUEST = "pull_request"
    # change-adjacent, not-in-CMDB (DESIGN §2.1 R-G6)
    CERTIFICATE = "certificate"
    FEATURE_FLAG = "feature_flag"
    EXTERNAL_SERVICE = "external_service"
    # L6 signals / investigation
    ALERT = "alert"
    INCIDENT = "incident"
    ANOMALY = "anomaly"                    # the canonical SYMPTOM node (R-G4)
    ERROR_SIGNATURE = "error_signature"
    BUSINESS_TRANSACTION = "business_transaction"
    HYPOTHESIS = "hypothesis"
    # escape hatch (mirrors ServiceNow base cmdb_ci)
    GENERIC_CI = "generic_ci"


# ── Edge catalog (closed — DESIGN §2.1 R-G1/R-G8) ─────────────────────────────
class EdgeType(StrEnum):
    # structural spine (dependent -> provider)
    DEPENDS_ON = "depends_on"
    CALLS = "calls"
    REALIZES = "realizes"
    INSTANCE_OF = "instance_of"
    RUNS_ON = "runs_on"
    HOSTED_ON = "hosted_on"
    DEPLOYED_TO = "deployed_to"
    CONTAINS = "contains"
    MEMBER_OF = "member_of"
    EXPOSES = "exposes"
    ROUTES_TO = "routes_to"
    CONNECTS_TO = "connects_to"
    READS_FROM = "reads_from"
    WRITES_TO = "writes_to"
    PRODUCES_TO = "produces_to"
    CONSUMES_FROM = "consumes_from"
    SECURED_BY = "secured_by"
    # ownership / supply-chain
    OWNS = "owns"
    BUILT_FROM = "built_from"
    RELEASED_AS = "released_as"
    RUNS_VERSION = "runs_version"
    DEPLOYED_AS = "deployed_as"
    INTRODUCED_BY = "introduced_by"
    # signal / causal (a separate, refutable layer)
    FIRED_ON = "fired_on"
    EMITTED = "emitted"
    AFFECTS = "affects"
    TRIGGERED_BY = "triggered_by"
    IMPACTS = "impacts"
    CHANGED_BY = "changed_by"
    CORRELATED_WITH = "correlated_with"
    # related-incident layer (Incident -> Incident): co-firing/similar priors that seed a
    # hypothesis ("3 other apps reported the same at the same time"), and true recurrences.
    SIMILAR_TO = "similar_to"
    RECURRENCE_OF = "recurrence_of"
    CAUSED_BY = "caused_by"
    # evidence layer — DERIVED projections of the canonical Hypothesis.{supporting,refuting}_facts
    # fact-id lists (VALIDATION-VERDICT §B P0 #1). The Fact is the ONE addressable evidence unit;
    # these edges are recomputed by the fold, never emitted by the planner. EVIDENCE_FOR/AGAINST
    # (a redundant second pair pointing the same node→hypothesis) were dropped.
    SUPPORTS = "supports"
    REFUTES = "refutes"
    REMEDIATED_BY = "remediated_by"


# ── Operation kinds (the LLM's only output channel — DESIGN §2.1/§2.2) ─────────
class OpKind(StrEnum):
    ADD_NODE = "add_node"
    ADD_FACT = "add_fact"
    ADD_EVENT = "add_event"
    ADD_EDGE = "add_edge"
    PROPOSE_HYPOTHESIS = "propose_hypothesis"
    UPDATE_HYPOTHESIS = "update_hypothesis"
    NO_EVIDENCE = "no_evidence"   # honest null-result sentinel (R-P2)
