"""Playbook loader — markdown + YAML front-matter → a validated `Playbook`. B1 (parse + validate).

The playbook is data; the engine compiles it into a run (compile.py). This is the only place the
authored markdown becomes the typed model; everything downstream works off `Playbook`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Union

import yaml

from engine.domain import Playbook

_FENCE = "---"
_ALLOWED = {
    "id", "version", "domain", "status", "owner", "phases", "graph_schema",
    "schemas", "defaults", "unknown_access", "error_handler", "changelog",
}


def split_frontmatter(text: str) -> tuple[dict, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FENCE:
        raise ValueError("playbook must open with a YAML front-matter fence '---'")
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == _FENCE), None)
    if end is None:
        raise ValueError("unterminated front-matter (no closing '---')")
    fm = yaml.safe_load("\n".join(lines[1:end])) or {}
    body = "\n".join(lines[end + 1:]).strip()
    return fm, body


def _to_kwargs(fm: dict, body: str) -> dict:
    fm = dict(fm)
    kwargs: dict = {"body_md": body}
    if "output_schemas" in fm:                 # front-matter name → model field `schemas`
        kwargs["schemas"] = fm.pop("output_schemas")
    for key, val in fm.items():
        if key not in _ALLOWED:
            raise ValueError(f"unknown playbook front-matter key: {key!r}")
        kwargs[key] = val
    return kwargs


def load_playbook_text(text: str) -> Playbook:
    fm, body = split_frontmatter(text)
    return Playbook.model_validate(_to_kwargs(fm, body))


def load_playbook(path: Union[str, Path]) -> Playbook:
    return load_playbook_text(Path(path).read_text(encoding="utf-8"))
