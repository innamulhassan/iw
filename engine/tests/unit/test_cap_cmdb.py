"""CMDB adapter test — the topology-backbone fold. Follows test_capability.py's pattern:
invoke via CapabilityLayer, materialize, assert ZERO reducer rejections (the adapter
emits only registry-valid types) and spot-check the expected typed nodes/edges."""
from __future__ import annotations

from iw_engine.capability import CapabilityLayer
from iw_engine.capability.adapters.cmdb import CmdbAdapter
from iw_engine.domain import registry
from iw_engine.domain.enums import EdgeType, NodeType, Origin
from iw_engine.domain.playbook import Tunables
from iw_engine.graph import Graph
from iw_engine.graph.reducer import materialize

RAW = {
    "env": "prod",
    "dependencies": [
        {"parent": "payments-api", "parent_type": "cmdb_ci_service",
         "child": "payments-db", "child_type": "cmdb_ci_database",
         "rel_type": "Depends on::Used by"},
        {"parent": "payments-api", "parent_type": "cmdb_ci_service",
         "child": "payments-events", "child_type": "cmdb_ci_msgqueue",
         "rel_type": "Depends on::Used by"},
        {"parent": "payments-api", "parent_type": "cmdb_ci_service",
         "child": "ledger-service", "child_type": "cmdb_ci_service",
         "rel_type": "Depends on::Used by"},
        {"parent": "payments-db", "parent_type": "cmdb_ci_database",
         "child": "db-host-07.prod.internal", "child_type": "cmdb_ci_server",
         "rel_type": "Runs on::Hosts"},
        {"parent": "payments-lb", "parent_type": "cmdb_ci_lb",
         "child": "lb-host-02.prod.internal", "child_type": "cmdb_ci_server",
         "rel_type": "Runs on::Hosts"},
        {"parent": "nightly-recon-job", "parent_type": "cmdb_ci_batch_job",
         "child": "batch-host-03.prod.internal", "child_type": "cmdb_ci_server",
         "rel_type": "Runs on::Runs"},
        {"parent": "payments-lb", "parent_type": "cmdb_ci_lb",
         "child": "prod-vlan-140", "child_type": "cmdb_ci_network_segment",
         "rel_type": "Connects to::Connected by"},
        # unrecognised sys_class_name -> ConfigItem escape valve; "depends on" has no
        # legal (SERVICE, ..., CONFIG_ITEM) triple in the registry, so the edge is
        # dropped (not forced) while the node is still folded.
        {"parent": "payments-api", "parent_type": "cmdb_ci_service",
         "child": "legacy-mainframe-gateway", "child_type": "cmdb_ci_unclassified",
         "rel_type": "Depends on::Used by"},
    ],
    "cis": [
        {"name": "payments-api", "sys_class_name": "cmdb_ci_service"},
        {"name": "payments-db", "sys_class_name": "cmdb_ci_database"},
    ],
    "ci_attrs": {
        "payments-db": {"engine": "postgresql", "ha_role": "primary",
                        "endpoint": "payments-db.prod.internal:5432"},
        "db-host-07.prod.internal": {"asset_id": "AST-0007", "cpu_cores": 16,
                                      "mem_gb": 64, "region": "us-east-1"},
        "prod-vlan-140": {"cidr": "10.20.140.0/24", "vlan": 140},
    },
}


def test_cmdb_normalize_folds_cleanly():
    layer = CapabilityLayer([CmdbAdapter()])
    ops, inv = layer.invoke("get_dependencies", RAW, allow_write=False)
    assert inv.provider == "cmdb" and not inv.blocked
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == [], mat.rejections           # adapter emits only registry-valid types

    node_ids = {n.id for n in mat.nodes}
    edges = {(e.type, e.src, e.dst) for e in mat.edges}

    svc = registry.node_id(NodeType.SERVICE, {"service_name": "payments-api", "env": "prod"})
    db = registry.node_id(NodeType.DATABASE, {"db_id": "payments-db"})
    mq = registry.node_id(NodeType.MESSAGE_QUEUE, {"topic_id": "payments-events"})
    ledger = registry.node_id(NodeType.SERVICE, {"service_name": "ledger-service", "env": "prod"})
    db_host = registry.node_id(NodeType.HOST, {"fqdn": "db-host-07.prod.internal"})
    lb = registry.node_id(NodeType.LOAD_BALANCER, {"lb_id": "payments-lb"})
    lb_host = registry.node_id(NodeType.HOST, {"fqdn": "lb-host-02.prod.internal"})
    batch = registry.node_id(
        NodeType.BATCH_JOB, {"job_name": "nightly-recon-job", "schedule_id": "adhoc"})
    batch_host = registry.node_id(NodeType.HOST, {"fqdn": "batch-host-03.prod.internal"})
    vlan = registry.node_id(NodeType.NETWORK_SEGMENT, {"segment_id": "prod-vlan-140"})
    generic_ci = registry.node_id(NodeType.CONFIG_ITEM, {"ci_id": "legacy-mainframe-gateway"})

    # typed CI nodes per sys_class_name dispatch
    expected_nodes = (
        svc, db, mq, ledger, db_host, lb, lb_host, batch, batch_host, vlan, generic_ci)
    for expected in expected_nodes:
        assert expected in node_ids, expected

    # enrichment attrs folded onto the right node's props
    db_node = next(n for n in mat.nodes if n.id == db)
    assert db_node.props["engine"] == "postgresql" and db_node.props["ha_role"] == "primary"
    host_node = next(n for n in mat.nodes if n.id == db_host)
    assert host_node.props["region"] == "us-east-1" and host_node.props["cpu_cores"] == 16

    # rel_type -> registry-legal EdgeType, all declared-origin
    assert (EdgeType.DEPENDS_ON, svc, db) in edges
    assert (EdgeType.DEPENDS_ON, svc, mq) in edges
    assert (EdgeType.DEPENDS_ON, svc, ledger) in edges
    assert (EdgeType.HOSTED_ON, db, db_host) in edges       # DB->Host: HOSTED_ON, not RUNS_ON
    assert (EdgeType.HOSTED_ON, lb, lb_host) in edges
    assert (EdgeType.RUNS_ON, batch, batch_host) in edges   # BatchJob->Host is a legal RUNS_ON pair
    assert (EdgeType.CONNECTS_TO, lb, vlan) in edges
    assert all(e.origin == Origin.DECLARED for e in mat.edges)

    # the unmapped sys_class_name -> no legal DEPENDS_ON(SERVICE, CONFIG_ITEM) triple:
    # node still folds (escape valve), edge is dropped rather than forced illegally.
    assert not any(e.dst == generic_ci for e in mat.edges)


def test_cmdb_bare_ci_records_no_edges():
    """get_ci_class / find_ci_by_attr shape: bare `cis` records, no `dependencies`."""
    layer = CapabilityLayer([CmdbAdapter()])
    raw = {"cis": [{"name": "payments-api", "sys_class_name": "cmdb_ci_service"}]}
    ops, inv = layer.invoke("get_ci_class", raw, allow_write=False)
    assert not inv.blocked
    mat = materialize(ops, 1, Graph(), Tunables())
    assert mat.rejections == []
    svc = registry.node_id(NodeType.SERVICE, {"service_name": "payments-api", "env": "prod"})
    assert any(n.id == svc for n in mat.nodes)
    assert mat.edges == []
