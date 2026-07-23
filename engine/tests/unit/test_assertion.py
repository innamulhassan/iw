"""Assertion atom (P1a build-spec step 1) — the belief-channel validator + the species/
time-shape invariants. Types only; nothing wires it yet. These lock the envelope's
invariants so the compat shim (steps 2-3) can lean on them."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from iw_engine.domain.assertion import Assertion, Window, channel_for_source
from iw_engine.domain.common import Confidence
from iw_engine.domain.enums import Channel, Source, Species, Stat

T0 = datetime(2026, 7, 19, 14, 0, tzinfo=UTC)
T1 = datetime(2026, 7, 19, 15, 0, tzinfo=UTC)


def _state(**kw):
    base = dict(id="a1", subject_ref="service:x", name="degraded", value=True,
                species=Species.STATE, channel=Channel.MEASURED, valid_from=T0,
                observed_at=T0, source=Source.PROMETHEUS, source_reliability=0.95, created_by=1)
    base.update(kw)
    return Assertion(**base)


# ── Source → channel map ──────────────────────────────────────────────────────
def test_channel_for_source_map():
    assert channel_for_source(Source.LLM) is Channel.INFERRED
    assert channel_for_source(Source.ENGINE) is Channel.ENGINE
    assert channel_for_source(Source.PROMETHEUS) is Channel.MEASURED
    assert channel_for_source(Source.HUMAN) is Channel.MEASURED
    assert channel_for_source(Source.CMDB) is Channel.MEASURED


# ── belief channel ────────────────────────────────────────────────────────────
def test_inferred_requires_confidence():
    a = _state(species=Species.DESCRIPTOR, channel=Channel.INFERRED, source=Source.LLM,
               source_reliability=None, confidence=Confidence(value=0.6, basis="reasoned"),
               valid_from=None)
    assert a.confidence.value == 0.6


def test_inferred_rejects_reliability():
    with pytest.raises(ValidationError, match="carries confidence, not reliability"):
        _state(species=Species.DESCRIPTOR, channel=Channel.INFERRED, source=Source.LLM,
               source_reliability=0.9, confidence=Confidence(value=0.6, basis="r"), valid_from=None)


def test_inferred_missing_confidence_rejected():
    with pytest.raises(ValidationError, match="must carry a confidence"):
        _state(species=Species.DESCRIPTOR, channel=Channel.INFERRED, source=Source.LLM,
               source_reliability=None, confidence=None, valid_from=None)


def test_measured_requires_reliability():
    with pytest.raises(ValidationError, match="must carry source_reliability"):
        _state(source_reliability=None)


def test_measured_rejects_confidence():
    with pytest.raises(ValidationError, match="carries source_reliability, not a confidence"):
        _state(confidence=Confidence(value=0.6, basis="r"))


def test_engine_channel_uses_reliability():
    a = _state(species=Species.DESCRIPTOR, channel=Channel.ENGINE, source=Source.ENGINE,
               source_reliability=1.0, valid_from=None)
    assert a.channel is Channel.ENGINE and a.source_reliability == 1.0


# ── M16: ONE authoritative belief-channel enforcer (the Assertion atom + its Fact view share it) ─
def test_belief_exclusivity_is_the_one_shared_enforcer():
    """M16: R-C4 (exactly one belief field) has a SINGLE enforcer — `common.enforce_belief_exclusivity`
    — not two hand-written copies. Both the Assertion atom and its Fact view import the SAME symbol,
    so the invariant can never drift. Exercise every branch of the one rule directly."""
    from iw_engine.domain import assertion as assertion_mod
    from iw_engine.domain import fact as fact_mod
    from iw_engine.domain.common import enforce_belief_exclusivity

    # the two validators import the exact same function object — one home, no dual implementation
    assert assertion_mod.enforce_belief_exclusivity is enforce_belief_exclusivity
    assert fact_mod.enforce_belief_exclusivity is enforce_belief_exclusivity

    ok_conf = Confidence(value=0.5, basis="b")
    # inferred → confidence required, reliability forbidden
    enforce_belief_exclusivity("x:", inferred=True, inferred_desc="inferred x",
                               measured_desc="measured x", confidence=ok_conf,
                               source_reliability=None)
    with pytest.raises(ValueError, match="must carry a confidence"):
        enforce_belief_exclusivity("x:", inferred=True, inferred_desc="inferred x",
                                   measured_desc="measured x", confidence=None,
                                   source_reliability=None)
    with pytest.raises(ValueError, match="carries confidence, not reliability"):
        enforce_belief_exclusivity("x:", inferred=True, inferred_desc="inferred x",
                                   measured_desc="measured x", confidence=ok_conf,
                                   source_reliability=0.9)
    # measured/declared/engine → reliability required, confidence forbidden
    enforce_belief_exclusivity("x:", inferred=False, inferred_desc="inferred x",
                               measured_desc="measured x", confidence=None, source_reliability=0.9)
    with pytest.raises(ValueError, match="must carry source_reliability"):
        enforce_belief_exclusivity("x:", inferred=False, inferred_desc="inferred x",
                                   measured_desc="measured x", confidence=None,
                                   source_reliability=None)
    with pytest.raises(ValueError, match="carries source_reliability, not a confidence"):
        enforce_belief_exclusivity("x:", inferred=False, inferred_desc="inferred x",
                                   measured_desc="measured x", confidence=ok_conf,
                                   source_reliability=0.9)


def test_fact_and_assertion_enforce_the_same_belief_rule():
    """M16 parity: the identical belief violation is rejected on BOTH the Fact record and the
    equivalent Assertion — a measured record carrying a confidence, and an inferred record missing
    one. One rule, two shapes, no divergence (the whole point of collapsing the dual enforcer)."""
    from iw_engine.domain.fact import Fact

    # measured record carrying BOTH belief fields → rejected on both shapes
    with pytest.raises(ValidationError):
        Fact(id="f1", subject_ref="service:x", predicate="red_errors", value=0.4, valid_from=T0,
             observed_at=T0, source=Source.PROMETHEUS, source_reliability=0.9,
             confidence=Confidence(value=0.6, basis="b"), created_by=1)
    with pytest.raises(ValidationError):
        _state(channel=Channel.MEASURED, source=Source.PROMETHEUS, source_reliability=0.9,
               confidence=Confidence(value=0.6, basis="b"))

    # inferred record missing its confidence → rejected on both shapes
    with pytest.raises(ValidationError):
        Fact(id="f2", subject_ref="service:x", predicate="root_cause_guess", value="x",
             valid_from=T0, observed_at=T0, source=Source.LLM, created_by=1)
    with pytest.raises(ValidationError):
        _state(species=Species.DESCRIPTOR, channel=Channel.INFERRED, source=Source.LLM,
               source_reliability=None, confidence=None, valid_from=None)


# ── species / time shape ──────────────────────────────────────────────────────
def test_identity_has_no_observed_at():
    with pytest.raises(ValidationError, match="write-once — no observed_at"):
        Assertion(id="i1", subject_ref="service:x", name="service_name", value="pay",
                  species=Species.IDENTITY, channel=Channel.DECLARED, observed_at=T0,
                  source=Source.CMDB, created_by=1)


def test_identity_carries_no_belief():
    a = Assertion(id="i1", subject_ref="service:x", name="service_name", value="pay",
                  species=Species.IDENTITY, channel=Channel.DECLARED, source=Source.CMDB,
                  created_by=1)
    assert a.confidence is None and a.source_reliability is None


def test_identity_rejects_belief():
    with pytest.raises(ValidationError, match="asserted truth — no belief channel"):
        Assertion(id="i1", subject_ref="service:x", name="service_name", value="pay",
                  species=Species.IDENTITY, channel=Channel.DECLARED, source=Source.CMDB,
                  source_reliability=0.9, created_by=1)


def test_reading_requires_stat_and_window():
    with pytest.raises(ValidationError, match="reading requires both stat and window"):
        _state(species=Species.READING, stat=None, window=None)


def test_reading_ok_with_stat_and_window():
    a = _state(species=Species.READING, name="red_errors", value=0.4, unit="ratio",
               stat=Stat.GAUGE, window=Window(at=T0), valid_from=None)
    assert a.stat is Stat.GAUGE and a.window.at == T0


def test_event_requires_occurred_at():
    with pytest.raises(ValidationError, match="event requires occurred_at"):
        Assertion(id="e1", subject_ref="deployment:x", name="rollout_complete",
                  species=Species.EVENT, channel=Channel.MEASURED, observed_at=T0,
                  source=Source.OCP, source_reliability=0.9, created_by=1)


def _event(**kw):
    base = dict(id="e1", subject_ref="deployment:x", name="rollout_complete",
                species=Species.EVENT, channel=Channel.MEASURED, occurred_at=T0, observed_at=T0,
                source=Source.OCP, created_by=1)
    base.update(kw)
    return Assertion(**base)


def test_event_belief_is_optional():
    # a shim-minted event may carry no belief (the Fact/Event era had none) ...
    assert _event().source_reliability is None
    # ... or a reliability (P1b makes events first-class belief-bearing) ...
    assert _event(source_reliability=0.9).source_reliability == 0.9


def test_event_rejects_both_belief_fields():
    with pytest.raises(ValidationError, match="at most one belief field, not both"):
        _event(source_reliability=0.9, confidence=Confidence(value=0.6, basis="r"))


def test_occurred_at_is_event_only():
    with pytest.raises(ValidationError, match="occurred_at is EVENT-only"):
        _state(occurred_at=T0)


def test_state_requires_observed_at():
    with pytest.raises(ValidationError, match="state requires observed_at"):
        _state(observed_at=None)


def test_valid_window_ordering():
    with pytest.raises(ValidationError, match="valid_to < valid_from"):
        _state(valid_from=T1, valid_to=T0)


# ── Window modes ──────────────────────────────────────────────────────────────
def test_window_point_or_range_not_both():
    with pytest.raises(ValidationError, match=r"point .* OR a range"):
        Window(at=T0, start=T0, end=T1)


def test_window_range_needs_both_ends():
    with pytest.raises(ValidationError, match="needs both start and end"):
        Window(start=T0)


def test_window_range_order():
    with pytest.raises(ValidationError, match="end < start"):
        Window(start=T1, end=T0)
