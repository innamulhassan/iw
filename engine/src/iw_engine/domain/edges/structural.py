"""Structural spine (DESIGN-INPUT §B.3 "Structural spine") — the durable, mostly-
declared/discovered dependency+placement backbone. Convention `(from) -[EDGE]-> (to)`;
dependency edges point dependent -> provider. Never mutated by causal inference
(edges/causal.py is a separate, refutable layer over the same node pairs — the graph
is a MultiDiGraph so both coexist, DESIGN §2.1 R-G8).
"""
from __future__ import annotations

from ..enums import EdgeType, NodeType, Origin
from ..spec import EdgeSpec

SPECS: tuple[EdgeSpec, ...] = (
    EdgeSpec(
        type=EdgeType.DEPENDS_ON,
        allowed=(
            (NodeType.SERVICE, NodeType.SERVICE),
            (NodeType.SERVICE, NodeType.DATABASE),
            (NodeType.SERVICE, NodeType.MESSAGE_QUEUE),
            (NodeType.SERVICE, NodeType.CACHE),
            (NodeType.SERVICE, NodeType.EXTERNAL_SERVICE),
            (NodeType.SERVICE, NodeType.API_ENDPOINT),
            (NodeType.COMPONENT, NodeType.SERVICE),
            (NodeType.COMPONENT, NodeType.COMPONENT),
            (NodeType.COMPONENT, NodeType.DATABASE),
            (NodeType.API_ENDPOINT, NodeType.SERVICE),
            (NodeType.BATCH_JOB, NodeType.DATABASE),
            (NodeType.BATCH_JOB, NodeType.SERVICE),
            (NodeType.BATCH_JOB, NodeType.MESSAGE_QUEUE),
            (NodeType.APPLICATION, NodeType.EXTERNAL_SERVICE),
        ),
        default_origin=Origin.DECLARED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.CALLS,
        allowed=(
            (NodeType.SERVICE, NodeType.SERVICE),
            (NodeType.SERVICE, NodeType.API_ENDPOINT),
            (NodeType.API_ENDPOINT, NodeType.SERVICE),
            (NodeType.COMPONENT, NodeType.SERVICE),
            # discovered downstream BACKEND / uninstrumented peer (AppD exit-call /
            # OTel peer.service / DD inferred-service) — the egress gap-closer (graph-model §C1).
            (NodeType.SERVICE, NodeType.EXTERNAL_SERVICE),
            (NodeType.COMPONENT, NodeType.EXTERNAL_SERVICE),
            (NodeType.API_ENDPOINT, NodeType.EXTERNAL_SERVICE),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
        # edge-borne RED (§C2), as CANONICAL names — the reducer governs the assertion AFTER
        # dictionary canonicalization (M30), parallel to node `applies_to`, so this lists what
        # a discovered CALLS legally carries in canonical form: the vendor exit-call spellings
        # (call_rate/call_error_rate/call_latency_p99) merge to these before the legality check.
        fact_predicates=("request_rate", "error_rate", "latency_p99"),
    ),
    EdgeSpec(
        type=EdgeType.REALIZES,
        allowed=(
            (NodeType.POD, NodeType.REPLICASET),
            (NodeType.REPLICASET, NodeType.DEPLOYMENT),
            (NodeType.POD, NodeType.DEPLOYMENT),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.INSTANCE_OF,
        allowed=(
            (NodeType.POD, NodeType.DEPLOYMENT),
            (NodeType.REPLICASET, NodeType.DEPLOYMENT),
            (NodeType.CONTAINER, NodeType.POD),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.RUNS_ON,
        allowed=(
            (NodeType.POD, NodeType.HOST),
            (NodeType.CONTAINER, NodeType.HOST),
            (NodeType.PROCESS, NodeType.HOST),
            (NodeType.BATCH_JOB, NodeType.HOST),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.HOSTED_ON,
        allowed=(
            (NodeType.HOST, NodeType.CLUSTER),
            (NodeType.DATABASE, NodeType.HOST),
            (NodeType.MESSAGE_QUEUE, NodeType.HOST),
            (NodeType.CACHE, NodeType.HOST),
            (NodeType.LOAD_BALANCER, NodeType.HOST),
            (NodeType.LOAD_BALANCER, NodeType.NETWORK_SEGMENT),
        ),
        default_origin=Origin.DECLARED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.DEPLOYED_TO,
        allowed=(
            (NodeType.DEPLOYMENT, NodeType.NAMESPACE),
            (NodeType.DEPLOYMENT, NodeType.CLUSTER),
            (NodeType.RELEASE, NodeType.NAMESPACE),
            (NodeType.RELEASE, NodeType.CLUSTER),
        ),
        default_origin=Origin.DECLARED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.CONTAINS,
        allowed=(
            (NodeType.NAMESPACE, NodeType.POD),
            (NodeType.NAMESPACE, NodeType.DEPLOYMENT),
            (NodeType.NAMESPACE, NodeType.REPLICASET),
            (NodeType.NAMESPACE, NodeType.CONFIG_ITEM),
            (NodeType.CLUSTER, NodeType.NAMESPACE),
            (NodeType.CLUSTER, NodeType.HOST),
        ),
        default_origin=Origin.DECLARED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.MEMBER_OF,
        allowed=(
            (NodeType.HOST, NodeType.CLUSTER),
            (NodeType.POD, NodeType.NAMESPACE),
            (NodeType.SERVICE, NodeType.TEAM),
            (NodeType.COMPONENT, NodeType.SERVICE),
        ),
        default_origin=Origin.DECLARED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.EXPOSES,
        allowed=(
            (NodeType.SERVICE, NodeType.API_ENDPOINT),
            (NodeType.DEPLOYMENT, NodeType.API_ENDPOINT),
            (NodeType.LOAD_BALANCER, NodeType.ROUTE),
            (NodeType.API_GATEWAY, NodeType.API_ENDPOINT),
            (NodeType.API_GATEWAY, NodeType.ROUTE),
        ),
        default_origin=Origin.DECLARED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.ROUTES_TO,
        allowed=(
            (NodeType.LOAD_BALANCER, NodeType.SERVICE),
            (NodeType.ROUTE, NodeType.SERVICE),
            (NodeType.ROUTE, NodeType.API_ENDPOINT),
            (NodeType.DNS, NodeType.LOAD_BALANCER),
            # security/edge path-device chain (graph-model — Networking + Security):
            # dns -> cdn/api_gateway; cdn -> lb/api_gateway/service; api_gateway ->
            # service/endpoint/lb/proxy; proxy -> service/endpoint/lb; lb <-> proxy.
            (NodeType.DNS, NodeType.CDN),
            (NodeType.DNS, NodeType.API_GATEWAY),
            (NodeType.CDN, NodeType.LOAD_BALANCER),
            (NodeType.CDN, NodeType.API_GATEWAY),
            (NodeType.CDN, NodeType.SERVICE),
            (NodeType.API_GATEWAY, NodeType.SERVICE),
            (NodeType.API_GATEWAY, NodeType.API_ENDPOINT),
            (NodeType.API_GATEWAY, NodeType.LOAD_BALANCER),
            (NodeType.API_GATEWAY, NodeType.PROXY),
            (NodeType.PROXY, NodeType.SERVICE),
            (NodeType.PROXY, NodeType.API_ENDPOINT),
            (NodeType.PROXY, NodeType.LOAD_BALANCER),
            (NodeType.LOAD_BALANCER, NodeType.PROXY),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.CONNECTS_TO,
        allowed=(
            (NodeType.HOST, NodeType.NETWORK_SEGMENT),
            (NodeType.SERVICE, NodeType.NETWORK_SEGMENT),
            (NodeType.LOAD_BALANCER, NodeType.NETWORK_SEGMENT),
            (NodeType.PROXY, NodeType.NETWORK_SEGMENT),
            (NodeType.API_GATEWAY, NodeType.NETWORK_SEGMENT),
            (NodeType.CDN, NodeType.NETWORK_SEGMENT),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.READS_FROM,
        allowed=(
            (NodeType.SERVICE, NodeType.DATABASE),
            (NodeType.SERVICE, NodeType.CACHE),
            (NodeType.SERVICE, NodeType.SCHEMA),
            (NodeType.COMPONENT, NodeType.DATABASE),
            (NodeType.COMPONENT, NodeType.CACHE),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.WRITES_TO,
        allowed=(
            (NodeType.SERVICE, NodeType.DATABASE),
            (NodeType.SERVICE, NodeType.CACHE),
            (NodeType.SERVICE, NodeType.SCHEMA),
            (NodeType.COMPONENT, NodeType.DATABASE),
            (NodeType.BATCH_JOB, NodeType.DATABASE),
            (NodeType.BATCH_JOB, NodeType.SCHEMA),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.PRODUCES_TO,
        allowed=(
            (NodeType.SERVICE, NodeType.MESSAGE_QUEUE),
            (NodeType.BATCH_JOB, NodeType.MESSAGE_QUEUE),
            (NodeType.COMPONENT, NodeType.MESSAGE_QUEUE),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.CONSUMES_FROM,
        allowed=(
            (NodeType.SERVICE, NodeType.MESSAGE_QUEUE),
            (NodeType.BATCH_JOB, NodeType.MESSAGE_QUEUE),
            (NodeType.COMPONENT, NodeType.MESSAGE_QUEUE),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.SECURED_BY,
        allowed=(
            (NodeType.SERVICE, NodeType.FIREWALL_RULE),
            (NodeType.NETWORK_SEGMENT, NodeType.FIREWALL_RULE),
            (NodeType.ROUTE, NodeType.FIREWALL_RULE),
            (NodeType.LOAD_BALANCER, NodeType.FIREWALL_RULE),
            (NodeType.DATABASE, NodeType.FIREWALL_RULE),
            (NodeType.EXTERNAL_SERVICE, NodeType.FIREWALL_RULE),
            # dst=waf — the L7 app-firewall policy sibling guarding an edge resource.
            (NodeType.API_GATEWAY, NodeType.WAF),
            (NodeType.CDN, NodeType.WAF),
            (NodeType.LOAD_BALANCER, NodeType.WAF),
            (NodeType.PROXY, NodeType.WAF),
            (NodeType.SERVICE, NodeType.WAF),
            # dst=certificate — TLS-expiry's structural home (pre-existing fix): the
            # resource terminating TLS points at the cert securing it.
            (NodeType.LOAD_BALANCER, NodeType.CERTIFICATE),
            (NodeType.PROXY, NodeType.CERTIFICATE),
            (NodeType.API_GATEWAY, NodeType.CERTIFICATE),
            (NodeType.CDN, NodeType.CERTIFICATE),
            (NodeType.SERVICE, NodeType.CERTIFICATE),
        ),
        default_origin=Origin.DECLARED,
        requires_confidence=False,
    ),
)
