"""belief — P4 belief arithmetic (DOMAIN-v3 §2.5 / DESIGN §2.3 R-C4): belief is EARNED
from evidence, computed by the ENGINE. The LLM only ever emits the coarse {low,med,high}
rubric; the engine weighs that band against the graph's evidence.

Evidence weight = source_reliability x temporal_proximity x topological_specificity:

- **reliability** — the fact's own belief channel: a MEASURED fact's `source_reliability`
  (reducer-filled from the tunables per-source map — INV-9) or an INFERRED fact's banded
  confidence. Exactly one exists (the Fact model's R-C4 invariant).
- **temporal proximity** — closeness of the fact's `valid_from` to the SYMPTOM ONSET (the
  anomaly's `onset_value` assertion), skew-tolerant per R-J2: inside the COMBINED
  clock-skew bound of the two sources proximity never discriminates (=1.0 — ordering is
  never asserted tighter than the skew window); beyond it, exponential decay with a
  tunable half-life.
- **topological specificity** — structural-spine hop distance from the fact's subject to
  the anomaly (`Graph.structural_distances`: declared/discovered edges only, never the
  inferred causal/evidence layer, so a hypothesis's own claims can never raise the
  specificity of its own evidence). Unreachable subjects get the tunable floor.

`weighted_score` blends the accumulated for-minus-against evidence with the LLM band as
the PRIOR: the band enters as a pseudo-observation of mass `prior_weight`, each resolvable
supporting fact pulls the score toward 1 and each refuting fact toward 0, weighted by its
evidence weight. A hypothesis with NO resolvable evidence therefore scores EXACTLY its
band (the fallback the design demands). Every knob is a tunable (INV-9 — zero engine
constants; the only literals are mathematical identities). All functions are pure and
deterministic (evidence lists are stored sorted; BFS hop counts are order-independent),
so live scoring and journal replay agree bit-for-bit.
"""
from __future__ import annotations

from datetime import datetime

from ..domain.enums import FactState, NodeType, Source
from ..domain.fact import Fact
from ..domain.hypothesis import Hypothesis
from ..domain.playbook import Tunables
from ..graph.graph import Graph


# ── skew (R-J2) ───────────────────────────────────────────────────────────────
def skew_s(source: Source | None, tunables: Tunables) -> float:
    """The clock-skew bound (seconds) of one source, from the tunables per-source map;
    a source without an explicit entry gets the map's "default"."""
    if source is None:
        return 0.0
    m = tunables.clock_skew_bound_s
    return float(m.get(source.value, m.get("default", 0.0)))


def combined_skew_s(a: Source | None, b: Source | None, tunables: Tunables) -> float:
    """R-J2: two clocks are only comparable up to the SUM of their skew bounds — a
    temporal assertion between sources a and b is never tighter than this window."""
    return skew_s(a, tunables) + skew_s(b, tunables)


# ── symptom anchor + onset ────────────────────────────────────────────────────
def find_anomaly(graph: Graph) -> str | None:
    """The symptom anchor when no explicit ref is bound: the FIRST Anomaly node in
    creation order (graph.nodes preserves insertion order, which journal replay
    reproduces — the same node the engine captures as `_anomaly_ref`)."""
    for n in graph.nodes.values():
        if n.type == NodeType.ANOMALY:
            return n.id
    return None


def onset_of(graph: Graph, anomaly_ref: str | None) -> tuple[datetime, Source] | None:
    """The symptom-onset instant + its observing source, derived from the anomaly node's
    `onset_value` assertion (DOMAIN-v3 §2.5). Deterministic: the earliest non-retracted
    onset fact by (valid_from, id). None when the anomaly (or its onset) is not yet
    framed — proximity is then neutral, never a guess."""
    if anomaly_ref is None:
        return None
    best: Fact | None = None
    for f in graph.facts.values():
        if (f.subject_ref == anomaly_ref and f.predicate == "onset_value"
                and f.state != FactState.RETRACTED):
            if best is None or (f.valid_from, f.id) < (best.valid_from, best.id):
                best = f
    if best is None:
        return None
    return best.valid_from, best.source


