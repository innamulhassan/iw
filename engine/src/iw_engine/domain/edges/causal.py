"""Signal / causal layer (DESIGN-INPUT §B.3) — a SEPARATE, refutable layer over the
structural spine (DESIGN §2.1 R-G8/R-G9-style separation, principle 9: dependency !=
causation). Directionality discipline: causal edges point effect -> cause.
`requires_confidence=True` marks the edges that are inferred belief claims (never a
naked float, principle 10) rather than discovered observations.

`_EVIDENCE_SOURCES` — SUPPORTS/REFUTES point some fact-bearing NodeId at a Hypothesis;
permissively, any node type except Hypothesis itself may be the subject a fact bears on.
These two are `derived=True`: the canonical evidence record is the hypothesis store's
`Hypothesis.{supporting,refuting}_facts` fact-id lists (the Fact is the ONE addressable
evidence unit — VALIDATION-VERDICT §B P0 #1). The fold recomputes these edges from those
lists so the graph view can never disagree with the hypothesis store; the planner may NOT emit them
directly (the reducer rejects a hand-authored SUPPORTS/REFUTES). The old redundant
EVIDENCE_FOR/EVIDENCE_AGAINST pair — a second node→Hypothesis link recording the same
thing — was dropped.
"""
from __future__ import annotations

from ..enums import EdgeType, NodeType, Origin
from ..spec import EdgeSpec

_EVIDENCE_SOURCES: tuple[tuple[NodeType, NodeType], ...] = tuple(
    (nt, NodeType.HYPOTHESIS) for nt in NodeType if nt != NodeType.HYPOTHESIS
)

