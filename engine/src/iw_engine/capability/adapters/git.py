"""Git adapter — commits, diffs, PR metadata, blame. Follows the Prometheus REFERENCE
adapter's shape: a `provider`, an `intents` set, an `effect`, and a pure
`normalize(raw) -> Operation[]` that folds the tool's raw JSON into the change/supply-chain
spine (DESIGN-INPUT §E.2 / §B.2-B.3): CodeCommit + PullRequest nodes, an INTRODUCED_BY edge
when a change is referenced, and diff stats as facts on the commit. A blame result pins the
terminal CAUSED_BY ErrorSignature->CodeCommit edge; a Hypothesis->CodeCommit CAUSED_BY edge
is added ONLY when the raw payload names a hypothesis id (causal linkage is usually the
planner's job — this adapter just draws the edge to an already-proposed hypothesis).
"""
from __future__ import annotations

from ...domain import registry
from ...domain.common import EvidenceRef
from ...domain.enums import Binding, ConfidenceLevel, EdgeType, Effect, NodeType, Source
from ...domain.operations import AddEdge, AddEvent, AddFact, AddNode, Operation
from ..layer import CapabilityMeta


class GitAdapter:
    provider = "git"
    intents = frozenset({"get_commit", "diff_range", "get_pr_for_commit", "blame", "read_diff"})
    effect = Effect.READ
    binding = Binding.REST   # local git — a thin REST/CLI shim, not an MCP server
    meta = CapabilityMeta(
        summary="Diffs, blame, and pull-request context for a change",
        queries_by="repo", returns="diffs, blame, commits")

    def normalize(self, raw: dict) -> list[Operation]:
        ops: list[Operation] = []

        commit = raw.get("commit")
        commit_id: str | None = None
        commit_repo: str | None = None
        if commit:
            props = {
                "sha": commit["sha"],
                "repo": commit.get("repo"),
                "author": commit.get("author"),
                "parent_sha": commit.get("parent_sha"),
                "authored_at": commit.get("authored_at"),
            }
            ops.append(AddNode(type=NodeType.CODE_COMMIT, props=props))
            commit_id = registry.node_id(NodeType.CODE_COMMIT, props)
            commit_repo = commit.get("repo")

        pr = raw.get("pr")
        if pr:
            pr_props = {
                "pr_id": pr["pr_id"],
                "repo": pr.get("repo", commit_repo),
                "author": pr.get("author"),
                "merged_sha": pr.get("merged_sha"),
            }
            ops.append(AddNode(type=NodeType.PULL_REQUEST, props=pr_props))
            pr_id = registry.node_id(NodeType.PULL_REQUEST, pr_props)
            if pr.get("event") and pr.get("at"):
                ops.append(AddEvent(entity=pr_id, type=pr["event"], occurred_at=pr["at"],
                                    observed_at=pr["at"], payload={}, source=Source.GIT))

        # a referenced change (compute its id up front so a content-only diff — no commit — can
        # attach to the change record that shipped it; the node itself is emitted below, in place)
        change = raw.get("change")
        change_props: dict | None = None
        change_id: str | None = None
        if change:
            change_props = {"change_id": change["change_id"],
                            "change_type": change.get("change_type")}
            change_id = registry.node_id(NodeType.CHANGE_EVENT, change_props)

        # diff_range / read_diff — diff stats as facts on the commit; when the payload carries
        # the actual `changed_lines` (GAP 2: content, not just counts) fold them into a
        # `diff_summary` fact the planner can read (e.g. the migration's `DROP INDEX` line).
        # With no commit surfaced, the diff belongs to the change record that shipped it.
        diff = raw.get("diff")
        diff_subject = commit_id or change_id
        if diff and diff_subject and diff.get("at"):
            for predicate in ("files_changed", "lines_added", "lines_deleted"):
                value = diff.get(predicate)
                if value is not None:
                    ops.append(AddFact(subject=diff_subject, predicate=predicate, value=value,
                                       valid_from=diff["at"], observed_at=diff["at"],
                                       source=Source.GIT,
                                       source_reliability=diff.get("reliability", 0.99)))
            changed = diff.get("changed_lines")
            if changed:
                summary = "; ".join(str(x) for x in changed) if isinstance(changed, list) \
                    else str(changed)
                ops.append(AddFact(subject=diff_subject, predicate="diff_summary", value=summary,
                                   valid_from=diff["at"], observed_at=diff["at"], source=Source.GIT,
                                   source_reliability=diff.get("reliability", 0.99)))

        # the change node + the INTRODUCED_BY join back to the commit (edge only when a commit
        # was surfaced; the node is emitted whenever a change is referenced)
        if change_props is not None:
            ops.append(AddNode(type=NodeType.CHANGE_EVENT, props=change_props))
            if commit_id:
                ops.append(AddEdge(type=EdgeType.INTRODUCED_BY, src=change_id, dst=commit_id))

        # blame — the terminal join: {file, line, sha} pins an ErrorSignature to a CodeCommit
        blame = raw.get("blame")
        if blame and blame.get("sha"):
            blame_props = {"sha": blame["sha"], "repo": blame.get("repo", commit_repo)}
            ops.append(AddNode(type=NodeType.CODE_COMMIT, props=blame_props))
            blame_commit_id = registry.node_id(NodeType.CODE_COMMIT, blame_props)

            # GAP 2 content: the blamed file:line + the actual offending source line, folded as a
            # readable fact on the commit (CODE_COMMIT predicates are unconstrained) so the planner
            # sees the code, not just a count. Live-only: hermetic fixtures carry no blame snippet.
            if blame.get("snippet") and blame.get("at"):
                ops.append(AddFact(
                    subject=blame_commit_id, predicate="blame_line",
                    value=f"{blame.get('file')}:{blame.get('line')}  {blame['snippet']}",
                    valid_from=blame["at"], observed_at=blame["at"], source=Source.GIT,
                    source_reliability=blame.get("reliability", 0.98)))

            es = raw.get("error_signature")
            es_hash = raw.get("error_signature_hash") or (es.get("signature_hash") if es else None)
            if es_hash:
                es_props = {"signature_hash": es_hash}
                if es:
                    es_props.update({
                        "exception_class": es.get("exception_class"),
                        "first_seen": es.get("first_seen"),
                        "file_line": f"{blame.get('file')}:{blame.get('line')}",
                    })
                ops.append(AddNode(type=NodeType.ERROR_SIGNATURE, props=es_props))
                es_id = registry.node_id(NodeType.ERROR_SIGNATURE, es_props)

                ev_ref = f"{blame.get('file')}:{blame.get('line')}"
                evidence = [EvidenceRef(kind="blame", ref=ev_ref, label=blame.get("snippet"))]
                ops.append(AddEdge(type=EdgeType.CAUSED_BY, src=es_id, dst=blame_commit_id,
                                   confidence_level=ConfidenceLevel.HIGH, evidence=evidence))

                hypothesis_id = raw.get("hypothesis_id")
                if hypothesis_id:
                    ops.append(AddEdge(type=EdgeType.CAUSED_BY, src=f"hyp:{hypothesis_id}",
                                       dst=blame_commit_id, confidence_level=ConfidenceLevel.HIGH,
                                       evidence=evidence))

        return ops
