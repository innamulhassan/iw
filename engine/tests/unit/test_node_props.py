"""P6 step 2 (P1a decision 3): node props are DECLARED-channel identity+descriptor assertions
in the ONE collection; the node's props dict is the materialized read view over them, and each
prop carries REAL per-assertion provenance (the declaring source + seq, threaded from
AddNode.source via the reducer). Merge semantics are byte-preserving with the dict era —
the golden suite is the end-to-end proof, these are the seam-level proofs.
"""
from __future__ import annotations

from datetime import UTC, datetime

from iw_engine.domain.enums import Channel, NodeType, Source, Species
from iw_engine.domain.node import Node
from iw_engine.domain.operations import AddNode
from iw_engine.domain.playbook import Tunables
from iw_engine.graph import Graph
from iw_engine.graph.reducer import materialize

T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
SID = "service:payments-api|prod"


def _svc(props=None, source=None, seq=1) -> Node:
    return Node(id=SID, type=NodeType.SERVICE,
                props=props or {"service_name": "payments-api", "env": "prod"},
                source=source, created_by=seq)


# ── props become DECLARED assertions with real provenance ─────────────────────
def test_props_land_as_declared_assertions_with_species_split():
    g = Graph()
    g.upsert_node(_svc({"service_name": "payments-api", "env": "prod", "tier": "gold"},
                       source=Source.CMDB))
    by_name = {a.name: a for a in g.declared_of(SID)}
    assert set(by_name) == {"service_name", "env", "tier"}
    # identity keys → IDENTITY species; the rest → DESCRIPTOR (decision 2's split)
    assert by_name["service_name"].species is Species.IDENTITY
    assert by_name["env"].species is Species.IDENTITY
    assert by_name["tier"].species is Species.DESCRIPTOR
    for a in by_name.values():
        assert a.channel is Channel.DECLARED
        assert a.source is Source.CMDB               # REAL provenance, per prop
    # the node's props dict is the view over them
    assert g.node(SID).props == {"service_name": "payments-api", "env": "prod", "tier": "gold"}


def test_prop_assertions_never_enter_the_facts_view():
    g = Graph()
    g.upsert_node(_svc(source=Source.CMDB))
    assert g.facts == {} and g.events == {}
    assert len(g.declared_of(SID)) == 2


def test_merge_semantics_are_dict_era_exact():
    """First mint keeps None-valued keys; a later upsert's non-None values win, its None
    values never override; unchanged values keep the FIRST declaration's provenance."""
    g = Graph()
    g.upsert_node(_svc({"service_name": "payments-api", "env": "prod", "owner": None},
                       source=Source.CMDB, seq=1))
    assert g.node(SID).props == {"service_name": "payments-api", "env": "prod", "owner": None}

    g.upsert_node(_svc({"service_name": "payments-api", "env": "prod",
                        "owner": "team-payments", "tier": None}, source=Source.SERVICENOW, seq=3))
    # owner: None → real value (new wins); tier: None on merge → never lands
    assert g.node(SID).props == {"service_name": "payments-api", "env": "prod",
                                 "owner": "team-payments"}
    by_name = {a.name: a for a in g.declared_of(SID)}
    assert by_name["owner"].source is Source.SERVICENOW and by_name["owner"].created_by == 3
    # unchanged values keep the first declarer (write-once flavor)
    assert by_name["service_name"].source is Source.CMDB and by_name["service_name"].created_by == 1


def test_unsourced_arrival_attributes_to_engine():
    g = Graph()
    g.upsert_node(_svc())                            # planner/engine-authored — no source
    assert all(a.source is Source.ENGINE for a in g.declared_of(SID))


def test_datetime_and_none_prop_values_survive_exactly():
    g = Graph()
    g.upsert_node(Node(id="change_event:CHG-7", type=NodeType.CHANGE_EVENT,
                       props={"change_id": "CHG-7", "at": T0, "note": None},
                       source=Source.SERVICENOW, created_by=1))
    props = g.node("change_event:CHG-7").props
    assert props["at"] == T0 and isinstance(props["at"], datetime)
    assert props["note"] is None


# ── the reducer threads AddNode.source onto the record ────────────────────────
def test_reducer_threads_addnode_source_to_per_prop_provenance():
    g = Graph()
    mat = materialize([AddNode(type=NodeType.SERVICE,
                               props={"service_name": "payments-api", "env": "prod"},
                               source=Source.CMDB)], 1, g, Tunables())
    assert mat.nodes[0].source is Source.CMDB
    g.upsert_node(mat.nodes[0])
    assert {a.source for a in g.declared_of(SID)} == {Source.CMDB}


# ── remap re-homes prop declarations (target wins per key) ────────────────────
def test_remap_moves_props_target_wins_per_key():
    g = Graph()
    old, new = "service:pay-api|prod", SID
    g.upsert_node(Node(id=old, type=NodeType.SERVICE,
                       props={"service_name": "pay_api", "env": "prod", "lang": "java"},
                       source=Source.APPD, provisional=True, created_by=1))
    g.upsert_node(_svc({"service_name": "payments-api", "env": "prod", "tier": "gold"},
                       source=Source.CMDB, seq=2))
    g.remap_id(old, new)
    got = g.node(new).props
    # content == {**old.props, **tgt.props}: canonical wins per key, old-only keys survive
    assert got == {"service_name": "payments-api", "env": "prod",
                   "tier": "gold", "lang": "java"}
    by_name = {a.name: a for a in g.declared_of(new)}
    assert by_name["lang"].source is Source.APPD           # moved with its provenance
    assert by_name["service_name"].source is Source.CMDB   # canonical's record kept
    assert g.declared_of(old) == []                        # nothing left keyed to the old id
    # ids re-keyed like edges: prop:<new>:<key>
    assert by_name["lang"].id == f"prop:{new}:lang"


# ── serialization keeps per-prop provenance ───────────────────────────────────
def test_roundtrip_preserves_per_prop_provenance():
    g = Graph()
    g.upsert_node(_svc(source=Source.CMDB))
    g2 = Graph.from_dict(g.to_dict())
    assert g2.to_dict() == g.to_dict()
    assert {a.id: a for a in g2.declared_of(SID)} == {a.id: a for a in g.declared_of(SID)}
    assert g2.node(SID).props == g.node(SID).props
