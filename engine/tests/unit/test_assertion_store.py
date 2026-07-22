"""P6 store-flip (part2 §3 + the P1a design decisions): the graph stores ONE assertion
collection; `facts`/`events` are read views discriminated by species/channel (decision 2),
`invalidated_by` lives on the atom (decision 1), and every Fact/Event field round-trips
byte-identically through the converters — the golden suite is the end-to-end proof, these
are the seam-level proofs.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from iw_engine.domain.assertion import Assertion
from iw_engine.domain.enums import Channel, FactState, NodeType, Source, Species
from iw_engine.domain.event import Event
from iw_engine.domain.fact import Fact
from iw_engine.domain.node import Node
from iw_engine.domain.shim import (
    assertion_of_event,
    assertion_of_fact,
    event_of_assertion,
    fact_of_assertion,
)
from iw_engine.graph import Graph

T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
SID = "service:payments-api|prod"


def _node(seq=1) -> Node:
    return Node(id=SID, type=NodeType.SERVICE,
                props={"service_name": "payments-api", "env": "prod"}, created_by=seq)


def _fact(fid="f1", predicate="red_errors", value=0.4, ts=T0, **kw) -> Fact:
    base = dict(id=fid, subject_ref=SID, predicate=predicate, value=value, valid_from=ts,
                observed_at=ts, source=Source.PROMETHEUS, source_reliability=0.95,
                created_by=1)
    base.update(kw)
    return Fact(**base)


def _event(eid="e1", etype="config_changed", ts=T0, **kw) -> Event:
    base = dict(id=eid, entity_ref=SID, type=etype, occurred_at=ts, observed_at=ts,
                payload={"actor": "ci"}, source=Source.OCP, created_by=1)
    base.update(kw)
    return Event(**base)


# ── ONE collection, two views ─────────────────────────────────────────────────
def test_facts_and_events_land_in_the_one_assertion_collection():
    g = Graph()
    g.upsert_node(_node())
    g.add_fact(_fact())
    g.add_event(_event())
    assert set(g.assertions) == {"f1", "e1"}          # ONE store holds both
    assert g.assertions["f1"].species is not Species.EVENT
    assert g.assertions["e1"].species is Species.EVENT
    # the views split it exactly (decision 2's discriminator)
    assert set(g.facts) == {"f1"} and set(g.events) == {"e1"}


def test_views_return_the_same_records_as_the_pre_flip_store():
    g = Graph()
    g.upsert_node(_node())
    f = _fact(where="eu-west-1a", provisional=True,
              source_native_name="red_errors", unit="ratio")
    ev = _event(source_native_name="ConfigChange")
    g.add_fact(f)
    g.add_event(ev)
    assert g.facts["f1"] == f                          # byte-for-byte the Fact that went in
    assert g.events["e1"] == ev                        # and the Event
    # view stability between mutations: repeated reads hand back the same objects
    assert g.facts is g.facts and g.facts["f1"] is g.facts["f1"]


def test_declared_channel_is_excluded_from_the_facts_view():
    """Decision 2: node-declared props are DECLARED-channel assertions → the props view,
    never the facts view (facts = observed knowledge only)."""
    g = Graph()
    g.upsert_node(_node())
    g.add_fact(_fact())
    g.assertions["p1"] = Assertion(
        id="p1", subject_ref=SID, name="env", value="prod", species=Species.DESCRIPTOR,
        channel=Channel.DECLARED, observed_at=T0, source=Source.CMDB,
        source_reliability=1.0, created_by=1)
    g._rev += 1
    assert "p1" in g.assertions and "p1" not in g.facts and "p1" not in g.events
    assert set(g.facts) == {"f1"}


# ── converter round-trips (the byte-identity seam) ────────────────────────────
def test_fact_assertion_roundtrip_is_exact():
    for f in (
        _fact(),
        _fact(fid="f2", where="eu-west-1a", unit="ratio", provisional=True,
              source_native_name="red_errors", valid_to=T0 + timedelta(minutes=5),
              supersedes="f1"),
        _fact(fid="f3", predicate="diff_summary", value={"lines": 4},
              source=Source.GIT, source_reliability=0.9),   # a DESCRIPTOR-species predicate
    ):
        assert fact_of_assertion(assertion_of_fact(f)) == f


def test_event_assertion_roundtrip_is_exact():
    for ev in (_event(),
               _event(eid="e2", etype="OOMKilled", payload={}, provisional=True,
                      source_native_name="oom_killed"),
               _event(eid="e3", invalidated_by="fact:f9", state=FactState.RETRACTED)):
        assert event_of_assertion(assertion_of_event(ev)) == ev


# ── lifecycle through the one store ───────────────────────────────────────────
def test_supersede_and_retract_operate_on_the_assertion_store():
    g = Graph()
    g.upsert_node(_node())
    g.add_fact(_fact())
    g.add_fact(_fact(fid="f2", ts=T0 + timedelta(minutes=5), supersedes="f1"))
    assert g.assertions["f1"].state == FactState.SUPERSEDED
    assert g.facts["f1"].state == FactState.SUPERSEDED          # the view mirrors it
    assert g.facts["f1"].valid_to == T0 + timedelta(minutes=5)

    g.retract_fact("f2")
    assert g.facts["f2"].state == FactState.RETRACTED


def test_event_invalidated_by_lives_on_the_atom():
    """Decision 1: invalidated_by is an Assertion field — a retracted occurrence carries the
    id of what proved it wrong, in the ONE store."""
    g = Graph()
    g.upsert_node(_node())
    g.add_event(_event())
    g.retract_event("e1", invalidated_by="fact:f9")
    assert g.assertions["e1"].invalidated_by == "fact:f9"
    assert g.assertions["e1"].state == FactState.RETRACTED
    assert g.events["e1"].invalidated_by == "fact:f9"           # the view carries it out


def test_remap_rewrites_subject_refs_in_one_pass():
    g = Graph()
    g.upsert_node(_node())
    g.add_fact(_fact())
    g.add_event(_event())
    g.remap_id(SID, "service:payments|prod")
    assert g.assertions["f1"].subject_ref == "service:payments|prod"
    assert g.assertions["e1"].subject_ref == "service:payments|prod"
    assert g.facts["f1"].subject_ref == "service:payments|prod"
    assert g.events["e1"].entity_ref == "service:payments|prod"


# ── serialisation: the one collection persists; legacy caches still load ──────
def test_to_dict_persists_assertions_not_views():
    g = Graph()
    g.upsert_node(_node())
    g.add_fact(_fact())
    g.add_event(_event())
    d = g.to_dict()
    assert "assertions" in d and "facts" not in d and "events" not in d
    g2 = Graph.from_dict(d)
    assert g2.to_dict() == d
    assert g2.facts == g.facts and g2.events == g.events


def test_from_dict_reads_the_legacy_facts_events_cache_shape():
    g = Graph()
    g.upsert_node(_node())
    f, ev = _fact(), _event()
    g.add_fact(f)
    g.add_event(ev)
    legacy = {
        "nodes": [n.model_dump(mode="json") for n in g.nodes.values()],
        "edges": [],
        "facts": [f.model_dump(mode="json")],
        "events": [ev.model_dump(mode="json")],
        "remaps": {},
    }
    g2 = Graph.from_dict(legacy)
    assert g2.facts["f1"] == f and g2.events["e1"] == ev
    assert set(g2.assertions) == {"f1", "e1"}
