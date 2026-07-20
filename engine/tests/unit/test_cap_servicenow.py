"""ServiceNow adapter unit test — normalize->ops folds cleanly through the reducer
(zero rejections = the adapter emits only registry-valid types). Two shapes: the full
fixture (incident, changes, ci, related incidents, impact, alert) and the EMPTY
change-list shape (the no-change incident class) — both must fold with zero rejections.
"""
from __future__ import annotations

from iw_engine.capability import CapabilityLayer
from iw_engine.capability.adapters.servicenow import ServiceNowAdapter
from iw_engine.domain import registry
from iw_engine.domain.enums import EdgeType, NodeType
from iw_engine.domain.node import Node
from iw_engine.domain.playbook import Tunables
from iw_engine.graph import Graph
from iw_engine.graph.reducer import materialize

RAW = {
    "incident": {
        "number": "INC0012345",
        "priority": "2 - High",
        "state": "In Progress",
        "assigned_to": "jdoe",
        "opened_at": "2026-07-19T14:00:00Z",
        "env": "prod",
        "cmdb_ci": {"value": "8a1b2c3d", "display_value": "payments-api"},
    },
    "changes": [
        {
            "number": "CHG0000456",
            "type": "standard",
            "cmdb_ci": {"display_value": "payments-api"},
            "requested_by": "jsmith",
            "start_date": "2026-07-19T13:50:00Z",
            "u_release_tag": "REL-2026.07.19-1",
        },
        {
            "number": "CHG0000457",
            "type": "emergency",
            "cmdb_ci": {"display_value": "payments-api"},
            "requested_by": "jsmith",
            "start_date": "2026-07-19T13:55:00Z",
            "u_commit_sha": "a1b2c3d4",
        },
    ],
    "ci": {"sys_id": "8a1b2c3d", "sys_class_name": "cmdb_ci_service",
           "name": "payments-api", "env": "prod"},
    "related_incidents": [
        {"number": "INC0012346", "priority": "3 - Moderate", "opened_at": "2026-07-19T14:10:00Z"},
    ],
    "impacted": [{"display_value": "checkout-api", "env": "prod"}],
    "alert": {"id": "EVT-9911", "at": "2026-07-19T13:58:00Z", "state": "firing"},
}

NO_CHANGE_RAW = {
    "incident": RAW["incident"],
    "changes": [],
}


def test_servicenow_normalize_folds_cleanly():
    layer = CapabilityLayer([ServiceNowAdapter()])
    ops, inv = layer.invoke("get_incident", RAW, allow_write=False)
    assert inv.provider == "servicenow" and not inv.blocked
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == [], mat.rejections   # adapter emits only registry-valid types

    inc_id = registry.node_id(NodeType.INCIDENT, {"incident_id": "INC0012345",
                                                   "severity": "2 - High", "commander": "jdoe"})
    svc_id = registry.node_id(NodeType.SERVICE, {"service_name": "payments-api", "env": "prod"})
    chg1_id = registry.node_id(NodeType.CHANGE_EVENT, {
        "change_id": "CHG0000456", "change_type": "standard", "target_ref": "payments-api",
        "actor": "jsmith", "ticket_id": "CHG0000456"})
    chg2_id = registry.node_id(NodeType.CHANGE_EVENT, {
        "change_id": "CHG0000457", "change_type": "emergency", "target_ref": "payments-api",
        "actor": "jsmith", "ticket_id": "CHG0000457"})
    rel_id = registry.node_id(NodeType.RELEASE, {"release_id": "REL-2026.07.19-1"})
    commit_id = registry.node_id(NodeType.CODE_COMMIT, {"sha": "a1b2c3d4"})
    related_id = registry.node_id(NodeType.INCIDENT, {"incident_id": "INC0012346",
                                                       "severity": "3 - Moderate", "commander": None})
    impacted_id = registry.node_id(NodeType.SERVICE, {"service_name": "checkout-api", "env": "prod"})
    alert_id = registry.node_id(NodeType.ALERT, {"alert_id": "EVT-9911"})

    node_ids = {n.id for n in mat.nodes}
    assert inc_id in node_ids
    assert svc_id in node_ids
    assert chg1_id in node_ids and chg2_id in node_ids
    assert rel_id in node_ids
    assert commit_id in node_ids
    assert related_id in node_ids
    assert impacted_id in node_ids
    assert alert_id in node_ids
    # get_ci: sys_class_name == cmdb_ci_service maps to a SERVICE node (same identity as above)
    assert any(n.type == NodeType.SERVICE and n.id == svc_id for n in mat.nodes)

    assert any(e.type == EdgeType.AFFECTS and e.src == inc_id and e.dst == svc_id for e in mat.edges)
    assert any(e.type == EdgeType.AFFECTS and e.src == inc_id and e.dst == impacted_id
               for e in mat.edges)
    assert any(e.type == EdgeType.CHANGED_BY and e.src == svc_id and e.dst == chg1_id
               for e in mat.edges)
    assert any(e.type == EdgeType.CHANGED_BY and e.src == svc_id and e.dst == chg2_id
               for e in mat.edges)
    assert any(e.type == EdgeType.INTRODUCED_BY and e.src == chg1_id and e.dst == rel_id
               for e in mat.edges)
    assert any(e.type == EdgeType.INTRODUCED_BY and e.src == chg2_id and e.dst == commit_id
               for e in mat.edges)
    assert any(e.type == EdgeType.TRIGGERED_BY and e.src == inc_id and e.dst == alert_id
               for e in mat.edges)
    assert any(e.type == EdgeType.FIRED_ON and e.src == alert_id and e.dst == svc_id
               for e in mat.edges)

    assert any(ev.entity_ref == inc_id and ev.type == "declared" for ev in mat.events)
    assert any(ev.entity_ref == chg1_id and ev.type == "implemented" for ev in mat.events)
    assert any(ev.entity_ref == alert_id and ev.type == "fired" for ev in mat.events)

    # a related prior is a hypothesis prior: it lands as an Incident->Incident SIMILAR_TO edge
    # off the primary incident, carrying a confidence (requires_confidence edge, folds cleanly)
    sim = [e for e in mat.edges if e.type == EdgeType.SIMILAR_TO]
    assert any(e.src == inc_id and e.dst == related_id for e in sim)
    assert all(e.confidence is not None for e in sim)


