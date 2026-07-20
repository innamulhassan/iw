"""Artifactory adapter — the supply-chain digest join. Answers "what code is actually
running": a `BuildArtifact` (identity = digest) is `BUILT_FROM` the `CodeCommit` named by
its `git.revision` property (the Artifactory->Git join), and — when the digest has been
promoted — `RELEASED_AS` a named `Release` the running workload's `RUNS_VERSION`/
`DEPLOYED_AS` edges (folded by the OCP adapter) ultimately resolve back to by digest
match. Mock shape per DESIGN-INPUT §E.2: AQL-style records `{sha256, properties:
{git.revision, build.number, promoted.to/at}}`; `list_promotions` may instead surface
promotion events as sibling records keyed by the same digest. One raw envelope services
all four intents (get_artifact_by_digest / get_build / aql_search / list_promotions) —
normalize is intent-agnostic, presence-driven, same pattern as the Prometheus reference.
"""
from __future__ import annotations

from ...domain import registry
from ...domain.enums import Binding, EdgeType, Effect, NodeType, Source
from ...domain.operations import AddEdge, AddEvent, AddNode, Operation


class ArtifactoryAdapter:
    provider = "artifactory"
    intents = frozenset({"get_artifact_by_digest", "get_build", "list_promotions", "aql_search"})
    effect = Effect.READ
    binding = Binding.MCP   # JFrog ships a first-party MCP server

    def normalize(self, raw: dict) -> list[Operation]:
        ops: list[Operation] = []
        artifact_ids: dict[str, str] = {}   # digest -> node_id, joins the promotions loop below

        for art in raw.get("artifacts", []):
            digest = art.get("sha256") or art.get("digest")
            if not digest:
                continue
            props = {"digest": digest}
            if art.get("repo"):
                props["repo"] = art["repo"]
            if art.get("build_number"):
                props["build_number"] = art["build_number"]
            ops.append(AddNode(type=NodeType.BUILD_ARTIFACT, props=props))
            art_id = registry.node_id(NodeType.BUILD_ARTIFACT, props)
            artifact_ids[digest] = art_id

            if art.get("created"):
                built_payload = {"repo": art.get("repo"), "build_number": art.get("build_number")}
                ops.append(AddEvent(entity=art_id, type="built", occurred_at=art["created"],
                                    observed_at=art["created"], payload=built_payload,
                                    source=Source.ARTIFACTORY))

            properties = art.get("properties", {})
            git_rev = properties.get("git.revision")
            if git_rev:
                commit_props = {"sha": git_rev}
                ops.append(AddNode(type=NodeType.CODE_COMMIT, props=commit_props))
                commit_id = registry.node_id(NodeType.CODE_COMMIT, commit_props)
                ops.append(AddEdge(type=EdgeType.BUILT_FROM, src=art_id, dst=commit_id))

            # promotion may ride along on the artifact's own properties (promoted.to/at)
            # rather than arriving as a separate list_promotions record
            promoted_to = properties.get("promoted.to")
            if promoted_to:
                rel_props = {"release_id": promoted_to}
                if art.get("build_number"):
                    rel_props["version"] = art["build_number"]
                ops.append(AddNode(type=NodeType.RELEASE, props=rel_props))
                rel_id = registry.node_id(NodeType.RELEASE, rel_props)
                # RELEASED_AS is the canonical BuildArtifact->Release direction (an artifact
                # promoted/wrapped as a deployable release); BUILT_FROM's permissive reverse
                # pair (Release->BuildArtifact) exists for adapters whose data runs the other
                # way, not needed here since we start from the artifact.
                ops.append(AddEdge(type=EdgeType.RELEASED_AS, src=art_id, dst=rel_id))
                promoted_at = properties.get("promoted.at")
                if promoted_at:
                    ops.append(AddEvent(entity=rel_id, type="released", occurred_at=promoted_at,
                                        observed_at=promoted_at, payload={"digest": digest},
                                        source=Source.ARTIFACTORY))
                    ops.append(AddEvent(entity=art_id, type="promoted", occurred_at=promoted_at,
                                        observed_at=promoted_at,
                                        payload={"release_id": promoted_to},
                                        source=Source.ARTIFACTORY))

        for promo in raw.get("promotions", []):
            digest = promo.get("sha256") or promo.get("digest")
            release_id = promo.get("release_id") or promo.get("environment")
            if not digest or not release_id:
                continue
            art_id = artifact_ids.get(digest)
            if art_id is None:
                # list_promotions called standalone (no sibling aql/get_artifact record in
                # this raw) — mint a minimal stub so the edge below has a known endpoint.
                props = {"digest": digest}
                ops.append(AddNode(type=NodeType.BUILD_ARTIFACT, props=props))
                art_id = registry.node_id(NodeType.BUILD_ARTIFACT, props)
                artifact_ids[digest] = art_id

            rel_props = {"release_id": release_id}
            if promo.get("version"):
                rel_props["version"] = promo["version"]
            ops.append(AddNode(type=NodeType.RELEASE, props=rel_props))
            rel_id = registry.node_id(NodeType.RELEASE, rel_props)
            ops.append(AddEdge(type=EdgeType.RELEASED_AS, src=art_id, dst=rel_id))

            promoted_at = promo.get("promoted_at")
            if promoted_at:
                rel_payload = {"digest": digest, "environment": promo.get("environment")}
                ops.append(AddEvent(entity=rel_id, type="released", occurred_at=promoted_at,
                                    observed_at=promoted_at, payload=rel_payload,
                                    source=Source.ARTIFACTORY))
                ops.append(AddEvent(entity=art_id, type="promoted", occurred_at=promoted_at,
                                    observed_at=promoted_at, payload={"release_id": release_id},
                                    source=Source.ARTIFACTORY))
        return ops
