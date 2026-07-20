"""Artifactory adapter unit test — the digest join: BuildArtifact BUILT_FROM CodeCommit
(git.revision), and BuildArtifact RELEASED_AS Release when a promotion is present. Same
pattern as test_capability.py's Prometheus test: invoke via CapabilityLayer, materialize,
assert ZERO reducer rejections (the adapter emits only registry-valid types/edges)."""
from __future__ import annotations

from iw_engine.capability import CapabilityLayer
from iw_engine.capability.adapters.artifactory import ArtifactoryAdapter
from iw_engine.domain import registry
from iw_engine.domain.enums import EdgeType, NodeType
from iw_engine.domain.playbook import Tunables
from iw_engine.graph import Graph
from iw_engine.graph.reducer import materialize

RAW = {
    "artifacts": [
        {
            "sha256": "sha256:9f8c7b6a5d4e3f2c1b0a9e8d7c6b5a4f3e2d1c0b",
            "repo": "docker-local",
            "build_number": "482",
            "created": "2026-07-15T10:02:00Z",
            "properties": {
                "git.revision": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0",
                "promoted.to": "prod",
                "promoted.at": "2026-07-16T09:00:00Z",
            },
        }
    ],
    "promotions": [
        {
            "sha256": "sha256:9f8c7b6a5d4e3f2c1b0a9e8d7c6b5a4f3e2d1c0b",
            "release_id": "staging",
            "promoted_at": "2026-07-15T18:00:00Z",
        }
    ],
}


def test_artifactory_normalize_folds_cleanly():
    layer = CapabilityLayer([ArtifactoryAdapter()])
    ops, inv = layer.invoke("get_artifact_by_digest", RAW, allow_write=False)
    assert inv.provider == "artifactory" and not inv.blocked

    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == [], mat.rejections     # adapter emits only registry-valid types

    digest = "sha256:9f8c7b6a5d4e3f2c1b0a9e8d7c6b5a4f3e2d1c0b"
    art_id = registry.node_id(NodeType.BUILD_ARTIFACT, {"digest": digest})
    commit_id = registry.node_id(NodeType.CODE_COMMIT,
                                 {"sha": "a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0"})
    prod_id = registry.node_id(NodeType.RELEASE, {"release_id": "prod"})
    staging_id = registry.node_id(NodeType.RELEASE, {"release_id": "staging"})

    assert any(n.id == art_id and n.type == NodeType.BUILD_ARTIFACT for n in mat.nodes)
    assert any(n.id == commit_id and n.type == NodeType.CODE_COMMIT for n in mat.nodes)
    assert any(n.id == prod_id for n in mat.nodes)
    assert any(n.id == staging_id for n in mat.nodes)

    assert any(e.type == EdgeType.BUILT_FROM and e.src == art_id and e.dst == commit_id
              for e in mat.edges)
    assert any(e.type == EdgeType.RELEASED_AS and e.src == art_id and e.dst == prod_id
              for e in mat.edges)
    assert any(e.type == EdgeType.RELEASED_AS and e.src == art_id and e.dst == staging_id
              for e in mat.edges)

    assert any(ev.entity_ref == art_id and ev.type == "built" for ev in mat.events)
    assert any(ev.entity_ref == art_id and ev.type == "promoted" for ev in mat.events)
    assert any(ev.entity_ref == prod_id and ev.type == "released" for ev in mat.events)
    assert any(ev.entity_ref == staging_id and ev.type == "released" for ev in mat.events)


def test_list_promotions_standalone_stubs_artifact():
    """list_promotions invoked on its own (no sibling artifacts[] in the raw) still folds
    cleanly — the adapter mints a minimal BuildArtifact stub so the edge has a known dst."""
    raw = {
        "promotions": [
            {"sha256": "sha256:deadbeef", "release_id": "prod",
             "promoted_at": "2026-07-16T09:00:00Z"}
        ]
    }
    layer = CapabilityLayer([ArtifactoryAdapter()])
    ops, inv = layer.invoke("list_promotions", raw, allow_write=False)
    assert not inv.blocked

    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == [], mat.rejections

    art_id = registry.node_id(NodeType.BUILD_ARTIFACT, {"digest": "sha256:deadbeef"})
    rel_id = registry.node_id(NodeType.RELEASE, {"release_id": "prod"})
    assert any(n.id == art_id for n in mat.nodes)
    assert any(e.type == EdgeType.RELEASED_AS and e.src == art_id and e.dst == rel_id
              for e in mat.edges)


def test_unknown_intent_still_recorded_not_crashing():
    layer = CapabilityLayer([ArtifactoryAdapter()])
    ops, inv = layer.invoke("nonexistent_intent", {}, allow_write=False)
    assert ops == [] and inv.blocked and "no capability" in inv.reason