def test_servicenow_related_incidents_recurrence_edge():
    """list_related_incidents standalone: an explicit `primary_incident` + a `relation=recurrence`
    entry folds a directed RECURRENCE_OF edge (the primary node need not be in the same payload —
    it is already in the graph from a prior phase), while a plain peer folds SIMILAR_TO."""
    layer = CapabilityLayer([ServiceNowAdapter()])
    raw = {
        "primary_incident": "INC-7702",
        "related_incidents": [
            {"number": "INC-7699", "priority": "2 - High", "relation": "recurrence",
             "opened_at": "2026-04-19T09:00:00Z", "confidence": "high"},
            {"number": "INC-8000", "priority": "3 - Moderate", "opened_at": "2026-07-19T09:05:00Z"},
        ],
    }
    ops, inv = layer.invoke("list_related_incidents", raw, allow_write=False)
    assert not inv.blocked

    # the primary incident already exists in the graph (created in a prior phase) — seed it so
    # the reducer resolves the edge source, then fold the related-incident ops against it
    primary = registry.node_id(NodeType.INCIDENT, {"incident_id": "INC-7702"})
    g = Graph()
    g.upsert_node(Node(id=primary, type=NodeType.INCIDENT, props={"incident_id": "INC-7702"},
                       created_by=1))
    mat = materialize(ops, 2, g, Tunables())
    assert mat.rejections == [], mat.rejections

    prior = registry.node_id(NodeType.INCIDENT, {"incident_id": "INC-7699"})
    peer = registry.node_id(NodeType.INCIDENT, {"incident_id": "INC-8000"})
    assert any(e.type == EdgeType.RECURRENCE_OF and e.src == primary and e.dst == prior
               for e in mat.edges)
    assert any(e.type == EdgeType.SIMILAR_TO and e.src == primary and e.dst == peer
               for e in mat.edges)


def test_servicenow_empty_change_list_is_the_no_change_incident_class():
    """find_recent_changes / query_change_log returning [] must fold cleanly — the
    no-change incident class is a first-class, zero-rejection shape, not an error."""
    layer = CapabilityLayer([ServiceNowAdapter()])
    ops, inv = layer.invoke("find_recent_changes", NO_CHANGE_RAW, allow_write=False)
    assert not inv.blocked
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == [], mat.rejections

    assert not any(n.type == NodeType.CHANGE_EVENT for n in mat.nodes)
    assert not any(e.type in (EdgeType.CHANGED_BY, EdgeType.INTRODUCED_BY) for e in mat.edges)
    # the incident + its CI still fold even with zero changes
    inc_id = registry.node_id(NodeType.INCIDENT, {"incident_id": "INC0012345",
                                                   "severity": "2 - High", "commander": "jdoe"})
    assert any(n.id == inc_id for n in mat.nodes)
