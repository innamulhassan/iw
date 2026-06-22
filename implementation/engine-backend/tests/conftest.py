"""Shared pytest fixtures."""
from __future__ import annotations

from pathlib import Path

import pytest

from engine.runtime import load_playbook

_PLAYBOOK_PATH = Path(__file__).resolve().parents[1] / "playbooks" / "incident-triage.md"


@pytest.fixture
def playbook():
    """The faithful incident-triage playbook (loaded from disk)."""
    return load_playbook(_PLAYBOOK_PATH)
