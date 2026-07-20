"""Git capability-adapter test — same pattern as test_capability.py: invoke via
CapabilityLayer, materialize, assert ZERO rejections (adapter emits only registry-valid
types), then assert the expected commit/PR/diff/blame nodes-facts-edges appear."""
from __future__ import annotations

from iw_engine.capability import CapabilityLayer
from iw_engine.capability.adapters.git import GitAdapter
from iw_engine.domain import registry
from iw_engine.domain.enums import EdgeType, NodeType
from iw_engine.domain.node import Node
from iw_engine.domain.playbook import Tunables
from iw_engine.graph import Graph
from iw_engine.graph.reducer import materialize

RAW = {
    "commit": {
        "sha": "abc123def456",
        "repo": "payments-api",
        "author": "jdoe",
        "parent_sha": "abc123def000",
        "authored_at": "2026-07-18T09:00:00Z",
    },
    "pr": {
        "pr_id": "482",
        "repo": "payments-api",
        "author": "jdoe",
        "merged_sha": "abc123def456",
        "event": "merged",
        "at": "2026-07-18T10:00:00Z",
    },
    "diff": {
        "from_sha": "abc123def000",
        "to_sha": "abc123def456",
        "files_changed": 2,
        "lines_added": 14,
        "lines_deleted": 6,
        "at": "2026-07-18T09:05:00Z",
    },
    "change": {"change_id": "CHG0012345", "change_type": "deploy"},
    "blame": {
        "file": "src/main/java/TaxCalculator.java",
        "line": 88,
        "sha": "abc123def456",
        "snippet": "return total * rate;",
    },
    "error_signature": {
        "signature_hash": "sig-npe-taxcalc-88",
        "exception_class": "NullPointerException",
        "first_seen": "2026-07-19T13:55:00Z",
    },
    "hypothesis_id": "h1",
}

COMMIT_ID = registry.node_id(NodeType.CODE_COMMIT, {"sha": "abc123def456"})
PR_ID = registry.node_id(NodeType.PULL_REQUEST, {"repo": "payments-api", "pr_id": "482"})
CHANGE_ID = registry.node_id(NodeType.CHANGE_EVENT, {"change_id": "CHG0012345"})
ES_ID = registry.node_id(NodeType.ERROR_SIGNATURE, {"signature_hash": "sig-npe-taxcalc-88"})


def _graph_with_hypothesis() -> Graph:
    """Seed a pre-existing Hypothesis node — proposing hypotheses is the planner's job;
    this adapter only draws a CAUSED_BY edge to one that already exists on the graph."""
    g = Graph()
    g.upsert_node(Node(id="hyp:h1", type=NodeType.HYPOTHESIS,
                       props={"statement": "recent TaxCalculator change caused the NPE"},
                       created_by=0))
    return g


def test_git_normalize_folds_cleanly():
    layer = CapabilityLayer([GitAdapter()])
    ops, inv = layer.invoke("blame", RAW, allow_write=False)
    assert inv.provider == "git" and not inv.blocked

    mat = materialize(ops, 1, _graph_with_hypothesis(), Tunables())
    assert mat.rejections == [], mat.rejections   # adapter emits only registry-valid types

    # CodeCommit node (identity = sha)
    assert any(n.id == COMMIT_ID and n.type == NodeType.CODE_COMMIT for n in mat.nodes)
    # PullRequest node (identity = repo, pr_id)
    assert any(n.id == PR_ID and n.type == NodeType.PULL_REQUEST for n in mat.nodes)
    assert any(e.entity_ref == PR_ID and e.type == "merged" for e in mat.events)
    # diff facts on the commit
    assert any(f.subject_ref == COMMIT_ID and f.predicate == "files_changed" and f.value == 2
              for f in mat.facts)
    assert any(f.subject_ref == COMMIT_ID and f.predicate == "lines_added" and f.value == 14
              for f in mat.facts)
    # INTRODUCED_BY: ChangeEvent -> CodeCommit
    assert any(e.type == EdgeType.INTRODUCED_BY and e.src == CHANGE_ID and e.dst == COMMIT_ID
              for e in mat.edges)
    # blame pins an ErrorSignature to the CodeCommit via CAUSED_BY
    assert any(e.type == EdgeType.CAUSED_BY and e.src == ES_ID and e.dst == COMMIT_ID
              for e in mat.edges)
    # hypothesis id present in raw -> Hypothesis -> CodeCommit CAUSED_BY edge too
    assert any(e.type == EdgeType.CAUSED_BY and e.src == "hyp:h1" and e.dst == COMMIT_ID
              for e in mat.edges)


def test_git_blame_without_hypothesis_id_skips_hypothesis_edge():
    raw = {k: v for k, v in RAW.items() if k != "hypothesis_id"}
    layer = CapabilityLayer([GitAdapter()])
    ops, _ = layer.invoke("blame", raw, allow_write=False)
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == [], mat.rejections
    assert any(e.type == EdgeType.CAUSED_BY and e.src == ES_ID and e.dst == COMMIT_ID
              for e in mat.edges)
    assert not any(e.type == EdgeType.CAUSED_BY and e.src.startswith("hyp:") for e in mat.edges)


def test_git_get_commit_alone_folds_just_the_commit_node():
    raw = {"commit": RAW["commit"]}
    layer = CapabilityLayer([GitAdapter()])
    ops, inv = layer.invoke("get_commit", raw, allow_write=False)
    assert inv.provider == "git" and not inv.blocked
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == []
    assert len(mat.nodes) == 1 and mat.nodes[0].id == COMMIT_ID
    assert mat.facts == [] and mat.edges == []


def test_unknown_intent_is_recorded_not_crashing():
    layer = CapabilityLayer([GitAdapter()])
    ops, inv = layer.invoke("nonexistent_intent", {}, allow_write=False)
    assert ops == [] and inv.blocked and "no capability" in inv.reason