# ── the three factors ─────────────────────────────────────────────────────────
def reliability_of(fact: Fact) -> float:
    """The fact's own belief channel: measured → source_reliability, inferred → banded
    confidence (exactly one exists per the Fact model's R-C4 validator)."""
    if fact.source_reliability is not None:
        return fact.source_reliability
    if fact.confidence is not None:
        return fact.confidence.value
    return 0.0   # unreachable under the model invariant — a channel-less fact weighs nothing


def temporal_proximity(t: datetime | None, source: Source | None,
                       onset: datetime | None, onset_source: Source | None,
                       tunables: Tunables) -> float:
    """Closeness of an observation to symptom onset. Inside the combined skew window the
    factor is exactly 1.0 — R-J2: proximity never asserts ordering (or distance) tighter
    than the two clocks support. Beyond it: 0.5 ** (excess / halflife). With no onset
    framed yet the factor is neutral (1.0) — absence of an anchor is never a penalty."""
    if t is None or onset is None:
        return 1.0
    try:
        dt = abs((t - onset).total_seconds())
    except TypeError:
        return 1.0   # naive/aware mix — belief must never crash the fold (INV-7): neutral
    excess = dt - combined_skew_s(source, onset_source, tunables)
    if excess <= 0.0:
        return 1.0
    return 0.5 ** (excess / tunables.proximity_halflife_s)


def topological_specificity(hops: int | None, tunables: Tunables) -> float:
    """Structural closeness of the evidence's subject to the anomaly: decay ** hops,
    floored. `hops=None` (subject unreachable over the spine, or not a node) gets the
    floor — evidence about an unplaced entity still weighs, dimly."""
    if hops is None:
        return tunables.specificity_floor
    return max(tunables.specificity_floor, tunables.specificity_decay ** hops)


def evidence_weight(fact: Fact, *, onset: datetime | None, onset_source: Source | None,
                    distances: dict[str, int] | None, tunables: Tunables) -> float:
    """weight = reliability x temporal_proximity x topological_specificity (§2.5).
    `distances=None` means NO anchor exists yet (specificity neutral, not floored);
    a dict miss means the subject is unreachable from the anchor (floored)."""
    rel = reliability_of(fact)
    prox = temporal_proximity(fact.valid_from, fact.source, onset, onset_source, tunables)
    spec = (1.0 if distances is None
            else topological_specificity(distances.get(fact.subject_ref), tunables))
    return rel * prox * spec


# ── the weighted score (the blend) ────────────────────────────────────────────
def weighted_score(h: Hypothesis, graph: Graph, tunables: Tunables,
                   *, anomaly_ref: str | None = None) -> float:
    """The engine-earned belief in `h`: the LLM band as a prior of mass `prior_weight`,
    blended with the accumulated weighted for-minus-against evidence —

        score = (prior_weight·band + Σ w(supporting)) /
                (prior_weight + Σ w(supporting) + Σ w(refuting))

    Supporting evidence pulls toward 1, refuting toward 0, each by its earned weight; no
    resolvable evidence ⇒ exactly the band. Only materialised, non-RETRACTED facts weigh
    (the Fact is the one addressable evidence unit — a disavowed observation stops
    counting, a superseded one was still the truth of its window). Rounded to 4 decimals
    (the reducer's precision precedent) for stable goldens."""
    anchor = anomaly_ref if anomaly_ref is not None else find_anomaly(graph)
    distances = graph.structural_distances(anchor) if anchor is not None else None
    ons = onset_of(graph, anchor)
    onset, onset_source = ons if ons is not None else (None, None)

    def w(fact_id: str) -> float:
        f = graph.facts.get(fact_id)
        if f is None or f.state == FactState.RETRACTED:
            return 0.0
        return evidence_weight(f, onset=onset, onset_source=onset_source,
                               distances=distances, tunables=tunables)

    s_for = sum(w(fid) for fid in h.supporting_facts)
    s_against = sum(w(fid) for fid in h.refuting_facts)
    denom = tunables.prior_weight + s_for + s_against
    if denom <= 0.0:
        return h.confidence.value
    return round((tunables.prior_weight * h.confidence.value + s_for) / denom, 4)
