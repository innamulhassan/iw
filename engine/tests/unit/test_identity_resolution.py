"""P5 identity/alias layer (DOMAIN-v3 §2.1 + §9.2 — R-J5's unbuilt half).

Step 1 — slug hardening: `_` collapses like space/`/` (cross-tool spellings of one name mint
ONE id), and a missing identity-key value is a REJECTION, never a degenerate `type:` id.
Step 2 — aliases on the entity + the graph's alias index: identity-backbone props
(`sys_id`/`app_id`/`repo`/`k8s_workload`) become `aliases {scheme: id}`; the index binds
`scheme:id` → node id, first binding wins, conflict = journaled contradiction.
"""
from __future__ import annotations

from datetime import UTC, datetime

from iw_engine.domain.enums import EdgeType, NodeType, Source
from iw_engine.domain.operations import AddEdge, AddFact, AddNode
from iw_engine.domain.playbook import Tunables
from iw_engine.domain.registry import missing_identity_keys, node_id
from iw_engine.graph import Graph
from iw_engine.graph.reducer import materialize
from iw_engine.graph.resolver import alias_key, aliases_from_props

T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)

SVC_PROPS = {"service_name": "payments-api", "env": "prod",
             "app_id": "APM-PAYMEN", "sys_id": "sys_2fe9a1", "repo": "acme/payments-api",
             "k8s_workload": "payments-deploy"}
SID = "service:payments-api|prod"


def _fold_nodes(g: Graph, mat) -> None:
    """Apply materialized nodes the way the fold does (nodes first)."""
    for n in mat.nodes:
        g.upsert_node(n)


# ── step 1: slug hardening ─────────────────────────────────────────────────────
def test_slug_collapses_underscores_case_and_spaces_to_one_id():
    """Audit 4 probe D: `payments_api` used to mint a SECOND service beside `payments-api`.
    All cross-tool spellings of the same display name now produce the same id."""
    want = "service:payments-api|prod"
    for spelling in ("payments-api", "payments_api", "Payments API", "Payments_API",
                     "payments/api"):
        assert node_id(NodeType.SERVICE,
                       {"service_name": spelling, "env": "prod"}) == want


def test_missing_identity_key_is_reported():
    assert missing_identity_keys(NodeType.SERVICE, {"env": "prod"}) == ("service_name",)
    assert missing_identity_keys(NodeType.GENERIC_CI, {"ci_id": None}) == ("ci_id",)
    assert missing_identity_keys(NodeType.GENERIC_CI, {"ci_id": "  "}) == ("ci_id",)
    assert missing_identity_keys(NodeType.SERVICE,
                                 {"service_name": "payments-api", "env": "prod"}) == ()


def test_reducer_rejects_add_node_with_missing_identity_key():
    """DOMAIN-v3 §2.1: a missing identity key is a rejection, not a `type:` degenerate id —
    and the dependent fact in the same batch rejects as unknown subject (cascade, each with
    its own recorded reason)."""
    g, tun = Graph(), Tunables()
    ops = [
        AddNode(type=NodeType.GENERIC_CI, props={"class_hint": "cmdb_ci_lb_netscaler"}),  # 0
        AddFact(subject="generic_ci:", predicate="anything", value=1,                     # 1
                valid_from=T0, observed_at=T0,
                source=Source.SERVICENOW, source_reliability=0.9),
    ]
    mat = materialize(ops, 1, g, tun)
    assert mat.nodes == []
    assert len(mat.rejections) == 2
    assert mat.rejections[0].op_index == 0 and mat.rejections[0].op_kind == "add_node"
    assert "missing identity key" in mat.rejections[0].reason
    assert "ci_id" in mat.rejections[0].reason
    assert mat.rejections[1].op_index == 1 and "unknown subject" in mat.rejections[1].reason


def test_reducer_still_accepts_complete_identity():
    g, tun = Graph(), Tunables()
    mat = materialize([AddNode(type=NodeType.SERVICE,
                               props={"service_name": "Payments_API", "env": "prod"})],
                      1, g, tun)
    assert mat.rejections == []
    assert [n.id for n in mat.nodes] == ["service:payments-api|prod"]


# ── step 2: aliases on the entity + the graph index ────────────────────────────
def test_aliases_derived_from_identity_backbone_props():
    assert aliases_from_props(NodeType.SERVICE, SVC_PROPS) == {
        "servicenow": "sys_2fe9a1", "appd": "APM-PAYMEN",
        "git": "acme/payments-api", "k8s": "payments-deploy"}
    # empty values never claim
    assert aliases_from_props(NodeType.SERVICE, {"service_name": "x", "app_id": "  "}) == {}
    # type-scoped: `repo` on a commit/PR is a namespace qualifier, NOT that entity's identity
    assert aliases_from_props(NodeType.CODE_COMMIT, {"sha": "abc", "repo": "acme/x"}) == {}
    assert aliases_from_props(NodeType.PULL_REQUEST, {"repo": "acme/x", "pr_id": 7}) == {}


