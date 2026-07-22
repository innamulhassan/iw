"""catalog.py — render the closed domain vocabulary + the playbook's per-phase intent
budget into a compact, LLM-facing grammar. This is the SCHEMA the live planner hands the
model: the only NODE types (discriminator + identity keys + key fact predicates), the only
EDGE types (legal src->dst pairs), and the phase-scoped allowed intents. It is derived
purely from the registry (NODE_SPECS / EDGE_SPECS) + the playbook, so it can never drift
from what the reducer will actually accept — an off-catalog op the LLM emits is exactly an
op the reducer will reject.

`render_catalog(registry, playbook)` returns the grammar string; `render_tools(adapters)`
renders the concrete capability (tool) intents the CapabilityLayer can resolve — the real
verbs behind the playbook's abstract `allowed_intents`.
"""
from __future__ import annotations

from . import dictionary
from .edges import EDGE_SPECS
from .nodes import NODE_SPECS
from .playbook import Playbook

# One-line docs for the concrete tool intents (what each returns), so the planner can pick
# the right verb. Neutral tool documentation — describes the capability, not any answer.
INTENT_HINTS: dict[str, str] = {
    # servicenow
    "get_incident": "the incident record + the affected CI",
    "find_recent_changes": "recent change tickets (CHG-*) near the incident window -> change_event "
                           "nodes (+ commit/release if the ticket names one)",
    "query_change_log": "change history for a CI",
    "get_ci": "a single CI record",
    "list_related_incidents": "other incidents on the same CI",
    "assess_impact": "the impacted-service blast radius",
    "ingest_alert": "normalize an inbound alert into the graph",
    # cmdb
    "get_dependencies": "declared topology: a CI's downstream dependencies (DEPENDS_ON/RUNS_ON, origin=declared)",
    "impact_analysis": "declared topology: upstream blast radius of a CI",
    "seed_graph": "declared topology around the subject",
    "get_ci_class": "a CI's class",
    "find_ci_by_attr": "CIs matching an attribute",
    # prometheus
    "active_alerts": "firing alerts for a service -> alert nodes + fired events",
    "instant_query": "a point-in-time metric value (e.g. connection-pool util, active_connections, retrans_segs)",
    "range_query": "a metric over a time range (e.g. probe_success flapping)",
    "fetch_metrics": "RED metrics (errors/latency/traffic) for a service",
    # appd
    "bt_health": "business-transaction health: art_p95 / epm / delta_vs_baseline",
    "get_snapshots": "transaction snapshots incl. exit-call boundaries (JDBC->db, HTTP->service) "
                     "— pins where the time goes",
    "healthrule_violations": "AppD health-rule violations for a service (an EMPTY result is a "
                             "clean callee — first-class null evidence)",
    "flowmap": "the observed call flowmap — discovers callees (downstream services)",
    "fetch_traces": "distributed traces for a BT/service",
    # splunk
    "search_errors": "raw error-log search",
    "error_signature_topk": "top deduped exception signatures -> error_signature nodes + count fact",
    "search_fw_denies": "firewall deny events -> firewall_rule deny_count",
    "transaction_trace": "a single transaction trace",
    "fetch_logs": "service logs",
    # git
    "get_commit": "commit metadata",
    "diff_range": "diff stats for a change/commit (lines_added/deleted on the commit)",
    "read_diff": "read a commit diff",
    "get_pr_for_commit": "the PR that merged a commit",
    "blame": "blame a file:line to the commit that introduced it -> CAUSED_BY error_signature->commit",
    # ocp
    "rollout_status": "deployment rollout status",
    "pod_status": "pod readiness / restart counts",
    "events": "kubernetes events",
    "pod_logs": "pod logs",
    # artifactory
    "get_artifact_by_digest": "build artifact by digest",
    "get_build": "CI build record",
    "list_promotions": "artifact promotion history",
    "aql_search": "artifact query",
}


def render_nodes() -> str:
    lines: list[str] = []
    for ntype, spec in sorted(NODE_SPECS.items(), key=lambda kv: (kv[1].tier, kv[0].value)):
        idk = ",".join(spec.identity_keys)
        # P2 §2.3: the legal fact names are the dictionary's CANONICAL spellings for this type
        # (a derived view of `applies_to`), not the per-type `fact_predicates` native list — the
        # model emits canonical names; the engine sets `source_native_name` for tool data.
        names = dictionary.fact_names_for(ntype)
        preds = ",".join(names) if names else "(none)"
        disc = (spec.discriminator or "").replace("\n", " ").split(". ")[0].strip()
        if len(disc) > 90:
            disc = disc[:87] + "..."
        lines.append(f"  {ntype.value} [{spec.tier}]  id_keys=({idk})  facts=[{preds}]"
                     + (f"\n     - {disc}" if disc else ""))
    return "\n".join(lines)


