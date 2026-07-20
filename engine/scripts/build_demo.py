"""Run a scenario through the real engine and export its FE bundle to workbench/public/.
The React workbench loads this static JSON — no server needed for the demo."""
from __future__ import annotations

import json
import pathlib
import sys
from datetime import UTC, datetime

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from e2e import scenario_code_regression as s1  # noqa: E402

from iw_engine.api.bundle import export_bundle  # noqa: E402
from iw_engine.runtime import Engine, ScriptedPlanner, load_playbook  # noqa: E402


def main() -> None:
    pb = load_playbook(ROOT / "src" / "iw_engine" / "playbooks" / "incident.yaml")
    subject, script = s1.build()
    res = Engine(pb, ScriptedPlanner(script),
                 clock=lambda: datetime(2026, 7, 19, tzinfo=UTC)).run(subject)
    bundle = export_bundle(res)
    out = ROOT.parent / "workbench" / "public" / "demo-code-regression.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(bundle, indent=2, default=str))
    print(f"wrote {out}  nodes={len(bundle['graph']['nodes'])} "
          f"facts={len(bundle['graph']['facts'])} hyps={len(bundle['ledger'])} "
          f"phases={bundle['phases']} outcome={bundle['outcome']}")


if __name__ == "__main__":
    main()