def test_reducer_lifts_backbone_props_into_node_aliases_and_graph_indexes_them():
    """The ServiceNow adapter's identity backbone (app_id/sys_id/repo/k8s_workload — carried
    inert as props since v1) becomes identity surface: aliases on the Node, scheme:id → node id
    in the graph's index after the fold applies the node."""
    g, tun = Graph(), Tunables()
    mat = materialize([AddNode(type=NodeType.SERVICE, props=dict(SVC_PROPS))], 1, g, tun)
    assert mat.rejections == []
    node = mat.nodes[0]
    assert node.aliases == {"servicenow": "sys_2fe9a1", "appd": "APM-PAYMEN",
                            "git": "acme/payments-api", "k8s": "payments-deploy"}
    assert node.props == SVC_PROPS                     # props unchanged — backbone stays visible
    _fold_nodes(g, mat)
    assert g.alias_index[alias_key("appd", "APM-PAYMEN")] == SID
    assert g.alias_index[alias_key("servicenow", "sys_2fe9a1")] == SID
    assert g.alias_index[alias_key("k8s", "payments-deploy")] == SID
    assert g.alias_index[alias_key("git", "acme/payments-api")] == SID


def test_alias_index_survives_graph_roundtrip():
    g, tun = Graph(), Tunables()
    _fold_nodes(g, materialize([AddNode(type=NodeType.SERVICE, props=dict(SVC_PROPS))],
                               1, g, tun))
    g2 = Graph.from_dict(g.to_dict())
    assert g2.alias_index == g.alias_index
    assert g2.nodes[SID].aliases == g.nodes[SID].aliases


def test_alias_upsert_unions_first_binding_wins():
    """A later observation adds NEW schemes freely; a conflicting claim on an already-bound
    scheme never silently rebinds (write-once flavor, §9.2)."""
    g, tun = Graph(), Tunables()
    _fold_nodes(g, materialize([AddNode(type=NodeType.SERVICE,
                                        props={"service_name": "payments-api", "env": "prod",
                                               "sys_id": "sys_2fe9a1"})], 1, g, tun))
    _fold_nodes(g, materialize([AddNode(type=NodeType.SERVICE,
                                        props={"service_name": "payments-api", "env": "prod",
                                               "app_id": "APM-PAYMEN"})], 2, g, tun))
    assert g.nodes[SID].aliases == {"servicenow": "sys_2fe9a1", "appd": "APM-PAYMEN"}


# ── step 3: entity resolution on ingest ────────────────────────────────────────
def _fold_all(g: Graph, mat) -> None:
    for n in mat.nodes:
        g.upsert_node(n)
    for f in mat.facts:
        g.add_fact(f)
    for e in mat.events:
        g.add_event(e)
    for e in mat.edges:
        g.add_edge(e)


def test_split_brain_unification_via_shared_tool_id():
    """THE split-brain kill (audit 4 S1.4 / task step 3): the same service arrives under two
    display names — `payments-api` (ServiceNow) and `payments-svc` (another tool) — linked by a
    shared sys_id. One entity results; the arrival's facts land on it (same-batch refs follow
    the fold); its identity-key props never overwrite the canonical's (write-once)."""
    g, tun = Graph(), Tunables()
    _fold_all(g, materialize([AddNode(type=NodeType.SERVICE, props=dict(SVC_PROPS))], 1, g, tun))

    twin_props = {"service_name": "payments-svc", "env": "prod", "sys_id": "sys_2fe9a1",
                  "owner": "team-payments"}
    twin_id = node_id(NodeType.SERVICE, twin_props)          # what the adapter would compute
    mat = materialize([
        AddNode(type=NodeType.SERVICE, props=twin_props),
        AddFact(subject=twin_id, predicate="degraded", value=True,
                valid_from=T0, observed_at=T0, source=Source.PROMETHEUS,
                source_reliability=0.95),
    ], 2, g, tun)
    assert mat.rejections == []
    assert [n.id for n in mat.nodes] == [SID]                # resolved, no twin minted
    assert mat.facts[0].subject_ref == SID                   # the paired fact followed the fold
    _fold_all(g, mat)
    assert twin_id not in g.nodes and len(g.nodes) == 1
    assert g.nodes[SID].props["service_name"] == "payments-api"   # write-once identity
    assert g.nodes[SID].props["owner"] == "team-payments"         # non-identity props merged


def test_observation_keyed_only_by_tool_credential_resolves():
    """DOMAIN-v3 §2.1 flagship: an observation arriving keyed ONLY `app_id=APM-PAYMEN` (no
    service_name/env — pre-P5 a degenerate id, post-step-1 a rejection) resolves to the
    existing entity through the alias index."""
    g, tun = Graph(), Tunables()
    _fold_all(g, materialize([AddNode(type=NodeType.SERVICE, props=dict(SVC_PROPS))], 1, g, tun))
    mat = materialize([AddNode(type=NodeType.SERVICE,
                               props={"app_id": "APM-PAYMEN", "tier_hint": "gold"})], 2, g, tun)
    assert mat.rejections == []
    assert [n.id for n in mat.nodes] == [SID]
    _fold_all(g, mat)
    assert g.nodes[SID].props["tier_hint"] == "gold"


