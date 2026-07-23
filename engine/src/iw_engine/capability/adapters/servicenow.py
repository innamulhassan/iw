"""ServiceNow adapter — incidents, changes, CI, impact. Mirrors PrometheusAdapter's shape
(the REFERENCE template): a `provider`, an `intents` set, an `effect`, and a pure
`normalize(raw)` that folds ServiceNow's native field names (`number`,
`cmdb_ci.display_value`, `opened_at`, `u_release_tag`, ...) into typed Operations.

`find_recent_changes`/`query_change_log` both key off the same optional `changes` list —
an EMPTY list is a first-class shape (the no-change incident class), not an error: the
loop below simply produces zero ChangeEvent ops and the rest of the fold is untouched.
"""
from __future__ import annotations

from typing import ClassVar

from ...domain import registry
from ...domain.enums import Binding, ConfidenceLevel, EdgeType, Effect, NodeType, Source, Species
from ...domain.operations import AddAssertion, AddEdge, AddNode, Operation
from ..layer import CapabilityMeta


class ServiceNowAdapter:
    provider = "servicenow"
    intents = frozenset({
        "get_incident",
        "find_recent_changes",
        "get_ci",
        "list_related_incidents",
        "assess_impact",
        "ingest_alert",
        "query_change_log",
    })
    effect = Effect.READ    # default across the intents set
    # PER-INTENT override (part4-capability §1: "ingest_alert reclassified write"): ingesting
    # an alert CREATES a record on the vendor side — it is a write against ServiceNow, not a
    # read-and-fold, so it belongs behind the human-approval gate like every other write.
    # The six sibling reads are untouched.
    effects: ClassVar[dict[str, Effect]] = {"ingest_alert": Effect.WRITE}
    binding = Binding.MCP   # ServiceNow ships a first-party MCP server (Zurich)
    meta = CapabilityMeta(
        summary="The incident record, its changes, related incidents, and the affected CI identity",
        queries_by="incident_id", returns="incident, changes, CI + its tool identifiers")

    def normalize(self, raw: dict) -> list[Operation]:
        ops: list[Operation] = []
        inc_id: str | None = None
        inc_env = "prod"

        # ── get_incident ──────────────────────────────────────────────────────
        inc = raw.get("incident")
        if inc:
            inc_props = {
                "incident_id": inc["number"],
                "severity": inc.get("priority"),
                "commander": inc.get("assigned_to"),
                # M2: the incident's human-readable record (title/short_description/work_notes/
                # caller_id) folded onto the ORIGIN node so it stops being a bare id — included
                # only when ServiceNow returns them, so a thin fixture mints the exact same node.
                **{k: inc[k] for k in ("title", "short_description", "work_notes", "caller_id")
                   if inc.get(k) is not None},
            }
            ops.append(AddNode(type=NodeType.INCIDENT, props=inc_props))
            inc_id = registry.node_id(NodeType.INCIDENT, inc_props)
            at = inc["opened_at"]
            ops.append(AddAssertion(subject=inc_id, name="declared", species=Species.EVENT,
                                    occurred_at=at, observed_at=at,
                                    value={"state": inc.get("state")}, source=Source.SERVICENOW,
                                    source_native_name="declared"))
            inc_env = inc.get("env", inc_env)

            ci = inc.get("cmdb_ci") or {}
            if ci.get("display_value"):
                # resolve the CI's per-tool identifiers off the incident's CMDB CI — the identity
                # backbone that lets each later tool be queried by ITS OWN id (AppD by app_id,
                # git by repo, the platform by k8s_workload), not by the display name.
                svc_props = {"service_name": ci["display_value"], "env": inc_env,
                             **{k: ci[k] for k in ("app_id", "sys_id", "repo", "k8s_workload")
                                if ci.get(k)}}
                ops.append(AddNode(type=NodeType.SERVICE, props=svc_props))
                svc_id = registry.node_id(NodeType.SERVICE, svc_props)
                ops.append(AddEdge(type=EdgeType.AFFECTS, src=inc_id, dst=svc_id))

        # ── find_recent_changes / query_change_log (EMPTY list == no-change class) ──
        for ch in raw.get("changes", []):
            ch_props = {
                "change_id": ch["number"],
                "change_type": ch.get("type"),
                "target_ref": (ch.get("cmdb_ci") or {}).get("display_value"),
                "actor": ch.get("requested_by"),
                "ticket_id": ch["number"],
            }
            ops.append(AddNode(type=NodeType.CHANGE_EVENT, props=ch_props))
            ch_id = registry.node_id(NodeType.CHANGE_EVENT, ch_props)
            at = ch["start_date"]
            ops.append(AddAssertion(subject=ch_id, name="implemented", species=Species.EVENT,
                                    occurred_at=at, observed_at=at,
                                    value={"actor": ch.get("requested_by")},
                                    source=Source.SERVICENOW, source_native_name="implemented"))

            target = (ch.get("cmdb_ci") or {}).get("display_value")
            if target:
                tgt_props = {"service_name": target, "env": ch.get("env", inc_env)}
                ops.append(AddNode(type=NodeType.SERVICE, props=tgt_props))
                tgt_id = registry.node_id(NodeType.SERVICE, tgt_props)
                ops.append(AddEdge(type=EdgeType.CHANGED_BY, src=tgt_id, dst=ch_id))

            release_tag = ch.get("u_release_tag")
            if release_tag:
                if ch.get("type") == "feature-flag":
                    # a feature-flag change's release tag names the FLAG ("<key>@<pct>"),
                    # not a build artifact: discover the FeatureFlag node. It is edge-
                    # isolated in the domain model (the causal link is the hypothesis's
                    # root_candidate, never a typed edge) — but the NODE must exist or no
                    # telemetry can land on it (live retest 2026-07-22: with no discovery
                    # channel every prometheus flag fact rejected 'unknown subject').
                    flag_props = {"flag_key": release_tag.rsplit("@", 1)[0],
                                  "env": ch.get("env", inc_env)}
                    ops.append(AddNode(type=NodeType.FEATURE_FLAG, props=flag_props))
                else:
                    rel_props = {"release_id": release_tag}
                    ops.append(AddNode(type=NodeType.RELEASE, props=rel_props))
                    ops.append(AddEdge(type=EdgeType.INTRODUCED_BY, src=ch_id,
                                       dst=registry.node_id(NodeType.RELEASE, rel_props)))

            commit_sha = ch.get("u_commit_sha")
            if commit_sha:
                commit_props = {"sha": commit_sha}
                ops.append(AddNode(type=NodeType.CODE_COMMIT, props=commit_props))
                ops.append(AddEdge(type=EdgeType.INTRODUCED_BY, src=ch_id,
                                   dst=registry.node_id(NodeType.CODE_COMMIT, commit_props)))

        # ── get_ci ────────────────────────────────────────────────────────────
        ci = raw.get("ci")
        if ci:
            if ci.get("sys_class_name") == "cmdb_ci_service":
                props = {"service_name": ci.get("name"), "env": ci.get("env", inc_env)}
                ops.append(AddNode(type=NodeType.SERVICE, props=props))
            else:
                props = {"ci_id": ci.get("sys_id"), "class_hint": ci.get("sys_class_name"),
                         "name": ci.get("name")}
                ops.append(AddNode(type=NodeType.GENERIC_CI, props=props))

        # ── list_related_incidents ────────────────────────────────────────────
        # Fold each co-firing/similar prior into an Incident node and link it back to the
        # primary incident with a SIMILAR_TO (or RECURRENCE_OF) edge — the related prior is a
        # hypothesis prior ("N other apps reported the same in the same window"). The primary is
        # the get_incident block above (inc_id) or an explicit `primary_incident` id on the
        # payload (so the intent works standalone against an already-known incident node).
        primary_inc = inc_id
        if primary_inc is None and raw.get("primary_incident"):
            primary_inc = registry.node_id(NodeType.INCIDENT,
                                            {"incident_id": raw["primary_incident"]})
        for ri in raw.get("related_incidents", []):
            ri_props = {
                "incident_id": ri["number"],
                "severity": ri.get("priority"),
                "commander": ri.get("assigned_to"),
                # M2: the related prior's human record too (same conditional fold as get_incident),
                # so a co-firing/recurrence incident carries its own title/description when served.
                **{k: ri[k] for k in ("title", "short_description", "work_notes", "caller_id")
                   if ri.get(k) is not None},
            }
            ops.append(AddNode(type=NodeType.INCIDENT, props=ri_props))
            ri_id = registry.node_id(NodeType.INCIDENT, ri_props)
            at = ri.get("opened_at")
            if at:
                ops.append(AddAssertion(subject=ri_id, name="declared", species=Species.EVENT,
                                        occurred_at=at, observed_at=at,
                                        value={"affected_ci": ri.get("cmdb_ci")},
                                        source=Source.SERVICENOW, source_native_name="declared"))
            if primary_inc is not None and primary_inc != ri_id:
                # "recurrence" marks a true recurrence of the SAME incident; else a co-firing peer.
                etype = (EdgeType.RECURRENCE_OF if ri.get("relation") == "recurrence"
                         else EdgeType.SIMILAR_TO)
                ops.append(AddEdge(type=etype, src=primary_inc, dst=ri_id,
                                   confidence_level=ConfidenceLevel(ri.get("confidence", "med"))))

        # ── assess_impact ─────────────────────────────────────────────────────
        for svc in raw.get("impacted", []):
            props = {"service_name": svc["display_value"], "env": svc.get("env", inc_env)}
            ops.append(AddNode(type=NodeType.SERVICE, props=props))
            if inc_id:
                ops.append(AddEdge(type=EdgeType.AFFECTS, src=inc_id,
                                   dst=registry.node_id(NodeType.SERVICE, props)))

        # ── ingest_alert ──────────────────────────────────────────────────────
        al = raw.get("alert")
        if al:
            al_props = {"alert_id": al["id"]}
            ops.append(AddNode(type=NodeType.ALERT, props=al_props))
            al_id = registry.node_id(NodeType.ALERT, al_props)
            at = al["at"]
            ops.append(AddAssertion(subject=al_id, name="fired", species=Species.EVENT,
                                    occurred_at=at, observed_at=at,
                                    value={"state": al.get("state", "firing")},
                                    source=Source.SERVICENOW, source_native_name="fired"))
            if inc_id:
                ops.append(AddEdge(type=EdgeType.TRIGGERED_BY, src=inc_id, dst=al_id))
            svc_display = ((raw.get("incident") or {}).get("cmdb_ci") or {}).get("display_value")
            if svc_display:
                svc_props = {"service_name": svc_display, "env": inc_env}
                ops.append(AddNode(type=NodeType.SERVICE, props=svc_props))
                ops.append(AddEdge(type=EdgeType.FIRED_ON, src=al_id,
                                   dst=registry.node_id(NodeType.SERVICE, svc_props)))

        return ops
