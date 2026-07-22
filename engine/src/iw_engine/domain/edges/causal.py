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
        symmetric=False,
        requires_confidence=False,
        semantics=(
            "Which entity a monitoring Alert was raised against (Prometheus "
            "active_alerts / AppD healthrule_violations)."
        ),
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
        symmetric=False,
        requires_confidence=False,
        semantics=(
            "The entity that produced a deduplicated ErrorSignature (Splunk "
            "search_errors/error_signature_topk) — added per DESIGN §2.1 R-G1 to "
            "close a capability-fold registry gap."
        ),
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
        symmetric=False,
        requires_confidence=False,
        semantics=(
            "The Anomaly's impact target — FRAME's canonical output edge (R-G4): "
            "'the whole method operates over the subgraph reachable from the "
            "Anomaly.' Also used for Incident->CI (ServiceNow) and Alert->Service."
        ),
    ),
    EdgeSpec(
        type=EdgeType.TRIGGERED_BY,
        allowed=(
            (NodeType.INCIDENT, NodeType.ALERT),
            (NodeType.ANOMALY, NodeType.ALERT),
            (NodeType.INCIDENT, NodeType.ANOMALY),
        ),
        default_origin=Origin.DISCOVERED,
        symmetric=False,
        requires_confidence=False,
        semantics=(
            "Which upstream signal kicked off this Incident/Anomaly — a discovered "
            "temporal link, weaker than CAUSED_BY (no confidence/evidence mandatory)."
        ),
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
        symmetric=False,
        requires_confidence=True,
        semantics=(
            "An INFERRED impact claim (distinct from the discovered AFFECTS) — e.g. "
            "'this ChangeEvent impacts this Service's error rate' — carrying "
            "confidence + evidence."
        ),
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
        symmetric=False,
        requires_confidence=False,
        semantics=(
            "Joins a CI to the ChangeEvent that altered it within the incident "
            "window — the RCA workhorse edge change-analysis keys off (DESIGN-INPUT "
            "§B.3)."
        ),
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
        symmetric=True,
        requires_confidence=True,
        semantics=(
            "Symmetric, weaker-than-causal statistical/temporal correlation carrying "
            "a correlation strength — no claim of direction."
        ),
    ),
    EdgeSpec(
        type=EdgeType.SIMILAR_TO,
        allowed=((NodeType.INCIDENT, NodeType.INCIDENT),),
        default_origin=Origin.INFERRED,
        symmetric=True,
        requires_confidence=True,
        semantics=(
            "Incident -> a co-firing / similar prior Incident (ServiceNow "
            "list_related_incidents). A related prior is a HYPOTHESIS PRIOR — 'N other "
            "apps reported the same symptom in the same window' — so it carries "
            "confidence + evidence like any inferred belief, never a naked link."
        ),
    ),
    EdgeSpec(
        type=EdgeType.RECURRENCE_OF,
        allowed=((NodeType.INCIDENT, NodeType.INCIDENT),),
        default_origin=Origin.INFERRED,
        symmetric=False,
        requires_confidence=True,
        semantics=(
            "Incident -> an earlier Incident this one RECURS (same CI + same signature "
            "reappearing). Directional (current -> prior); stronger than SIMILAR_TO — a "
            "known-recurrence prior sharpens the leading hypothesis toward the prior's "
            "confirmed root cause. Carries confidence + evidence."
        ),
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
        ),
        default_origin=Origin.INFERRED,
        symmetric=False,
        requires_confidence=True,
        semantics=(
            "Effect -> cause, the core causal-chain edge; always carries confidence + "
            "evidence and is scoped to a Hypothesis's status (DESIGN §2.1 R-G8). "
            "Covers all 6 scenario root-cause classes: code (CodeCommit), deployment "
            "(ChangeEvent), network (NetworkSegment), database (Database), firewall "
            "(FirewallRule), and no-change (Host/BatchJob saturation), plus the L4 "
            "edge/security path devices (LoadBalancer/Proxy/ApiGateway/Cdn/Waf/Dns/Route)."
        ),
    ),
    EdgeSpec(
        type=EdgeType.SUPPORTS,
        allowed=_EVIDENCE_SOURCES,
        default_origin=Origin.INFERRED,
        symmetric=False,
        requires_confidence=True,
        derived=True,
        semantics=(
            "Fact(subject) -> Hypothesis: the subject of a fact in the hypothesis's "
            "supporting_facts list. DERIVED by the fold from that canonical fact-id "
            "list (VALIDATION-VERDICT §B P0 #1) — a graph-side projection, never "
            "planner-emitted; the precise facts live on the hypothesis store record."
        ),
    ),
    EdgeSpec(
        type=EdgeType.REFUTES,
        allowed=_EVIDENCE_SOURCES,
        default_origin=Origin.INFERRED,
        symmetric=False,
        requires_confidence=True,
        derived=True,
        semantics=(
            "Fact(subject) -> Hypothesis: the subject of a fact in the hypothesis's "
            "refuting_facts list — REQUIRED anti-confirmation-bias evidence "
            "(principle 10); also how a typed NoEvidence fact ('we checked X, it was "
            "clean') refutes a hypothesis honestly (R-P2). DERIVED, like SUPPORTS."
        ),
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
        symmetric=False,
        requires_confidence=False,
        semantics=(
            "Hypothesis(confirmed) -> the remediating action/change record — "
            "DESIGN §2.1 R-G2: the root cause IS a confirmed Hypothesis, remediation "
            "is this edge/hypothesis store record, never a node."
        ),
    ),
)
