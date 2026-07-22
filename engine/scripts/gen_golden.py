"""Phase 0 — golden oracle. Snapshot every scenario's exported bundle as the equivalence
net: the depth refactor (folder-loaded domains, str-typed vocab, node-expansion) must
reproduce these byte-for-byte. Run with --update to (re)generate the goldens.
"""
from __future__ import annotations

import importlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from e2e._helpers import run  # noqa: E402

from iw_engine.api.bundle import export_bundle  # noqa: E402

SCENARIOS = ["code_regression", "deployment", "network", "database", "firewall", "nochange",
             "messaging", "infra", "cache", "featureflag", "certificate"]
GOLDEN_DIR = ROOT / "tests" / "e2e" / "golden"


def bundle_for(name: str) -> dict:
    m = importlib.import_module(f"e2e.scenario_{name}")
    built = m.build()
    subject, script = built[0], built[1]
    fixtures = built[2] if len(built) > 2 else None
    return export_bundle(run(subject, script, fixtures))


def main() -> None:
    GOLDEN_DIR.mkdir(exist_ok=True)
    for name in SCENARIOS:
        b = bundle_for(name)
        (GOLDEN_DIR / f"{name}.json").write_text(json.dumps(b, indent=2, default=str, sort_keys=True))
        print(f"{name:16} nodes={len(b['graph']['nodes']):2} facts={len(b['graph']['facts']):2} "
              f"hyps={len(b['hypotheses'])} outcome={b['outcome']}")


if __name__ == "__main__":
    main()
