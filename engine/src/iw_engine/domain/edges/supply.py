"""Ownership / supply-chain (DESIGN-INPUT §B.3) — business ownership plus the
`CodeCommit -> BuildArtifact -> Release -> running Deployment/Pod` provenance spine
that change-analysis (the cheapest strong signal) and "what code is actually running"
walk. `DEPLOYED_AS` was added per DESIGN §2.1 R-G1 to close a capability-fold gap.

Directionality note: canonical direction per DESIGN §2.1 R-G1 / DESIGN-INPUT §E.2 is
listed first in each `allowed` tuple; a permissive reverse pair is also included where
a scenario adapter is more naturally expressed the other way round (better slightly
permissive than blocking a real scenario, per the build brief).
"""
from __future__ import annotations

from ..enums import EdgeType, NodeType, Origin
from ..spec import EdgeSpec

SPECS: tuple[EdgeSpec, ...] = (
    EdgeSpec(
        type=EdgeType.OWNS,
        allowed=(
            (NodeType.TEAM, NodeType.SERVICE),
            (NodeType.TEAM, NodeType.APPLICATION),
            (NodeType.TEAM, NodeType.COMPONENT),
            (NodeType.APPLICATION, NodeType.SERVICE),
        ),
        default_origin=Origin.DECLARED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.BUILT_FROM,
        allowed=(
            (NodeType.BUILD_ARTIFACT, NodeType.CODE_COMMIT),
            (NodeType.RELEASE, NodeType.BUILD_ARTIFACT),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.RELEASED_AS,
        allowed=(
            (NodeType.BUILD_ARTIFACT, NodeType.RELEASE),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.RUNS_VERSION,
        allowed=(
            (NodeType.DEPLOYMENT, NodeType.RELEASE),
            (NodeType.POD, NodeType.RELEASE),
            (NodeType.DEPLOYMENT, NodeType.BUILD_ARTIFACT),
            (NodeType.POD, NodeType.BUILD_ARTIFACT),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.DEPLOYED_AS,
        allowed=(
            (NodeType.RELEASE, NodeType.DEPLOYMENT),
            (NodeType.RELEASE, NodeType.POD),
            (NodeType.BUILD_ARTIFACT, NodeType.DEPLOYMENT),
            (NodeType.BUILD_ARTIFACT, NodeType.POD),
            (NodeType.DEPLOYMENT, NodeType.RELEASE),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
    EdgeSpec(
        type=EdgeType.INTRODUCED_BY,
        allowed=(
            (NodeType.CHANGE_EVENT, NodeType.CODE_COMMIT),
            (NodeType.CHANGE_EVENT, NodeType.RELEASE),
            (NodeType.CHANGE_EVENT, NodeType.PULL_REQUEST),
            (NodeType.RELEASE, NodeType.CODE_COMMIT),
        ),
        default_origin=Origin.DISCOVERED,
        requires_confidence=False,
    ),
)