def test_alias_keyed_subject_and_edge_endpoint_resolve():
    """An assertion subject (or edge endpoint) written as `scheme:id` lands on the canonical
    entity — cross-tool joins stop depending on display-name luck."""
    g, tun = Graph(), Tunables()
    _fold_all(g, materialize([AddNode(type=NodeType.SERVICE, props=dict(SVC_PROPS))], 1, g, tun))
    mat = materialize([
        AddFact(subject="appd:APM-PAYMEN", predicate="degraded", value=True,
                valid_from=T0, observed_at=T0, source=Source.APPD, source_reliability=0.9),
        AddNode(type=NodeType.ALERT, props={"alert_id": "alt-9"}),
        AddEdge(type=EdgeType.FIRED_ON, src="alert:alt-9", dst="servicenow:sys_2fe9a1"),
    ], 2, g, tun)
    assert mat.rejections == []
    assert mat.facts[0].subject_ref == SID
    assert mat.edges[0].dst == SID
    assert mat.edges[0].id == f"edge:fired_on:alert:alt-9->{SID}:discovered"


def test_unresolvable_alias_subject_still_rejects_unknown():
    g, tun = Graph(), Tunables()
    mat = materialize([AddFact(subject="appd:GHOST", predicate="degraded", value=True,
                               valid_from=T0, observed_at=T0, source=Source.APPD,
                               source_reliability=0.9)], 1, g, tun)
    assert len(mat.rejections) == 1 and "unknown subject appd:GHOST" in mat.rejections[0].reason


def test_ambiguous_credentials_never_guess():
    """Two credentials bound to two DIFFERENT canonicals + no identity keys → rejection naming
    the ambiguity (deterministic, never a coin-flip merge)."""
    g, tun = Graph(), Tunables()
    _fold_all(g, materialize([
        AddNode(type=NodeType.SERVICE, props={"service_name": "payments-api", "env": "prod",
                                              "sys_id": "SYS-A"}),
        AddNode(type=NodeType.SERVICE, props={"service_name": "checkout-api", "env": "prod",
                                              "app_id": "APM-B"}),
    ], 1, g, tun))
    mat = materialize([AddNode(type=NodeType.SERVICE,
                               props={"sys_id": "SYS-A", "app_id": "APM-B"})], 2, g, tun)
    assert mat.nodes == []
    assert len(mat.rejections) == 1
    assert "ambiguous alias resolution" in mat.rejections[0].reason
    assert "missing identity key" in mat.rejections[0].reason


def test_complete_keys_with_ambiguous_credentials_mint_with_contradiction_notices():
    """Ambiguous credentials + COMPLETE identity keys: the arrival mints under its own id (no
    guess), and each foreign claim is a recorded contradiction."""
    g, tun = Graph(), Tunables()
    _fold_all(g, materialize([
        AddNode(type=NodeType.SERVICE, props={"service_name": "payments-api", "env": "prod",
                                              "sys_id": "SYS-A"}),
        AddNode(type=NodeType.SERVICE, props={"service_name": "checkout-api", "env": "prod",
                                              "app_id": "APM-B"}),
    ], 1, g, tun))
    mat = materialize([AddNode(type=NodeType.SERVICE,
                               props={"service_name": "orders-api", "env": "prod",
                                      "sys_id": "SYS-A", "app_id": "APM-B"})], 2, g, tun)
    assert [n.id for n in mat.nodes] == ["service:orders-api|prod"]
    assert len(mat.rejections) == 2
    assert all("alias contradiction" in r.reason for r in mat.rejections)


def test_alias_contradiction_between_canonical_entities_is_recorded_not_rebound():
    """§9.2: two DIFFERENT canonical entities claiming the same tool id is a CONTRADICTION —
    journaled via the rejections channel (surfaced to planner + bundle), the claiming op still
    materializes, and the index keeps the first binding. (Both entities already exist as
    canonicals, so this is never a resolution/unification case: canonical entities never merge.)"""
    g, tun = Graph(), Tunables()
    _fold_nodes(g, materialize([AddNode(type=NodeType.SERVICE,
                                        props={"service_name": "payments-api", "env": "prod",
                                               "sys_id": "SYS-1"})], 1, g, tun))
    _fold_nodes(g, materialize([AddNode(type=NodeType.SERVICE,
                                        props={"service_name": "checkout-api", "env": "prod"})],
                               2, g, tun))
    mat = materialize([AddNode(type=NodeType.SERVICE,
                               props={"service_name": "checkout-api", "env": "prod",
                                      "sys_id": "SYS-1"})], 3, g, tun)
    assert [n.id for n in mat.nodes] == ["service:checkout-api|prod"]   # op NOT dropped
    assert len(mat.rejections) == 1
    assert "alias contradiction" in mat.rejections[0].reason
    assert "servicenow:SYS-1" in mat.rejections[0].reason
    _fold_nodes(g, mat)
    assert g.alias_index[alias_key("servicenow", "SYS-1")] == SID       # first binding kept
    # the contested claim is still visible on the claiming node (honest record), index unmoved
    assert g.nodes["service:checkout-api|prod"].aliases == {"servicenow": "SYS-1"}
