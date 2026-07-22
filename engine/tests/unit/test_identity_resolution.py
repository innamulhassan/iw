"""P5 identity/alias layer (DOMAIN-v3 §2.1 + §9.2 — R-J5's unbuilt half).

Step 1 — slug hardening: `_` collapses like space/`/` (cross-tool spellings of one name mint
ONE id), and a missing identity-key value is a REJECTION, never a degenerate `type:` id.
"""
from __future__ import annotations

from datetime import UTC, datetime

from iw_engine.domain.enums import NodeType, Source
from iw_engine.domain.operations import AddFact, AddNode
from iw_engine.domain.playbook import Tunables
from iw_engine.domain.registry import missing_identity_keys, node_id
from iw_engine.graph import Graph
from iw_engine.graph.reducer import materialize

T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)


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
