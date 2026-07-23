"""The seven-class edge taxonomy (NODE-EDGE-PRIMITIVES 2026-07-23 §5.2).

Every EdgeType maps onto exactly one settled semantic class; this test LOCKS the mapping and
the two behavioral disciplines the class carries (PROVENANCE=immutable, CORRESPONDENCE=symmetric),
so a new edge type cannot land unclassified and a reclassification is a deliberate diff.
"""
from __future__ import annotations

from iw_engine.domain import registry
from iw_engine.domain.edges import (
    EDGE_SPECS,
    IMMUTABLE_EDGE_TYPES,
    STRUCTURAL_EDGE_TYPES,
    SYMMETRIC_EDGE_TYPES,
)
from iw_engine.domain.enums import EdgeClass, EdgeType

# The settled §5.2 mapping — the whole closed catalog, class by class.
EXPECTED: dict[EdgeClass, set[EdgeType]] = {
    EdgeClass.STRUCTURAL: {
        EdgeType.DEPENDS_ON, EdgeType.CALLS, EdgeType.REALIZES, EdgeType.INSTANCE_OF,
        EdgeType.RUNS_ON, EdgeType.HOSTED_ON, EdgeType.DEPLOYED_TO, EdgeType.CONTAINS,
        EdgeType.MEMBER_OF, EdgeType.EXPOSES, EdgeType.ROUTES_TO, EdgeType.CONNECTS_TO,
        EdgeType.READS_FROM, EdgeType.WRITES_TO, EdgeType.PRODUCES_TO, EdgeType.CONSUMES_FROM,
        EdgeType.SECURED_BY, EdgeType.OWNS,   # OWNS reassigned to STRUCTURAL (§5.2)
    },
    EdgeClass.PROVENANCE: {
        EdgeType.BUILT_FROM, EdgeType.RELEASED_AS, EdgeType.RUNS_VERSION,
        EdgeType.DEPLOYED_AS, EdgeType.INTRODUCED_BY,
    },
    EdgeClass.PARTICIPATION: {
        EdgeType.FIRED_ON, EdgeType.EMITTED, EdgeType.AFFECTS, EdgeType.TRIGGERED_BY,
        EdgeType.CHANGED_BY,
    },
    EdgeClass.CAUSAL: {EdgeType.IMPACTS, EdgeType.CORRELATED_WITH, EdgeType.CAUSED_BY},
    EdgeClass.EVIDENTIAL: {EdgeType.SUPPORTS, EdgeType.REFUTES},
    EdgeClass.CORRESPONDENCE: {EdgeType.SIMILAR_TO, EdgeType.RECURRENCE_OF},
    EdgeClass.REMEDIATION: {EdgeType.REMEDIATED_BY},
}


def test_every_edge_type_maps_to_its_settled_class():
    got: dict[EdgeClass, set[EdgeType]] = {c: set() for c in EdgeClass}
    for t in EdgeType:
        got[registry.edge_class(t)].add(t)
    assert got == EXPECTED


def test_the_catalog_is_completely_and_uniquely_classified():
    # closure: every EdgeType has a class; partition: the classes tile the catalog with no overlap.
    assert {t for t in EdgeType} == {t for members in EXPECTED.values() for t in members}
    flat = [t for members in EXPECTED.values() for t in members]
    assert len(flat) == len(set(flat)) == len(list(EdgeType))
    assert all(s.edge_class in EdgeClass for s in EDGE_SPECS.values())


def test_provenance_is_immutable_and_only_provenance():
    # §5.2 class 2: a lineage edge never un-happens — superseded-on-rebuild, never retracted-as-wrong.
    assert IMMUTABLE_EDGE_TYPES == EXPECTED[EdgeClass.PROVENANCE]
    assert all(registry.is_immutable_edge(t) for t in EXPECTED[EdgeClass.PROVENANCE])
    assert not any(registry.is_immutable_edge(t)
                   for t in EdgeType if t not in EXPECTED[EdgeClass.PROVENANCE])


def test_correspondence_is_symmetric_and_only_correspondence():
    # §5.2 class 6: stored canonical, read symmetric.
    assert SYMMETRIC_EDGE_TYPES == EXPECTED[EdgeClass.CORRESPONDENCE]
    assert all(registry.is_symmetric_edge(t) for t in EXPECTED[EdgeClass.CORRESPONDENCE])
    assert not any(registry.is_symmetric_edge(t)
                   for t in EdgeType if t not in EXPECTED[EdgeClass.CORRESPONDENCE])


def test_owns_is_structural_by_class_but_not_an_airlock_substitution_surface():
    # The deliberate distinction (§5.2 + edges/__init__): OWNS behaves structurally (mutable,
    # retractable) so its CLASS is STRUCTURAL, but it is NOT in the airlock spine — you own a
    # service, not an unknown CI — so the generic_ci substitution surface stays unchanged.
    assert registry.edge_class(EdgeType.OWNS) is EdgeClass.STRUCTURAL
    assert EdgeType.OWNS not in STRUCTURAL_EDGE_TYPES
    assert not registry.is_immutable_edge(EdgeType.OWNS)   # structural, not lineage
