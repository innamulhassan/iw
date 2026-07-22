"""InvestigationStore — file-backed durability so a live investigation survives a backend
restart (the demo requirement). The engine already treats the journal as the DURABLE source of
truth (`rebuild(journal) -> graph + hypothesis store`); this layer just lands that journal on disk as the
session drives, and reads it back into a read-only reopen after a restart.

Layout — one directory per investigation, keyed by a sanitized `subject.key`:

    <root>/<safe-key>/journal.ndjsonl   append-only NDJSON — the durable truth
    <root>/<safe-key>/graph.json        atomic write-temp-rename cache (rebuildable)
    <root>/<safe-key>/meta.json         subject, state, outcome, timestamps

`<root>` defaults to `engine/data/investigations/` (resolved from the package location, so it is
stable regardless of the working directory). Persistence reuses `graph/persistence.py`
(`save_graph` + its atomic writer) and `journal/journal.py` (`to_ndjson`/`model_dump_json`) —
this module adds no new serialization, only the on-disk layout + the append/reopen wiring.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import iw_engine

from ..domain.enums import CloseOutcome
from ..domain.playbook import Tunables
from ..domain.subject import SubjectRef
from ..graph.fold import rebuild
from ..graph.persistence import _atomic_write, save_graph
from ..journal.journal import SCHEMA_VERSION, Journal
from .engine import RunResult

META_SCHEMA_VERSION = 1

# Windows forbids  < > : " / \ | ? *  in filenames (and ':' is exactly what `subject.key`
# uses as its domain/id separator); keep only a portable, unambiguous alphabet.
_ILLEGAL = re.compile(r"[^A-Za-z0-9._-]")


def safe_key(key: str) -> str:
    """`subject.key` ("domain:id") -> a cross-platform-safe directory name. Every character
    outside ``[A-Za-z0-9._-]`` (notably the Windows-illegal ':') becomes '_'."""
    sanitized = _ILLEGAL.sub("_", key)
    return sanitized or "_"


def _default_root() -> Path:
    # iw_engine/__init__.py -> parents[2] is the `engine/` dir (sibling of `src/`).
    return Path(iw_engine.__file__).resolve().parents[2] / "data" / "investigations"


class InvestigationStore:
    """File-backed durability for investigations. One instance is shared by a SessionManager and
    all the sessions it creates; it is intentionally stateless beyond its root path."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else _default_root()

    def dir_for(self, key: str) -> Path:
        return self.root / safe_key(key)

    def has(self, key: str) -> bool:
        return (self.dir_for(key) / "journal.ndjsonl").exists()

    # ── WRITE ────────────────────────────────────────────────────────────────
    def reset(self, key: str) -> None:
        """Clear any prior on-disk run of this id. `SessionManager.create` overwrites a prior run
        of the same id in memory; mirror that on disk so a fresh session never appends onto a
        stale journal."""
        d = self.dir_for(key)
        for name in ("journal.ndjsonl", "graph.json", "meta.json"):
            p = d / name
            if p.exists():
                p.unlink()

    def _append_journal(self, key: str, journal: Journal, prior: int) -> int:
        """Append journal entries beyond index `prior` to journal.ndjsonl (append-only). The
        NDJSON schema-version header is written once, when the file is first created; from then on
        only the new entries are appended (no full rewrite). Returns the new persisted count."""
        d = self.dir_for(key)
        d.mkdir(parents=True, exist_ok=True)
        p = d / "journal.ndjsonl"
        entries = journal.entries
        if prior > 0 and p.exists():
            new = entries[prior:]
            lines = [e.model_dump_json() for e in new]
            if lines:
                with p.open("a") as f:
                    f.write("\n".join(lines) + "\n")
        else:
            # first write for this run — header + everything so far (atomic, crash-safe).
            lines = [json.dumps({"schema_version": SCHEMA_VERSION})]
            lines += [e.model_dump_json() for e in entries]
            _atomic_write(p, "\n".join(lines) + "\n")
        return len(entries)

    def persist(self, subject: SubjectRef, engine, *, prior: int, state: str) -> int:
        """Land the current session state on disk: append the journal, atomically rewrite the
        graph cache + meta. Returns the new persisted-journal count (feed it back as `prior`)."""
        key = subject.key
        d = self.dir_for(key)
        d.mkdir(parents=True, exist_ok=True)
        n = self._append_journal(key, engine.journal, prior)
        save_graph(engine.graph, d / "graph.json")
        res = engine.result()
        created = self._existing_created_at(d)
        meta = {
            "schema_version": META_SCHEMA_VERSION,
            "key": key,
            "subject": subject.model_dump(),
            "state": state,
            "outcome": res.close_outcome.value if res.close_outcome else "open",
            "close_outcome": res.close_outcome.value if res.close_outcome else None,
            "phases_run": [p.value for p in res.phases_run],
            # P4: the playbook tunables the run scored under — a disk reopen re-binds the
            # rebuilt store's belief arithmetic with EXACTLY these knobs, so the reopened
            # bundle's earned confidence equals the live one field-for-field.
            "tunables": engine.playbook.tunables.model_dump(),
            "created_at": created or datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        _atomic_write(d / "meta.json", json.dumps(meta, indent=2, default=str))
        return n

    def _existing_created_at(self, d: Path) -> str | None:
        p = d / "meta.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text()).get("created_at")
        except (json.JSONDecodeError, OSError):
            return None

    # ── REOPEN (read-only, from disk) ────────────────────────────────────────
    def load_result(self, key: str) -> RunResult | None:
        """Read journal.ndjsonl + meta.json and `rebuild(journal) -> graph + hypothesis store`,
        returning a RunResult reconstructed purely from the durable journal (the graph.json cache
        is not consulted — the journal is the source of truth)."""
        d = self.dir_for(key)
        jp, mp = d / "journal.ndjsonl", d / "meta.json"
        if not jp.exists() or not mp.exists():
            return None
        journal = Journal.from_ndjson(jp.read_text())
        meta = json.loads(mp.read_text())
        subject = SubjectRef.model_validate(meta["subject"])
        # P4: rebind belief scoring with the tunables the run was persisted under (older
        # metas without the key fall back to the model defaults).
        tun = (Tunables.model_validate(meta["tunables"]) if meta.get("tunables")
               else Tunables())
        graph, store = rebuild(journal, tunables=tun)
        co = meta.get("close_outcome")
        close_outcome = CloseOutcome(co) if co else None
        phases_run = [e.phase_id for e in journal.phase_entries() if e.phase_id]
        return RunResult(
            subject=subject, phases_run=phases_run, graph=graph, hypothesis_store=store,
            journal=journal, confirmed=store.confirmed(), close_outcome=close_outcome)

    def load_bundle(self, key: str) -> dict | None:
        """A read-only reopen payload, snapshot-shaped: `export_bundle` of the disk-rebuilt run
        plus a minimal session envelope (state from meta, `read_only=True`). Returns None when the
        id is not on disk."""
        res = self.load_result(key)
        if res is None:
            return None
        from ..api.bundle import export_bundle  # lazy: avoids an import cycle at module load
        meta = json.loads((self.dir_for(key) / "meta.json").read_text())
        bundle = export_bundle(res)
        return {
            **bundle,
            "session_id": key,
            "state": meta.get("state", "closed"),
            "read_only": True,
            "pending_gate": None,
            "messages": [],
            "events": [],
        }

    def list_disk(self) -> list[dict]:
        """`list_view`-shaped rows for every investigation on disk (for merging into GET /sessions)."""
        out: list[dict] = []
        if not self.root.exists():
            return out
        for d in sorted(self.root.iterdir()):
            mp = d / "meta.json"
            if not mp.exists():
                continue
            try:
                meta = json.loads(mp.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            subject = meta.get("subject", {})
            key = meta.get("key") or f"{subject.get('domain')}:{subject.get('id')}"
            out.append({
                "id": key, "subject": subject,
                "state": meta.get("state", "closed"),
                "outcome": meta.get("outcome", "open"),
                "persisted": True,
            })
        return out
