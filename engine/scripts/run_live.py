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
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iw_engine.capability import CapabilityLayer, ScenarioSource  # noqa: E402
from iw_engine.capability.adapters import default_adapters  # noqa: E402
from iw_engine.domain import registry  # noqa: E402
from iw_engine.domain.catalog import (  # noqa: E402
    render_catalog,
    render_tools,
    tool_intents,
)
from iw_engine.domain.playbook import Playbook  # noqa: E402
from iw_engine.runtime import Engine, load_playbook  # noqa: E402
from iw_engine.runtime.live_fixtures import LIVE_SCENARIOS as SCENARIOS  # noqa: E402
from iw_engine.runtime.live_planner import GeminiClient, LivePlanner, XaiClient  # noqa: E402


# ── client + wiring ───────────────────────────────────────────────────────────────
def make_client(model: str | None):
    key_file = pathlib.Path.home() / ".secrets" / "stock" / "gemini-api-key.txt"
    xai = os.environ.get("XAI_API_KEY")
    if xai:
        return XaiClient(xai, model=model or "grok-4.5")
    if key_file.exists() and key_file.read_text().strip():
        return GeminiClient(key_file.read_text().strip(), model=model or "gemini-2.5-flash-lite")
    return None


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
    intent_provider = {i: a.provider for a in adapters for i in a.intents}
    source = ScenarioSource(intent_provider, fixtures)
    layer = CapabilityLayer(adapters, source=source)

    catalog_text = render_catalog(registry, pb)
    tools_text = render_tools(adapters)
    planner = LivePlanner(client, catalog_text, tools_text, tool_intents(adapters),
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
    converged = (root == golden_root) and (len(res.rejections) == 0) and bool(refuted)

    print(f"\n-- {name} RESULT --")
    print(f"  phases:      {[p.value for p in res.phases_run]}")
    print(f"  outcome:     {res.close_outcome.value if res.close_outcome else 'open'}")
    for h in res.ledger.ranked():
        print(f"  hyp {h.id:8} status={h.status.value:11} conf={h.confidence.value:.2f} "
              f"root={h.root_candidate}")
    print(f"  winner_root: {root}   golden_root: {golden_root}   "
          f"MATCH={root == golden_root}")
    print(f"  rejections:  {len(res.rejections)}  {[r.reason for r in res.rejections][:6]}")
    print(f"  refuted:     {refuted}")
    print(f"  repairs:     {len(planner.repairs)}  {planner.repairs[:4]}")
    print(f"  >>> CONVERGED: {converged}")
    return {"name": name, "converged": converged, "root": root, "golden": golden_root,
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
              "  ~/.secrets/stock/gemini-api-key.txt   (Gemini)\n"
              "  export XAI_API_KEY=...                 (xAI)\n"
              "Then: .venv/bin/python scripts/run_live.py")
        return

    names = list(SCENARIOS) if args.scenario == "all" else [args.scenario]
    results = [run_scenario(n, client, max_steps=args.max_steps) for n in names]

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
