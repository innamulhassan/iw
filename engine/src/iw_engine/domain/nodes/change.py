"""L5 — change & supply chain, plus change-adjacent-but-not-in-CMDB nodes
(DESIGN §3 / §2.1 R-G6 / DESIGN-INPUT §B.2).

CodeCommit / BuildArtifact / Release / ChangeEvent / PullRequest form the supply-chain
spine `Commit -> Artifact -> Release -> running workload` that change-analysis (the
cheapest strong signal) walks. Certificate / FeatureFlag / ExternalService are
change-adjacent-but-not-in-CMDB (R-G6) — they power no-change-*looking* incidents that
are actually caused by an expiry, a flag flip, or a vendor outage.
"""
from __future__ import annotations

from ..enums import NodeType
from ..spec import NodeSpec

SPECS: tuple[NodeSpec, ...] = (
    NodeSpec(
        type=NodeType.CODE_COMMIT,
        tier="L5",
        identity_keys=("sha",),
        static_props=("sha", "repo", "author", "parent_sha", "authored_at"),
        fact_predicates=(),
        event_types=(),
        discriminator=(
            "An immutable commit (identity = sha) — the terminal node a blame/diff "
            "join resolves a CAUSED_BY chain to; not itself a running artifact."
        ),
    ),
    NodeSpec(
        type=NodeType.BUILD_ARTIFACT,
        tier="L5",
        identity_keys=("digest",),
        static_props=("digest", "repo", "build_number"),
        fact_predicates=(),
        event_types=("built", "promoted"),
        discriminator=(
            "An immutable built artifact identified by digest, BUILT_FROM a CodeCommit "
            "and wrapped RELEASED_AS a Release. Distinguishes from Release (the "
            "promotable/deployable wrapper) and CodeCommit (the source)."
        ),
    ),
    NodeSpec(
        type=NodeType.RELEASE,
        tier="L5",
        identity_keys=("release_id",),
        static_props=("release_id", "version"),
        fact_predicates=(),
        event_types=("released", "rolled_back"),
        discriminator=(
            "A named, deployable version wrapping a BuildArtifact, DEPLOYED_AS a "
            "running Deployment/Pod — the unit `find_recent_changes` correlates "
            "against symptom onset."
        ),
    ),
    NodeSpec(
        type=NodeType.CHANGE_EVENT,
        tier="L5",
        identity_keys=("change_id",),
        static_props=("change_id", "change_type", "target_ref", "actor", "ticket_id"),
        fact_predicates=(),
        event_types=("opened", "implemented", "closed"),
        discriminator=(
            "A first-class ServiceNow-style change record (deploy/config/infra/"
            "db-migration) — essentially a first-class event, the node the RCA "
            "CHANGED_BY edge and HYPOTHESIZE's change-first seeding key off. Covers "
            "config/infra/db changes that a Release wouldn't (a Release is "
            "specifically a deployable code version)."
        ),
    ),
    NodeSpec(
        type=NodeType.PULL_REQUEST,
        tier="L5",
        identity_keys=("repo", "pr_id"),
        static_props=("pr_id", "repo", "author", "merged_sha"),
        fact_predicates=(),
        event_types=("opened", "merged", "closed"),
        discriminator=(
            "The review/merge record for one or more CodeCommits — use when the "
            "fixture surfaces PR metadata (get_pr_for_commit) distinct from the raw "
            "commit, e.g. 'PR removed a ConfigMap key.'"
        ),
    ),
    NodeSpec(
        type=NodeType.CERTIFICATE,
        tier="L5",
        identity_keys=("cert_id",),
        static_props=("cert_id", "subject", "issuer"),
        fact_predicates=("days_to_expiry",),
        event_types=("issued", "renewed", "expired"),
        discriminator=(
            "A TLS/cert credential (R-G6, change-adjacent-but-not-in-CMDB) whose "
            "`expired` event powers no-change-*looking* incidents actually caused by "
            "expiry — no ChangeEvent/ticket will exist for it."
        ),
    ),
    NodeSpec(
        type=NodeType.FEATURE_FLAG,
        tier="L5",
        identity_keys=("flag_key", "env"),
        static_props=("flag_key", "env"),
        fact_predicates=("enabled", "rollout_percentage"),
        event_types=("flipped",),
        discriminator=(
            "A flag whose `flipped` event (R-G6) is a change usually invisible to "
            "CMDB/git — a common modern incident trigger. Attach a ChangeEvent "
            "additionally only if the flip was also ticketed."
        ),
    ),
    NodeSpec(
        type=NodeType.EXTERNAL_SERVICE,
        tier="L5",
        identity_keys=("service_name",),
        static_props=("service_name", "vendor"),
        # exit-call RED (§C3) so a trace-discovered backend surfaces the same shape as a Service
        fact_predicates=("availability", "latency_p99", "call_rate", "error_rate"),
        event_types=("outage_started", "outage_resolved"),
        discriminator=(
            "A third-party/SaaS dependency outside CMDB (R-G6) — a Service DEPENDS_ON it, or "
            "CALLS it as a discovered exit-call backend. Vendor-outage + no-change incidents."
        ),
    ),
)
