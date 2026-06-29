"""The four typed phase outputs — incident-triage's contracts. 04-data-model §4.4.

AssessResult · RootCauseResult · RemediationResult · VerifyResult. Each points back into the graph
(node ids, paths) and carries its evidence. A different playbook declares different output schemas;
these are incident-triage's.
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import Field, field_validator

from .common import Base, Confidence
from .enums import (
    IncidentType,
    RemediationKind,
    RemediationStatus,
    RootCauseStatus,
    TimeFactorKind,
    VerifyStatus,
)


# ── Assess ─────────────────────────────────────────────────────────────
class ImpactAssessment(Base):
    scope: Optional[str] = None
    blast_radius: list[str] = Field(default_factory=list)
    bounded_by: Optional[str] = None
    severity: Optional[str] = None            # business terms (P1, …) — NO SLO/burn-rate
    urgency: Optional[str] = None
    business_impact: Optional[str] = None


class TimeFactor(Base):
    kind: TimeFactorKind                      # cron|scheduled_expiry|ttl_lapse|license_expiry
    next_at: Optional[str] = None


class SuspectedLocus(Base):
    node: str
    why: Optional[str] = None


class Suggestion(Base):
    possible_fix: str
    basis: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class AssessResult(Base):
    incident_type: IncidentType               # perf|availability|data|network|capacity|change|business|security
    symptom: str                              # the anchor for everything downstream
    affected: list[str] = Field(default_factory=list)
    impact_assessment: ImpactAssessment
    changed: list[str] = Field(default_factory=list)   # [] = checked & clean (NOT "unknown")
    time_factor: Optional[TimeFactor] = None
    suspected_locus: Optional[SuspectedLocus] = None
    related: list[str] = Field(default_factory=list)   # related incidents actively used
    cluster: Optional[str] = None             # operator-confirmed grouping (never auto)
    suggestions: list[Suggestion] = Field(default_factory=list)
    owner: Optional[str] = None


# ── Root cause ─────────────────────────────────────────────────────────
class Candidate(Base):
    cause: str
    node: Optional[str] = None
    confidence: Confidence                    # {value, basis} — basis = WHY, not a bare number
    rank: int
    path: list[str] = Field(default_factory=list)   # causal chain victim→…→cause
    evidence: list[str] = Field(default_factory=list)
    recommended_fix: Optional[str] = None


class RuledOut(Base):
    hyp: str
    evidence: Optional[str] = None


class RootCauseResult(Base):
    candidates: list[Candidate]
    selected: Optional[int] = None            # index of the candidate being acted on (operator-confirmed)
    ruled_out: list[RuledOut] = Field(default_factory=list)
    status: RootCauseStatus = RootCauseStatus.confident


# ── Remediation ────────────────────────────────────────────────────────
class Approval(Base):
    decision: str                             # approve | refine | deny
    actor: str
    at: Optional[str] = None


_REVERT_WHEN = re.compile(r"^(incident_close|problem|change|action:.+)$")


class Action(Base):
    action_id: str
    kind: RemediationKind                     # reverse | mitigate | escalate
    technique: Optional[str] = None           # mitigate: failover|reroute|throttle|scale|isolate
    target: Optional[str] = None
    team: Optional[str] = None                # escalate: the team paged (04-data-model §4.4/§8)
    expected_effect: Optional[str] = None
    blast_radius: Optional[str] = None
    rollback: Optional[str] = None
    temporary: bool = False
    revert_when: Optional[str] = None         # incident_close | action:<id> | problem | change
    idempotency_key: Optional[str] = None
    gated: bool = True
    approval: Optional[Approval] = None
    result: Optional[str] = None
    status: Optional[str] = None              # done | …

    @field_validator("revert_when")
    @classmethod
    def _check_revert_when(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _REVERT_WHEN.fullmatch(v):   # fullmatch: $ would let a trailing \n bypass
            raise ValueError(
                f"revert_when must be incident_close|problem|change|action:<id>, got {v!r}"
            )
        return v


class Followup(Base):
    detail: str
    basis: Optional[str] = None               # e.g. "escalate"
    owner: Optional[str] = None


class RemediationResult(Base):
    actions: list[Action] = Field(default_factory=list)
    followups: list[Followup] = Field(default_factory=list)   # durable fixes owned elsewhere
    status: RemediationStatus = RemediationStatus.applied


# ── Verify & close ─────────────────────────────────────────────────────
class TemporaryActionStatus(Base):
    action_id: str
    status: str                               # reverted | scheduled — must be clear before close


class Residual(Base):
    item: str
    owner: Optional[str] = None


class VerifyResult(Base):
    recovered: bool                           # confirmed from the USER's side
    before_after: Optional[str] = None        # the proof (p99 4.2s→260ms, …)
    watch_window: Optional[str] = None
    recovery_confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    resolution: Optional[str] = None          # what the next incident learns from
    temporary_actions_status: list[TemporaryActionStatus] = Field(default_factory=list)
    residual: list[Residual] = Field(default_factory=list)
    closed_by: Optional[str] = None
    status: VerifyStatus = VerifyStatus.closed


# the engine validates a phase's `output` against the schema its playbook phase declares
OUTPUT_TYPES: dict[str, type[Base]] = {
    "AssessResult": AssessResult,
    "RootCauseResult": RootCauseResult,
    "RemediationResult": RemediationResult,
    "VerifyResult": VerifyResult,
}
