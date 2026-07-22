"""File-backed persistence (DESIGN §2.4 R-J4). The journal NDJSON is the DURABLE truth;
the graph JSON is a fast cache that can always be rebuilt by replaying the journal. Both
use write-temp-then-rename for crash-safety and stamp a schema_version.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..journal.journal import Journal
from .graph import Graph

GRAPH_SCHEMA_VERSION = 2   # 2: the P6 store-flip — one "assertions" list replaces facts/events


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)          # atomic on POSIX


def save_graph(graph: Graph, path: str | Path) -> None:
    data = {"schema_version": GRAPH_SCHEMA_VERSION, **graph.to_dict()}
    _atomic_write(Path(path), json.dumps(data, indent=2, default=str))


def load_graph(path: str | Path) -> Graph | None:
    p = Path(path)
    if not p.exists():
        return None
    return Graph.from_dict(json.loads(p.read_text()))


def save_journal(journal: Journal, path: str | Path) -> None:
    _atomic_write(Path(path), journal.to_ndjson())


def load_journal(path: str | Path) -> Journal | None:
    p = Path(path)
    if not p.exists():
        return None
    return Journal.from_ndjson(p.read_text())
