"""Enumerations — the controlled value-spaces from 04-data-model.

Node `kind`/`layer`, fact `impact_state`, edge/step kinds, the phase outputs' status + type enums,
governance access/effect, and the registry kinds. Incident-triage's value-space is modelled here;
a different playbook supplies its own node_types/edges/facts at load time (domain-neutral engine).
"""
from __future__ import annotations

from enum import Enum


# ── governance ─────────────────────────────────────────────────────────
class Access(str, Enum):
    """What the agent may do with a capability. 04-data-model §6.2 (CapabilityPolicy.access)."""

    allow = "allow"
    ask = "ask"
    deny = "deny"


class Effect(str, Enum):
    """A capability's side-effect class. DeclaredCapability.effect_hint / CapabilityPolicy.effect."""

    read = "read"
    write = "write"
    unknown = "unknown"


class PhaseEffect(str, Enum):
    """A playbook phase's effect bound. Note the vocabulary differs from capability Effect."""

    read_only = "read-only"
    write = "write"


# ── graph ──────────────────────────────────────────────────────────────
class ImpactState(str, Enum):
    """How a node is impacted — SEPARATE from health. 04-data-model §3.3 (Fact.impact_state)."""

    erroring = "erroring"
    degraded = "degraded"
    stalled = "stalled"
    lagging = "lagging"
    wrong_data = "wrong-data"
    ok = "ok"


class NodeKind(str, Enum):
    """Incident-triage's node kinds. 04-data-model §3.1/§3.2 (a different playbook supplies others)."""

    system = "system"
    incident = "incident"
    change = "change"
    alert = "alert"


class Layer(str, Enum):
    """A `system` node's layer (system nodes only). 04-data-model §3.1."""

    business = "business"
    app = "app"
    database = "database"
    network = "network"
    storage = "storage"
    compute = "compute"
    location = "location"
    external = "external"


# ── phase record / step ────────────────────────────────────────────────
class PhaseState(str, Enum):
    """PhaseRecord.state. 04-data-model §4.1."""

    active = "active"
    waiting_input = "waiting_input"
    waiting_approval = "waiting_approval"
    blocked = "blocked"
    done = "done"
    failed = "failed"


class StepKind(str, Enum):
    """Step.kind. 04-data-model §4.2."""

    tool_call = "tool_call"
    reasoning = "reasoning"
    suggestion = "suggestion"
    decision = "decision"
    user_input = "user_input"


# ── phase outputs ──────────────────────────────────────────────────────
class IncidentType(str, Enum):
    """AssessResult.incident_type. 04-data-model §4.4."""

    performance = "performance"
    availability = "availability"
    data = "data"
    network = "network"
    capacity = "capacity"
    change = "change"
    business = "business"
    security = "security"


class TimeFactorKind(str, Enum):
    """AssessResult.time_factor.kind — a time-triggered contributor. 04-data-model §4.4."""

    cron = "cron"
    scheduled_expiry = "scheduled_expiry"
    ttl_lapse = "ttl_lapse"
    license_expiry = "license_expiry"


class RootCauseStatus(str, Enum):
    confident = "confident"
    needs_input = "needs-input"
    escalated = "escalated"


class RemediationKind(str, Enum):
    """Action.kind. 04-data-model §4.4."""

    reverse = "reverse"
    mitigate = "mitigate"
    escalate = "escalate"


class RemediationStatus(str, Enum):
    applied = "applied"
    partial = "partial"
    failed = "failed"


class VerifyStatus(str, Enum):
    closed = "closed"
    held = "held"
    reopened = "reopened"


# ── feedback ───────────────────────────────────────────────────────────
class FeedbackKind(str, Enum):
    """Feedback.kind. 04-data-model §5."""

    outcome = "outcome"      # did it work
    failure = "failure"      # the engine was wrong
    correction = "correction"  # the right answer


# ── registry ───────────────────────────────────────────────────────────
class ProviderKind(str, Enum):
    """How a source binds. 04-data-model §6.2 (Provider.kind)."""

    skill = "skill"
    mcp_local = "mcp_local"
    mcp_remote = "mcp_remote"
    a2a_agent = "a2a_agent"
    api = "api"


class ProviderStatus(str, Enum):
    registered = "registered"
    connected = "connected"
    disabled = "disabled"
    error = "error"


class PolicyStatus(str, Enum):
    """A NEW capability lands `pending_review`. 04-data-model §6.2 (CapabilityPolicy.status)."""

    active = "active"
    pending_review = "pending_review"


# ── playbook ───────────────────────────────────────────────────────────
class PlaybookStatus(str, Enum):
    draft = "draft"
    active = "active"
    deprecated = "deprecated"
