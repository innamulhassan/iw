"""L4 — network/edge tier (DESIGN §3 / DESIGN-INPUT §B.2).

LoadBalancer / Route / NetworkSegment / FirewallRule / Dns, plus the security-layer
path/policy siblings Proxy / ApiGateway / Cdn / Waf. Powers scenario 3 (network —
retransmits/MTU) and scenario 5 (firewall — ACL change -> deny spike).

Four L4 families, kept visible (graph-model refinement — Networking/Infra + Security):
  · path-device : LoadBalancer, Proxy, ApiGateway, Cdn
  · transport   : NetworkSegment (folds VPN/tunnel/Transit-GW)
  · policy      : FirewallRule (folds security_group/NACL), Waf
  · naming      : Dns
"""
from __future__ import annotations

from ..enums import NodeType
from ..spec import NodeSpec

SPECS: tuple[NodeSpec, ...] = (
    NodeSpec(
        type=NodeType.LOAD_BALANCER,
        tier="L4",
        identity_keys=("lb_id",),
        static_props=("lb_id", "name"),
        fact_predicates=("backend_healthy_count", "request_rate", "5xx_rate"),
        event_types=("backend_marked_down", "backend_marked_up", "config_changed"),
        discriminator=(
            "Routes traffic to Service backends via ROUTES_TO; distinguishes from Route "
            "(a single rule) by being the device/resource itself."
        ),
    ),
    NodeSpec(
        type=NodeType.ROUTE,
        tier="L4",
        identity_keys=("route_id",),
        static_props=("route_id", "path_pattern"),
        fact_predicates=(),
        event_types=("rule_changed",),
        discriminator=(
            "A single routing rule (path/host match) owned by a LoadBalancer/Dns — not "
            "the LB resource itself."
        ),
    ),
    NodeSpec(
        type=NodeType.NETWORK_SEGMENT,
        tier="L4",
        identity_keys=("segment_id",),
        static_props=("segment_id", "cidr", "vlan"),
        fact_predicates=("packet_loss", "retrans_segs", "probe_success"),
        event_types=("mtu_changed", "link_flapping", "tunnel_down", "rekey_failed"),
        discriminator=(
            "A network/subnet boundary (VLAN/CIDR) Hosts CONNECTS_TO — the transport "
            "family — use for boundary/transport-layer symptoms (retransmits, MTU, "
            "probe failures), distinct from FirewallRule (policy) or Route (L7 rule). "
            "Discriminator vs firewall: clean policy denies point to FirewallRule; "
            "flapping/retransmits with no denies point here. "
            "FOLD: a VPN / tunnel / Transit-Gateway is modelled here (its down/rekey "
            "faults are the tunnel_down / rekey_failed events), not as a new type. "
            "DEFER: a bare NIC/ENI is not modelled (promote only if a per-NIC scenario "
            "is named); a BGP/AS/route-table withdrawal is a generic_ci concern — model "
            "it as a change_event/anomaly on the affected segment, not a new node."
        ),
    ),
    NodeSpec(
        type=NodeType.FIREWALL_RULE,
        tier="L4",
        identity_keys=("rule_id",),
        static_props=("rule_id", "direction", "proto", "port_range", "src", "dst", "class_hint"),
        fact_predicates=("deny_count",),
        event_types=("rule_changed", "deny_spike"),
        discriminator=(
            "A specific ACL/policy rule; the protected resource points at it via "
            "SECURED_BY. The policy family. Distinguishes from NetworkSegment by being "
            "a policy object, not a physical/logical boundary — a security change here "
            "is human-gated, never auto-applied. "
            "FOLD: a cloud security_group or NACL is a FirewallRule too — record the "
            "cloud class in the class_hint prop rather than minting a new type. "
            "Waf is the L7 sibling in this family (app-layer request filtering)."
        ),
    ),
    NodeSpec(
        type=NodeType.DNS,
        tier="L4",
        identity_keys=("record_name", "record_type"),
        static_props=("record_name", "record_type"),
        fact_predicates=("resolution_success_rate", "ttl"),
        event_types=("record_changed",),
        discriminator=(
            "A DNS record resolving a name to a target — the naming family — use when "
            "a symptom traces to name resolution rather than L4 routing/policy."
        ),
    ),
    NodeSpec(
        type=NodeType.PROXY,
        tier="L4",
        identity_keys=("proxy_id",),
        static_props=("proxy_id", "name", "kind"),
        fact_predicates=(
            "upstream_5xx_rate",
            "active_connections",
            "request_rate",
            "p99_latency",
            "upstream_healthy",
        ),
        event_types=(
            "config_reloaded",
            "upstream_timeout",
            "mtls_handshake_failed",
            "connection_pool_exhausted",
        ),
        discriminator=(
            "A reverse/forward/sidecar/egress proxy (Envoy/nginx/HAProxy) — the "
            "path-device family sibling of LoadBalancer, and the home for the "
            "otherwise-homeless service-mesh sidecar class. The kind prop is one of "
            "reverse|forward|sidecar|egress. Use when the hop that fails is a proxy "
            "process (upstream timeouts, mTLS handshake, connection-pool exhaustion), "
            "distinct from LoadBalancer (the L4 traffic distributor) and ApiGateway "
            "(auth/rate-limit/route-map). DEFER: a plain router/switch is not a Proxy — "
            "model it as a config_item (record the device class in class_hint)."
        ),
    ),
    NodeSpec(
        type=NodeType.API_GATEWAY,
        tier="L4",
        identity_keys=("gateway_id",),
        static_props=("gateway_id", "name", "provider"),
        fact_predicates=(
            "request_rate",
            "5xx_rate",
            "rejected_rate",
            "auth_failure_rate",
            "throttle_rate",
            "p99_latency",
        ),
        event_types=(
            "route_remapped",
            "rate_limit_tripped",
            "auth_reject_spike",
            "config_deployed",
        ),
        discriminator=(
            "An API gateway (Kong/Apigee/AWS-APIGW/Istio-GW/APIM) owning auth, "
            "rate-limit, quota and the route-map — the path-device family sibling of "
            "LoadBalancer/Proxy. Use when the symptom is gateway-level policy (auth "
            "rejects, throttle/quota trips, a route remap), distinct from a raw Proxy "
            "(transport hop) and LoadBalancer (backend distribution). It EXPOSES the "
            "ApiEndpoints/Routes it fronts."
        ),
    ),
    NodeSpec(
        type=NodeType.CDN,
        tier="L4",
        identity_keys=("cdn_id",),
        static_props=("cdn_id", "name", "provider"),
        fact_predicates=(
            "cache_hit_ratio",
            "origin_5xx_rate",
            "edge_5xx_rate",
            "request_rate",
            "origin_latency_p99",
        ),
        event_types=(
            "origin_fetch_error",
            "cache_purged",
            "config_deployed",
            "pop_outage",
        ),
        discriminator=(
            "A content delivery network (CloudFront/Akamai/Fastly/Cloudflare) — the "
            "outermost path-device family sibling. Its defining fork is origin-vs-edge: "
            "separate origin_5xx_rate / edge_5xx_rate and origin_latency_p99 localise a "
            "fault to the edge PoP vs the backing origin. Use for cache-hit collapse, "
            "PoP outage, or origin-fetch errors."
        ),
    ),
    NodeSpec(
        type=NodeType.WAF,
        tier="L4",
        identity_keys=("waf_id",),
        static_props=("waf_id", "name", "provider"),
        fact_predicates=("blocked_request_rate", "false_positive_rate", "rule_matches"),
        event_types=("rule_set_updated", "block_spike", "rule_disabled"),
        discriminator=(
            "A web application firewall (L7 app-layer request filtering) — the policy "
            "family sibling of FirewallRule (which is L3/L4 ACL). Optional Tier-1.5 "
            "guard the fronted resource points at via SECURED_BY. Use when a rule-set "
            "update drives a block spike or false-positive surge (legitimate traffic "
            "blocked), distinct from FirewallRule's network-layer deny."
        ),
    ),
)
