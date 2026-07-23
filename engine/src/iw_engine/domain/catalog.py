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

# (INTENT_HINTS — 50 lines of hand-written per-tool one-liners — was deleted as dead code:
# referenced nowhere, and per-tool docs already derive from each capability's own `meta`
# via render_tools(), so a hand-restated copy could only drift.)


def render_nodes() -> str:
    """Each node type as its typed-schema DECLARATION (2026-07-23 primitives §2, the schema step):
    identity keys + the datum-shape CATEGORIES that apply — which predicates are its properties /
    states / readings / spans / events, as DATA (`dictionary.categories_for`, a derived view of the
    single name authority). The per-category split teaches the LLM which SPECIES to author for each
    predicate on this type (the §8.1 router: property=timeless-about, state=true-over-a-window,
    reading=measured-number, span=bounded-happening, event=instant), not just a flat fact list."""
    lines: list[str] = []
    for ntype, spec in sorted(NODE_SPECS.items(), key=lambda kv: (kv[1].tier, kv[0].value)):
        idk = ",".join(spec.identity_keys)
        # P2 §2.3 + the §2 category split: legal predicate names are the dictionary's CANONICAL
        # spellings (a derived view of species+applies_to), grouped by datum-shape category so the
        # LLM sees which SPECIES each predicate is. The model emits canonical names; the engine sets
        # `source_native_name` for tool data.
        cats = dictionary.categories_for(ntype)
        parts = [f"{cat}=[{','.join(cats[cat])}]" for cat in dictionary.CATEGORY_ORDER if cats[cat]]
        catline = "  ".join(parts) if parts else "(peripheral — no cataloged predicates)"
        disc = (spec.discriminator or "").replace("\n", " ").split(". ")[0].strip()
        if len(disc) > 90:
            disc = disc[:87] + "..."
        lines.append(f"  {ntype.value} [{spec.tier}]  id_keys=({idk})\n     {catline}"
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
        if p.gate.promotion:
            gate.append(f"leading-hypothesis confidence>={pb.tunables.confidence_gate}")
        if p.gate.refutation_attempted:
            gate.append("a rival ruled out OR the leader challenged with refuting evidence")
        if p.gate.symptom_cleared:
            gate.append(f"an active '{pb.symptom_cleared_event}' event on the symptom node")
        if p.gate.human_approved:
            gate.append("a human approval (gate_decision approve/refine) on the journal")
        gtxt = ("\n     GATE (to ADVANCE or DONE): " + "; ".join(gate)) if gate else ""
        nxt = ", ".join(f"{k}->{v}" for k, v in p.on_verdict.items()) or "(terminal)"
        lines.append(
            f"  {p.id}: {p.goal}\n"
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
##   Each type declares its predicates BY CATEGORY — property / state / reading / span / event.
##   The category tells you which `species` to author (§8.1 router): property=timeless fact ABOUT
##   it (never sliced as-of-T), state=a value TRUE over a window you WILL slice as-of-T, reading=a
##   measured number (stat+window), span=a bounded happening it PARTICIPATES in, event=an instant.
{render_nodes()}

## EDGE TYPES  (legal (src_type -> dst_type) pairs; an illegal triple is REJECTED)
##   AIRLOCK: `generic_ci` may stand in for either end of a STRUCTURAL edge (depends_on, calls,
##   runs_on, ...) when its other end is legal there — the edge lands provisional at reduced
##   confidence, origin=discovered. A generic_ci may also be NAMED A CAUSE via caused_by.
{render_edges()}

## NODE-ID CONVENTION (how to reference a node in a fact/edge/hypothesis)
  id = "<type>:" + the node's identity_key values, slugged (lowercased, spaces/'/'->'-'),
       joined by "|".  e.g. change_event with change_id="CHG-9" -> "change_event:chg-9";
       service with service_name="orders-api", env="prod" -> "service:orders-api|prod";
       code_commit with sha="abc123" -> "code_commit:abc123".
  ALWAYS copy an exact id verbatim from the CURRENT GRAPH slice when the node already exists.

## QUERY GRAMMAR — the DESK (pushed) + REACHING for more
  Your DESK each turn is the FOCUS SLICE (pre-tiered: focus + cause-path + suspects in full, the
  frontier to expand along, healthy nodes collapsed to a count) + the ranked hypotheses + the
  engine-computed GRAPH PROJECTIONS. REASON over them; what the engine reaches for lands on the desk:
    - state as-of-T   — the STATE tile TRUE at an instant (a version/image/severity AT onset, not now)
    - metric summary  — a READING summarised over a window (through the metric_query handle)
    - spans of a node — the SPAN datums it participates in, span_phase ALWAYS shown; a span whose
                        interval CONTAINS onset IS the RCA overlap join (change window / outage / rollout)
    - change-trail    — the STATE supersede-chain + the change-index (events, spans, change_events,
                        AND every STATE version-boundary — so a silent bump is never missed)
  You EXPAND over the OBSERVED spine; you only READ the derived causal/evidence layer as the slice
  renders it (a topology query never drags in hypotheses).

## PHASES (you are driving ONE phase per turn; the engine routes on your verdict)
{render_phases(playbook)}

Total: {len(node_types)} node types, {len(EDGE_SPECS)} edge types."""


def _intent_effect(a, intent: str) -> str:
    """The effect of ONE intent on this adapter — the same per-intent resolution the
    CapabilityLayer's gate applies (the adapter's optional `effects` override first, its
    default `effect` else), so the tool list the model sees and the gate that judges its
    calls can never disagree about what is a write."""
    effects = getattr(a, "effects", None)
    if isinstance(effects, dict) and intent in effects:
        return effects[intent].value
    return a.effect.value


def render_tools(adapters, *, include_writes: bool = False) -> str:
    """The concrete capabilities the layer can resolve — rendered FROM each capability's own
    `meta` (its one-line purpose + the identifier it queries by). Nothing tool-specific is
    hardcoded here: adding a capability makes the reasoner aware of it, and its `queries_by`
    field tells the reasoner which resolved identifier to pass. Effects are PER-INTENT (one
    adapter may host reads AND a gated write — part4-capability §1): read intents render in
    the read blocks; write intents render under a `[WRITE — human-gated]` block for the same
    provider, and only when `include_writes` (so a live planner can propose a remediation)."""
    def block(a, intents: list[str], *, write: bool) -> str:
        meta = getattr(a, "meta", None)
        desc = f" — {meta.summary} · queries by `{meta.queries_by}`" if meta else ""
        label = f"  {a.provider} [WRITE — human-gated]" if write else f"  {a.provider}"
        return f"{label}{desc}\n      intents: {'  '.join(intents)}"

    reads: list[str] = []
    writes: list[str] = []
    for a in sorted(adapters, key=lambda x: x.provider):
        r = sorted(i for i in a.intents if _intent_effect(a, i) != "write")
        w = sorted(i for i in a.intents if _intent_effect(a, i) == "write")
        if r:
            reads.append(block(a, r, write=False))
        if w and include_writes:
            writes.append(block(a, w, write=True))
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
    `include_writes` also admits WRITE intents so the LIVE planner's remediation call is legal.
    Write-ness is PER-INTENT (`effects` override first), matching the layer's gate."""
    return {i for a in adapters for i in a.intents
            if include_writes or _intent_effect(a, i) != "write"}
