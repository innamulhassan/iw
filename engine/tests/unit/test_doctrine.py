"""Doctrine-as-playbook-data (Part III §3). The live planner's system prompt is ASSEMBLED:
persona/evidence-contracts/rooting/progression come from the playbook's `doctrine:` block
(data, not engine code), and every validity list is DERIVED from its enum — so the prompt
can never again drift from the grammar the way the old hand-restated `_SYSTEM` source list
dropped `bigpanda`."""
from __future__ import annotations

import pathlib

import iw_engine
from iw_engine.domain import catalog
from iw_engine.domain.enums import HypothesisStatus, OpKind, Source
from iw_engine.domain.playbook import Doctrine
from iw_engine.runtime.live_planner import LivePlanner
from iw_engine.runtime.loader import load_playbook
from iw_engine.runtime.planner import PlanContext

PLAYBOOK = pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml"


def _doctrine_kwargs(**overrides) -> dict:
    base = {f: f"<{f}>" for f in Doctrine.model_fields}
    base.update(overrides)
    return base


def test_packaged_playbook_carries_the_full_doctrine():
    pb = load_playbook(PLAYBOOK)
    assert pb.doctrine is not None
    # the four moved doctrine families are all present, verbatim prompt prose
    assert pb.doctrine.persona.startswith("You are a senior SRE")
    assert "onset_value" in pb.doctrine.frame_contract          # the FRAME Anomaly+onset contract
    assert "CODE_COMMIT" in pb.doctrine.rooting                 # fault-class rooting conventions
    assert "FIREWALL_RULE" in pb.doctrine.rooting
    # P7 5-phase algebra: the progression prose teaches the investigate LOOP + act-entry
    assert "hypothesize⇄evidence⇄refute loop" in pb.doctrine.progression
    assert "human-gated ACT" in pb.doctrine.progression


def test_system_prompt_is_doctrine_plus_derived_validity_lists():
    planner = LivePlanner(client=None, catalog_text="", tools_text="", tool_intents=set(),
                          verbose=False)
    s = planner.system
    d = load_playbook(PLAYBOOK).doctrine
    # doctrine fragments land verbatim (assembly is concatenation, never re-wrapping)
    for fragment in (d.persona, d.evidence_ops, d.fact_rules, d.frame_contract, d.rooting,
                     d.investigate_advance, d.verify_advance, d.hypothesis_method):
        assert fragment in s
    # the validity lists are DERIVED from the enums — incl. the bigpanda drift fix
    assert ("valid sources: " + ", ".join(x.value for x in Source) + ".") in s
    assert "bigpanda" in s
    assert ("valid hypothesis statuses: "
            + ", ".join(x.value for x in HypothesisStatus) + ".") in s
    # op kinds advertise exactly the parser's dispatch set: every OpKind EXCEPT the
    # adapters' native AddAssertion atom (the model authors via the add_fact/add_event shims)
    kinds_line = next(ln for ln in s.splitlines() if ln.startswith("valid op kinds: "))
    kinds = kinds_line.removeprefix("valid op kinds: ").rstrip(".").split(", ")
    assert kinds == [k.value for k in OpKind if k is not OpKind.ADD_ASSERTION]
    assert "add_assertion" not in kinds_line


def test_custom_doctrine_overrides_the_packaged_default():
    d = Doctrine(**_doctrine_kwargs(persona="You investigate SUPPLY-CHAIN compromises.",
                                    progression="advance the moment the ledger balances"))
    planner = LivePlanner(client=None, catalog_text="", tools_text="", tool_intents=set(),
                          doctrine=d, verbose=False)
    assert planner.system.startswith("You investigate SUPPLY-CHAIN compromises.")
    assert "senior SRE" not in planner.system      # the packaged persona is fully displaced

    pb = load_playbook(PLAYBOOK)
    spec = pb.phase(pb.entry_phase)
    from iw_engine.domain.subject import SubjectRef
    ctx = PlanContext(subject=SubjectRef(domain="app-incident", id="INC-1", kind="incident"),
                      phase=pb.entry_phase, phase_spec=spec, goal=spec.goal,
                      tunables=pb.tunables)
    # the per-turn progression prose is doctrine too (the TRIAGE/INVESTIGATE scope rule)
    assert "PROGRESSION RULE: advance the moment the ledger balances" in planner._build_prompt(ctx)


def test_entry_seed_hint_is_doctrine_data_not_engine_vocab():
    """M23: the entry-phase seed literal ("emit the Anomaly node + an onset_value fact NOW") is
    DOCTRINE DATA, not baked in engine code. The engine _OUTPUT_CONTRACT is domain-neutral now, and
    the replan nudge reads doctrine.entry_seed_hint keyed on the entry_phase role binding."""
    from iw_engine.domain.subject import SubjectRef
    from iw_engine.runtime import live_planner as lp
    pb = load_playbook(PLAYBOOK)
    assert pb.doctrine.entry_seed_hint == "emit the Anomaly node + an onset_value fact NOW."
    # the engine's output-contract constant no longer bakes any incident vocabulary
    for literal in ("onset_value", "anomaly", "healthrule_violations", "cleared"):
        assert literal not in lp._OUTPUT_CONTRACT
    # the replan nudge (attempt>1 at the entry phase) reads the doctrine hint by role binding
    spec = pb.phase(pb.entry_phase)
    ctx = PlanContext(subject=SubjectRef(domain="app-incident", id="INC-1", kind="incident"),
                      phase=pb.entry_phase, entry_phase=pb.entry_phase, phase_spec=spec,
                      goal=spec.goal, tunables=pb.tunables)
    planner = LivePlanner(client=None, catalog_text="", tools_text="", tool_intents=set(),
                          verbose=False)
    planner._attempts[pb.entry_phase] = 2   # force the replan nudge
    assert "emit the Anomaly node + an onset_value fact NOW." in planner._build_prompt(ctx)


def test_intent_hints_is_gone():
    # 50 lines of dead, hand-restated per-tool docs — deleted; tool docs derive from
    # each capability's own `meta` via render_tools()
    assert not hasattr(catalog, "INTENT_HINTS")
