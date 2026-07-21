"""run_live.py — drive the REAL LLM planner (LivePlanner) end-to-end over three incident
classes and measure CONVERGENCE against the golden root cause (VALIDATION-VERDICT §A gaps
2 + 4; docs/PROGRAM-PLAN.md "Live convergence").

This is the live counterpart to the hermetic e2e suite: the same Engine, playbook, adapters
and reducer, but with (1) a real Gemini/xAI planner instead of ScriptedPlanner, and (2) a
`ScenarioSource` fixture transport that resolves intent -> provider (GAP 4) so any valid
intent for a fixtured provider returns that provider's data. The LIVE-ONLY fixtures below
return real CONTENT (GAP 2): the git diff carries the actual `DROP INDEX` line, blame carries
file:line + the offending source line — not just counts. The hermetic `test_golden` fixtures
are untouched.

Convergence per scenario = the confirmed/leading hypothesis's root_candidate equals the
golden root  AND  0 reducer rejections  AND  a rival hypothesis was refuted.

Usage:
    .venv/bin/python scripts/run_live.py [--scenario code_regression|database|network|all]
                                         [--model gemini-2.5-flash] [--max-steps 16]
A key is read from ~/.secrets/stock/gemini-api-key.txt (Gemini) or the XAI_API_KEY env /
AssetOne .env (xAI). No key -> the script prints how to provide one and exits 0.
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iw_engine.capability import CapabilityLayer, ScenarioSource  # noqa: E402
from iw_engine.capability.adapters import default_adapters  # noqa: E402
from iw_engine.capability.adapters.remediation import RemediationAdapter  # noqa: E402
from iw_engine.domain import registry  # noqa: E402
from iw_engine.domain.catalog import (  # noqa: E402
    render_catalog,
    render_tools,
    tool_intents,
)
from iw_engine.domain.playbook import Playbook  # noqa: E402
from iw_engine.runtime import Engine, load_playbook  # noqa: E402
from iw_engine.runtime.live_fixtures import LIVE_SCENARIOS as SCENARIOS  # noqa: E402
from iw_engine.runtime.live_planner import LivePlanner  # noqa: E402
from iw_engine.runtime.llm_client import make_llm_client  # noqa: E402


# ── client + wiring ───────────────────────────────────────────────────────────────
def make_client(model: str | None):
    """Resolve a live LLM client via the consolidated factory (xAI-first, then Gemini,
    then None). IW_LIVE_PROVIDER overrides; see iw_engine.runtime.llm_client."""
    return make_llm_client(model)


def available_intents(fixtures: dict, adapters) -> set[str]:
    """The concrete READ intents actually wired for this incident (GAP 4 part 2 — told to the
    model as the connected integrations): every read intent of a fixtured provider."""
    fixtured = set(fixtures)
    return {i for a in adapters if a.provider in fixtured and a.effect.value != "write"
            for i in a.intents}


def run_scenario(name: str, client, *, max_steps: int) -> dict:
    subject, fixtures, golden_root = SCENARIOS[name]()
    pb: Playbook = load_playbook(ROOT / "src" / "iw_engine" / "playbooks" / "incident.yaml")
    adapters = default_adapters()
    # include the write-effect remediation tool (parity with live_build_manager) so the LLM can
    # PROPOSE apply_remediation in REMEDIATE instead of it being dropped as off-catalog.
    tool_adapters = [*adapters, RemediationAdapter()]
    intent_provider = {i: a.provider for a in tool_adapters for i in a.intents}
    source = ScenarioSource(intent_provider, fixtures)
    layer = CapabilityLayer(tool_adapters, source=source)

    catalog_text = render_catalog(registry, pb)
    tools_text = render_tools(tool_adapters, include_writes=True)
    planner = LivePlanner(client, catalog_text, tools_text,
                          tool_intents(tool_adapters, include_writes=True),
                          available_sources=available_intents(fixtures, adapters), verbose=True)

    engine = Engine(pb, planner, layer=layer)
    planner.graph = engine.graph   # hand the live planner the direct graph ref (full view)

    print(f"\n{'=' * 78}\nSCENARIO: {name}   model={client.name}   golden_root={golden_root}\n"
          f"{'=' * 78}")
    engine.start(subject, max_steps=max_steps)
    while not engine.done():
        source.phase = engine.current_phase.value   # phase-scope the fixtures (recovery in verify)
        engine.step()
    res = engine.result()

    lead = res.ledger.leading()
    conf = res.confirmed
    winner = conf or lead
    root = winner.root_candidate if winner else None
    refuted = [h.id for h in res.ledger.hypotheses.values() if h.status.value == "refuted"]
    # golden_root may be a single id or a tuple of equally-valid, causally-coupled roots (e.g. an
    # ACL incident's root is legitimately the CHANGE that tightened the rule OR the rule itself).
    golds = golden_root if isinstance(golden_root, (tuple, list)) else (golden_root,)
    match = root in golds
    converged = match and (len(res.rejections) == 0) and bool(refuted)

    print(f"\n-- {name} RESULT --")
    print(f"  phases:      {[p.value for p in res.phases_run]}")
    print(f"  outcome:     {res.close_outcome.value if res.close_outcome else 'open'}")
    for h in res.ledger.ranked():
        print(f"  hyp {h.id:8} status={h.status.value:11} conf={h.confidence.value:.2f} "
              f"root={h.root_candidate}")
    print(f"  winner_root: {root}   golden_root: {' | '.join(golds)}   MATCH={match}")
    print(f"  rejections:  {len(res.rejections)}  {[r.reason for r in res.rejections][:6]}")
    print(f"  refuted:     {refuted}")
    print(f"  repairs:     {len(planner.repairs)}  {planner.repairs[:4]}")
    print(f"  >>> CONVERGED: {converged}")
    return {"name": name, "converged": converged, "root": root, "golden": " | ".join(golds),
            "rejections": len(res.rejections), "refuted": refuted,
            "repairs": len(planner.repairs),
            "outcome": res.close_outcome.value if res.close_outcome else "open"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="all",
                    choices=[*SCENARIOS, "all"])
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-steps", type=int, default=16)
    args = ap.parse_args()

    client = make_client(args.model)
    if client is None:
        print("No LLM key found. Provide one of:\n"
              "  export XAI_API_KEY=...                  (xAI/Grok — default)\n"
              "  export GEMINI_API_KEY=...               (Gemini)\n"
              "  ~/.secrets/stock/gemini-api-key.txt     (Gemini, legacy file)\n"
              "Optional: IW_LIVE_MODEL=<model>, IW_LIVE_PROVIDER=xai|gemini\n"
              "Then: .venv/bin/python scripts/run_live.py")
        return

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    # Per-scenario isolation: a JSON-parse failure, a transient MCP error, or an LLM
    # exhaustion in ONE scenario must not crash the whole batch. Catch, record as
    # not-converged with the error, and continue — the summary still reports every
    # scenario. (The interactive session backend has its own _drive_and_clear catch;
    # this is the batch equivalent.)
    results = []
    for n in names:
        try:
            results.append(run_scenario(n, client, max_steps=args.max_steps))
        except Exception as exc:   # batch runner must not die on one scenario
            print(f"\n-- {n} FAILED --\n  {type(exc).__name__}: {exc}")
            results.append({"name": n, "converged": False, "root": None,
                            "golden": "", "rejections": 0, "refuted": [],
                            "repairs": 0, "outcome": f"error: {type(exc).__name__}"})

    print(f"\n{'=' * 78}\nSUMMARY   model={client.name}\n{'=' * 78}")
    n_conv = sum(r["converged"] for r in results)
    for r in results:
        print(f"  {r['name']:16} converged={r['converged']!s:5} "
              f"root={r['root']} (golden {r['golden']}) "
              f"rejections={r['rejections']} refuted={len(r['refuted'])} outcome={r['outcome']}")
    print(f"\n  CONVERGED {n_conv}/{len(results)}  "
          f"(target: >=2/3 with 0 rejections + a refuted rival)")


if __name__ == "__main__":
    main()
