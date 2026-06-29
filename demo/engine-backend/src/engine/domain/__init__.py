"""The data model (P1). Pydantic v2 shapes faithful to ../../design/v2/04-data-model.html.

Import surface: `from engine.domain import Node, AssessResult, Playbook, ...`.
"""
from __future__ import annotations

from .common import Base, Confidence
from .enums import (
    Access,
    Effect,
    FeedbackKind,
    ImpactState,
    IncidentType,
    Layer,
    NodeKind,
    PhaseEffect,
    PhaseState,
    PlaybookStatus,
    PolicyStatus,
    ProviderKind,
    ProviderStatus,
    RemediationKind,
    RemediationStatus,
    RootCauseStatus,
    StepKind,
    TimeFactorKind,
    VerifyStatus,
)
from .feedback import Feedback
from .graph import Edge, Fact, Node
from .outputs import (
    OUTPUT_TYPES,
    Action,
    Approval,
    AssessResult,
    Candidate,
    Followup,
    ImpactAssessment,
    Residual,
    RemediationResult,
    RootCauseResult,
    RuledOut,
    Suggestion,
    SuspectedLocus,
    TemporaryActionStatus,
    TimeFactor,
    VerifyResult,
)
from .phase import PhaseRecord, Step
from .playbook import Defaults, ErrorHandler, PhaseSpec, Playbook, Retry
from .registry import CapabilityPolicy, DeclaredCapability, Provider
from .subject import SubjectRef

__all__ = [
    # base
    "Base", "Confidence",
    # subject
    "SubjectRef",
    # graph
    "Node", "Fact", "Edge",
    # phase
    "PhaseRecord", "Step",
    # outputs
    "AssessResult", "ImpactAssessment", "TimeFactor", "SuspectedLocus", "Suggestion",
    "RootCauseResult", "Candidate", "RuledOut",
    "RemediationResult", "Action", "Approval", "Followup",
    "VerifyResult", "TemporaryActionStatus", "Residual",
    "OUTPUT_TYPES",
    # feedback
    "Feedback",
    # playbook
    "Playbook", "PhaseSpec", "Defaults", "Retry", "ErrorHandler",
    # registry
    "Provider", "DeclaredCapability", "CapabilityPolicy",
    # enums
    "Access", "Effect", "PhaseEffect", "ImpactState", "NodeKind", "Layer",
    "PhaseState", "StepKind", "IncidentType", "TimeFactorKind", "RootCauseStatus",
    "RemediationKind", "RemediationStatus", "VerifyStatus", "FeedbackKind",
    "ProviderKind", "ProviderStatus", "PolicyStatus", "PlaybookStatus",
]
