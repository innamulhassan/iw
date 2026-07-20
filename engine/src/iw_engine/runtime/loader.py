"""Playbook loader — YAML → validated Playbook (the tuned, declarative config)."""
from __future__ import annotations

from pathlib import Path

import yaml

from ..domain.playbook import Playbook


def load_playbook_text(text: str) -> Playbook:
    return Playbook.model_validate(yaml.safe_load(text))


def load_playbook(path: str | Path) -> Playbook:
    return load_playbook_text(Path(path).read_text())
