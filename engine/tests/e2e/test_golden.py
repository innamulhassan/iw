"""The equivalence oracle (Phase 0). Every scenario's exported bundle must match its
golden snapshot — the net that guarantees the depth refactor (folder-loaded domains,
str-typed vocab, node-expansion, stepper) preserves behavior byte-for-byte.
Regenerate goldens intentionally with `python scripts/gen_golden.py`.
"""
from __future__ import annotations

import importlib
import json
import pathlib

import pytest

from iw_engine.api.bundle import export_bundle

from ._helpers import run

GOLDEN = pathlib.Path(__file__).parent / "golden"
SCENARIOS = ["code_regression", "deployment", "network", "database", "firewall", "nochange",
             "messaging", "infra"]


@pytest.mark.parametrize("name", SCENARIOS)
def test_bundle_matches_golden(name):
    m = importlib.import_module(f"e2e.scenario_{name}")
    built = m.build()
    subject, script = built[0], built[1]
    fixtures = built[2] if len(built) > 2 else None
    got = json.loads(json.dumps(export_bundle(run(subject, script, fixtures)),
                                default=str, sort_keys=True))
    want = json.loads((GOLDEN / f"{name}.json").read_text())
    assert got == want, f"{name} bundle diverged from golden"