SPECS: tuple[EdgeSpec, ...] = (
    EdgeSpec(
        type=EdgeType.FIRED_ON,
        allowed=(
            (NodeType.ALERT, NodeType.SERVICE),
            (NodeType.ALERT, NodeType.API_ENDPOINT),
            (NodeType.ALERT, NodeType.HOST),
            (NodeType.ALERT, NodeType.DATABASE),
            (NodeType.ALERT, NodeType.MESSAGE_QUEUE),
            (NodeType.ALERT, NodeType.CACHE),
            (NodeType.ALERT, NodeType.POD),
            (NodeType.ALERT, NodeType.NETWORK_SEGMENT),
            (NodeType.ALERT, NodeType.LOAD_BALANCER),
            (NodeType.ALERT, NodeType.PROXY),
            (NodeType.ALERT, NodeType.API_GATEWAY),
            (NodeType.ALERT, NodeType.CDN),
            (NodeType.ALERT, NodeType.WAF),
            (NodeType.ALERT, NodeType.DNS),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.EMITTED,
        allowed=(
            (NodeType.SERVICE, NodeType.ERROR_SIGNATURE),
            (NodeType.POD, NodeType.ERROR_SIGNATURE),
            (NodeType.COMPONENT, NodeType.ERROR_SIGNATURE),
            (NodeType.CONTAINER, NodeType.ERROR_SIGNATURE),
            (NodeType.API_ENDPOINT, NodeType.ERROR_SIGNATURE),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.AFFECTS,
        allowed=(
            (NodeType.ANOMALY, NodeType.SERVICE),
            (NodeType.ANOMALY, NodeType.API_ENDPOINT),
            (NodeType.ANOMALY, NodeType.DATABASE),
            (NodeType.ANOMALY, NodeType.HOST),
            (NodeType.ANOMALY, NodeType.BUSINESS_TRANSACTION),
            (NodeType.INCIDENT, NodeType.SERVICE),
            (NodeType.INCIDENT, NodeType.API_ENDPOINT),
            (NodeType.INCIDENT, NodeType.GENERIC_CI),
            (NodeType.ALERT, NodeType.SERVICE),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.TRIGGERED_BY,
        allowed=(
            (NodeType.INCIDENT, NodeType.ALERT),
            (NodeType.ANOMALY, NodeType.ALERT),
            (NodeType.INCIDENT, NodeType.ANOMALY),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.IMPACTS,
        allowed=(
            (NodeType.CHANGE_EVENT, NodeType.SERVICE),
            (NodeType.CHANGE_EVENT, NodeType.DATABASE),
            (NodeType.CHANGE_EVENT, NodeType.API_ENDPOINT),
            (NodeType.HYPOTHESIS, NodeType.SERVICE),
            (NodeType.ANOMALY, NodeType.BUSINESS_TRANSACTION),
        ),
        default_origin=Origin.INFERRED,
        requires_confidence=True,
    ),
    EdgeSpec(
        type=EdgeType.CHANGED_BY,
        allowed=(
            (NodeType.SERVICE, NodeType.CHANGE_EVENT),
            (NodeType.DATABASE, NodeType.CHANGE_EVENT),
            (NodeType.HOST, NodeType.CHANGE_EVENT),
            (NodeType.DEPLOYMENT, NodeType.CHANGE_EVENT),
            (NodeType.FIREWALL_RULE, NodeType.CHANGE_EVENT),
            (NodeType.NETWORK_SEGMENT, NodeType.CHANGE_EVENT),
            (NodeType.CONFIG_ITEM, NodeType.CHANGE_EVENT),
            (NodeType.GENERIC_CI, NodeType.CHANGE_EVENT),
            # L4 edge/security path devices + pre-existing fix (load_balancer/dns/route
            # were not legal change targets — a config push to any of these is a change).
            (NodeType.PROXY, NodeType.CHANGE_EVENT),
            (NodeType.API_GATEWAY, NodeType.CHANGE_EVENT),
            (NodeType.CDN, NodeType.CHANGE_EVENT),
            (NodeType.WAF, NodeType.CHANGE_EVENT),
            (NodeType.LOAD_BALANCER, NodeType.CHANGE_EVENT),
            (NodeType.DNS, NodeType.CHANGE_EVENT),
            (NodeType.ROUTE, NodeType.CHANGE_EVENT),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.CORRELATED_WITH,
        allowed=(
            (NodeType.ANOMALY, NodeType.CHANGE_EVENT),
            (NodeType.ANOMALY, NodeType.ALERT),
            (NodeType.ANOMALY, NodeType.ANOMALY),
            (NodeType.SERVICE, NodeType.SERVICE),
            (NodeType.HYPOTHESIS, NodeType.HYPOTHESIS),
        ),
        default_origin=Origin.INFERRED,
        requires_confidence=True,
    ),
    EdgeSpec(
        type=EdgeType.SIMILAR_TO,
        allowed=((NodeType.INCIDENT, NodeType.INCIDENT),),
        default_origin=Origin.INFERRED,
        requires_confidence=True,
    ),
    EdgeSpec(
        type=EdgeType.RECURRENCE_OF,
        allowed=((NodeType.INCIDENT, NodeType.INCIDENT),),
        default_origin=Origin.INFERRED,
        requires_confidence=True,
    ),
    EdgeSpec(
        type=EdgeType.CAUSED_BY,
        allowed=(
            (NodeType.HYPOTHESIS, NodeType.CHANGE_EVENT),
            (NodeType.HYPOTHESIS, NodeType.CODE_COMMIT),
            (NodeType.HYPOTHESIS, NodeType.CERTIFICATE),
            (NodeType.HYPOTHESIS, NodeType.FEATURE_FLAG),
            (NodeType.HYPOTHESIS, NodeType.FIREWALL_RULE),
            (NodeType.HYPOTHESIS, NodeType.HOST),
            (NodeType.HYPOTHESIS, NodeType.DATABASE),
            (NodeType.HYPOTHESIS, NodeType.EXTERNAL_SERVICE),
            (NodeType.HYPOTHESIS, NodeType.NETWORK_SEGMENT),
            (NodeType.HYPOTHESIS, NodeType.BATCH_JOB),
            (NodeType.HYPOTHESIS, NodeType.ANOMALY),
            (NodeType.ERROR_SIGNATURE, NodeType.CODE_COMMIT),
            (NodeType.ANOMALY, NodeType.CHANGE_EVENT),
            (NodeType.ANOMALY, NodeType.HOST),
            (NodeType.ANOMALY, NodeType.DATABASE),
            (NodeType.ANOMALY, NodeType.FIREWALL_RULE),
            (NodeType.ANOMALY, NodeType.CODE_COMMIT),
            (NodeType.ANOMALY, NodeType.NETWORK_SEGMENT),
            (NodeType.ANOMALY, NodeType.EXTERNAL_SERVICE),
            (NodeType.ANOMALY, NodeType.CERTIFICATE),
            (NodeType.ANOMALY, NodeType.FEATURE_FLAG),
            (NodeType.ANOMALY, NodeType.BATCH_JOB),
            # L4 edge/security path devices as nameable root causes + pre-existing fix
            # (load_balancer/dns/route: an LB health-check fail / DNS misconfig / bad
            # route can now be named a root cause, not just correlated).
            (NodeType.HYPOTHESIS, NodeType.PROXY),
            (NodeType.HYPOTHESIS, NodeType.API_GATEWAY),
            (NodeType.HYPOTHESIS, NodeType.CDN),
            (NodeType.HYPOTHESIS, NodeType.WAF),
            (NodeType.HYPOTHESIS, NodeType.LOAD_BALANCER),
            (NodeType.HYPOTHESIS, NodeType.DNS),
            (NodeType.HYPOTHESIS, NodeType.ROUTE),
            (NodeType.ANOMALY, NodeType.PROXY),
            (NodeType.ANOMALY, NodeType.API_GATEWAY),
            (NodeType.ANOMALY, NodeType.CDN),
            (NodeType.ANOMALY, NodeType.WAF),
            (NodeType.ANOMALY, NodeType.LOAD_BALANCER),
            (NodeType.ANOMALY, NodeType.DNS),
            (NodeType.ANOMALY, NodeType.ROUTE),
            # P3 TYPE AIRLOCK (DOMAIN-v3 §2.4 row 2): an unknown CI is finally BLAMABLE — a
            # generic_ci can be named the cause instead of being unblamable-but-remediable.
            # The reducer marks these provisional with a confidence penalty; promotion of the
            # CI to a real type (the RETYPE op) is a later phase.
            (NodeType.HYPOTHESIS, NodeType.GENERIC_CI),
            (NodeType.ANOMALY, NodeType.GENERIC_CI),
        ),
        default_origin=Origin.INFERRED,
        requires_confidence=True,
    ),
    EdgeSpec(
        type=EdgeType.SUPPORTS,
        allowed=_EVIDENCE_SOURCES,
        default_origin=Origin.INFERRED,
        requires_confidence=True,
        derived=True,
    ),
    EdgeSpec(
        type=EdgeType.REFUTES,
        allowed=_EVIDENCE_SOURCES,
        default_origin=Origin.INFERRED,
        requires_confidence=True,
        derived=True,
    ),
    EdgeSpec(
        type=EdgeType.REMEDIATED_BY,
        allowed=(
            (NodeType.HYPOTHESIS, NodeType.CHANGE_EVENT),
            (NodeType.HYPOTHESIS, NodeType.RELEASE),
            (NodeType.HYPOTHESIS, NodeType.CONFIG_ITEM),
            (NodeType.HYPOTHESIS, NodeType.FEATURE_FLAG),
            (NodeType.HYPOTHESIS, NodeType.FIREWALL_RULE),
            (NodeType.HYPOTHESIS, NodeType.GENERIC_CI),
        ),
        default_origin=Origin.DECLARED,
        requires_confidence=False,
    ),
)
