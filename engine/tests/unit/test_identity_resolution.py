"""P5 identity/alias layer (DOMAIN-v3 §2.1 + §9.2 — R-J5's unbuilt half).

Step 1 — slug hardening: `_` collapses like space/`/` (cross-tool spellings of one name mint
ONE id), and a missing identity-key value is a REJECTION, never a degenerate `type:` id.
Step 2 — aliases on the entity + the graph's alias index: identity-backbone props
(`sys_id`/`app_id`/`repo`/`k8s_workload`) become `aliases {scheme: id}`; the index binds
`scheme:id` → node id, first binding wins, conflict = journaled contradiction.
"""
from __future__ import annotations

from datetime import UTC, datetime

from iw_engine.domain.enums import NodeType, Source
from iw_engine.domain.operations import AddFact, AddNode
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