def render_edges() -> str:
    lines: list[str] = []
    for etype, spec in sorted(EDGE_SPECS.items(), key=lambda kv: kv[0].value):
        pairs = spec.allowed
        shown = "; ".join(f"{s.value}->{d.value}" for s, d in pairs[:5])
        more = "" if len(pairs) <= 5 else f"  (+{len(pairs) - 5} more legal pairs)"
        conf = "  <requires confidence_level+evidence>" if spec.requires_confidence else ""
        lines.append(f"  {etype.value}: {shown}{more}{conf}")
    return "\n".join(lines)


def render_phases(pb: Playbook) -> str:
    lines: list[str] = []
    for p in pb.phases:
        gate = []
        if p.gate.min_facts:
            gate.append(f"min_facts>={p.gate.min_facts}")
        if p.gate.require_confidence_gate:
            gate.append(f"leading-hypothesis confidence>={pb.tunables.confidence_gate}")
        if p.gate.require_refutation:
            gate.append("a rival ruled out OR the leader challenged with refuting evidence")
        gtxt = ("\n     GATE (to ADVANCE): " + "; ".join(gate)) if gate else ""
        nxt = ", ".join(f"{k}->{v.value}" for k, v in p.on_verdict.items()) or "(terminal)"
        lines.append(
            f"  {p.id.value}: {p.goal}\n"
            f"     allowed_intents (abstract action budget): {', '.join(p.allowed_intents)}\n"
            f"     produces_required: {p.produces_required or '(none)'}"
            f"{gtxt}\n     routes: {nxt}")
    return "\n".join(lines)


def render_catalog(registry, playbook: Playbook) -> str:
    """The full LLM-facing grammar: node types, edge types, per-phase intents, node-id rule."""
    node_types = sorted(t.value for t in registry.all_node_types())
    return f"""\
# INVESTIGATION GRAMMAR (closed vocabulary — you may ONLY use members below)

## NODE TYPES  (pick a member; never invent a label; `generic_ci` is the sole escape hatch)
{render_nodes()}

## EDGE TYPES  (legal (src_type -> dst_type) pairs; an illegal triple is REJECTED)
{render_edges()}

## NODE-ID CONVENTION (how to reference a node in a fact/edge/hypothesis)
  id = "<type>:" + the node's identity_key values, slugged (lowercased, spaces/'/'->'-'),
       joined by "|".  e.g. change_event with change_id="CHG-9" -> "change_event:chg-9";
       service with service_name="orders-api", env="prod" -> "service:orders-api|prod";
       code_commit with sha="abc123" -> "code_commit:abc123".
  ALWAYS copy an exact id verbatim from the CURRENT GRAPH slice when the node already exists.

## PHASES (you are driving ONE phase per turn; the engine routes on your verdict)
{render_phases(playbook)}

Total: {len(node_types)} node types, {len(EDGE_SPECS)} edge types."""


def render_tools(adapters, *, include_writes: bool = False) -> str:
    """The concrete capabilities the layer can resolve — rendered FROM each capability's own
    `meta` (its one-line purpose + the identifier it queries by). Nothing tool-specific is
    hardcoded here: adding a capability makes the reasoner aware of it, and its `queries_by`
    field tells the reasoner which resolved identifier to pass. `include_writes` surfaces WRITE
    tools (labelled human-gated) so a live planner can propose a remediation in REMEDIATE."""
    def block(a) -> str:
        meta = getattr(a, "meta", None)
        desc = f" — {meta.summary} · queries by `{meta.queries_by}`" if meta else ""
        return f"  {a.provider}{desc}\n      intents: {'  '.join(sorted(a.intents))}"

    reads = [block(a) for a in sorted(adapters, key=lambda x: x.provider) if a.effect.value != "write"]
    writes = [block(a).replace(f"  {a.provider}", f"  {a.provider} [WRITE — human-gated]", 1)
              for a in sorted(adapters, key=lambda x: x.provider)
              if a.effect.value == "write" and include_writes]
    return (
        "## AVAILABLE TOOLS — grouped by capability. Emit the exact intent names in `calls`.\n"
        "# ROUTING: each capability names the id it 'queries by'. Resolve that id off the target\n"
        "#   CI — the incident's Service carries app_id / repo / k8s_workload / sys_id, resolved\n"
        "#   from the incident record — and pass it in the call params. Do NOT reuse the display\n"
        "#   name for a tool that queries by a different id (AppD queries by app_id, git by repo,\n"
        "#   the platform by k8s_workload).\n"
        + "\n".join(reads + writes))


def tool_intents(adapters, *, include_writes: bool = False) -> set[str]:
    """The set of resolvable intents used to reject off-catalog `calls` — read-only by default;
    `include_writes` also admits WRITE intents so the LIVE planner's remediation call is legal."""
    return {i for a in adapters if include_writes or a.effect.value != "write" for i in a.intents}
