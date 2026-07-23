"""L6 — signals/investigation tier, plus the escape hatch (DESIGN §3 / §2.1 R-G2/R-G4).

Alert (raw upstream signal) / Incident (the wrapping record) / Anomaly (THE canonical
symptom node, R-G4) / ErrorSignature (deduped exception cluster, R-G1) /
BusinessTransaction (AppD BT) / Hypothesis (graph-node projection of the hypothesis store record;
root cause IS a confirmed Hypothesis, R-G2 — there is no separate RootCause/Remediation
node). GenericCI is the single escape hatch (mirrors ServiceNow base `cmdb_ci`).
"""
from __future__ import annotations

from ..enums import NodeType
from ..spec import NodeSpec

SPECS: tuple[NodeSpec, ...] = (
    NodeSpec(
        type=NodeType.ALERT,
        tier="L6",
        identity_keys=("alert_id",),
        static_props=("alert_id", "rule", "severity", "metric", "threshold"),
        fact_predicates=(),
        event_types=("fired", "ack", "resolved", "re_fired", "flapping"),
        discriminator=(
            "A monitoring-rule firing (Prometheus/AppD), FIRED_ON the entity it "
            "concerns. Distinguishes from Anomaly (the normalized symptom FRAME "
            "produces): an Alert may seed the investigation but is not itself the "
            "canonical symptom node."
        ),
    ),
    NodeSpec(
        type=NodeType.INCIDENT,
        tier="L6",
        identity_keys=("incident_id",),
        # M2: the incident's human record props (title/short_description/work_notes/caller_id)
        # documented for doc-parity beside id/severity/commander — static_props is doc-only
        # (never reducer-enforced; props are stored verbatim), so this is purely additive.
        static_props=("incident_id", "severity", "commander",
                      "title", "short_description", "work_notes", "caller_id"),
        fact_predicates=(),
        event_types=("declared", "mitigated", "resolved"),
        discriminator=(
            "The ServiceNow-style incident record wrapping the investigation; AFFECTS "
            "points from it to the impacted CI. Distinct from Anomaly (the graph "
            "symptom node) and Hypothesis (the causal explanation)."
        ),
    ),
    NodeSpec(
        type=NodeType.ANOMALY,
        tier="L6",
        identity_keys=("anomaly_id",),
        static_props=("anomaly_id", "metric", "onset_ref"),
        fact_predicates=("onset_value", "severity_score"),
        event_types=("detected", "cleared"),
        discriminator=(
            "THE canonical symptom node (R-G4) — FRAME's output, carrying the onset "
            "fact + AFFECTS->(Service|ApiEndpoint). The whole method operates over the "
            "subgraph reachable from this node; use it, never a bare fact, for 'the "
            "thing that's wrong.'"
        ),
    ),
    NodeSpec(
        type=NodeType.ERROR_SIGNATURE,
        tier="L6",
        identity_keys=("signature_hash",),
        static_props=("signature_hash", "exception_class", "first_seen", "file_line"),
        fact_predicates=("count", "last_seen"),
        event_types=(),
        discriminator=(
            "A deduplicated recurring error/exception pattern EMITTED by a Service/Pod "
            "(Splunk search_errors/error_signature_topk) — added per R-G1 to close the "
            "capability-fold registry gap. The terminal node a CAUSED_BY edge to a "
            "CodeCommit resolves via blame; distinguishes from Anomaly (RED-level "
            "symptom) by being a specific log-level exception cluster."
        ),
    ),
    NodeSpec(
        type=NodeType.BUSINESS_TRANSACTION,
        tier="L6",
        identity_keys=("service_name", "bt_name"),
        static_props=("bt_name", "service_name"),
        fact_predicates=("art_p95", "epm", "delta_vs_baseline"),
        event_types=(),
        discriminator=(
            "An AppDynamics business transaction — a named user-facing operation with "
            "its own RT/EPM baseline, finer-grained than Service RED. Use when the "
            "fixture is AppD BT health rather than raw Service metrics."
        ),
    ),
    NodeSpec(
        type=NodeType.HYPOTHESIS,
        tier="L6",
        identity_keys=("hid",),
        static_props=("hid", "statement"),
        fact_predicates=(),
        event_types=("created", "confirmed", "refuted", "superseded"),
        discriminator=(
            "The graph-node projection of a hypothesis store Hypothesis record (see "
            "domain/hypothesis.py for status/confidence/causal_chain, which live on "
            "the hypothesis store, not as node facts) — exists so the SUPPORTS/REFUTES/CAUSED_BY/"
            "REMEDIATED_BY edges have a NodeId to attach to. The root cause IS a "
            "Hypothesis{status=confirmed} (R-G2) — there is no separate RootCause/"
            "Remediation node."
        ),
    ),
    NodeSpec(
        type=NodeType.GENERIC_CI,
        tier="signal",
        identity_keys=("ci_id",),
        static_props=("ci_id", "class_hint", "name"),
        fact_predicates=(),
        event_types=(),
        discriminator=(
            "The single escape hatch (mirrors ServiceNow base `cmdb_ci`) for any CI "
            "whose shape doesn't map to a more specific NodeType; `class_hint` records "
            "what it actually is. Repeated identical `class_hint` values are the "
            "registry-evolution signal (P2-5) that a real NodeType is missing — never "
            "mint a new label here, only use this member."
        ),
    ),
)
