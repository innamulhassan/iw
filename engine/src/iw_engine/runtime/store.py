"""InvestigationStore — the ONE data-layer module (P6 step 4, part2 §3/§3b). A SIMPLE
CONCRETE file store, per the owner's directive: NO pluggable StorageBackend, no Protocol, no
second implementation — files, no DB. The only structural rule: every byte of investigation
disk I/O lives behind THIS module's small surface (save = `persist`/`reset`, load = `load_result`,
reopen = `load_bundle`, list = `list_disk`/`has`), so a later DB swap is a one-module change.

JOURNAL-AUTHORITATIVE, CACHE-VERIFIED (R-J4's disagreement check, finally real):
  - journal.ndjsonl is THE record — append-only, fsync'd per append, partial-tail-tolerant;
  - graph.json is a projection cache — atomic write-temp-rename on every persist, stamped
    with the journal-head WATERMARK it was projected from. On load, watermark ≠ journal head
    (or an unreadable cache) ⇒ the graph is REBUILT from the journal and the cache rewritten
    (self-heal). A stale or corrupt cache can never poison a reopen.
  - meta.json — subject, state, outcome, tunables, timestamps (atomic rewrite).

Layout — one directory per investigation, keyed by a sanitized `subject.key`:

    <root>/<safe-key>/journal.ndjsonl   append-only NDJSON — the durable truth
    <root>/<safe-key>/graph.json        watermarked projection cache (always rebuildable)
    <root>/<safe-key>/meta.json         subject, state, outcome, timestamps

`<root>` defaults to `engine/data/investigations/` (resolved from the package location, so it is
stable regardless of the working directory). Persistence reuses `graph/persistence.py`
(`save_graph` + its atomic writer) and `journal/journal.py` (`to_ndjson`/`model_dump_json`) —
this module adds no new serialization, only the on-disk layout + the append/reopen wiring.
"""
from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

import iw_engine

from ..domain.playbook import Tunables
from ..domain.subject import SubjectRef
from ..graph.fold import rebuild
from ..graph.graph import Graph
from ..graph.persistence import _atomic_write, save_graph
from ..hypothesis.store import HypothesisStore
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
    """The investigations directory. An explicit `IW_DATA_ROOT` wins (M19c): the package-relative
    fallback resolves via `iw_engine.__file__ -> parents[2]` = the in-repo `engine/` dir (sibling of
    `src/`), which on a pip-install points INTO site-packages, where no writable `data/` exists — a
    deployment sets the env var. The dev/test tree needs no env and keeps the in-repo default."""
    base = os.environ.get("IW_DATA_ROOT")
    if base:
        return Path(base) / "investigations"
    return Path(iw_engine.__file__).resolve().parents[2] / "data" / "investigations"


def _read_meta(mp: Path) -> dict:
    """Read + VALIDATE meta.json (F8 / M19a). Mirrors `Journal.from_ndjson` and `Graph.from_dict`:
    an unknown FUTURE `schema_version` is REFUSED LOUDLY — misreading a newer meta (whose
    `state`/`outcome`/`tunables` semantics may have moved) would silently serve a WRONG reopen.
    Before M19 the meta reader alone did a bare `.get`, so the three on-disk artifacts disagreed on
    version policy; now they tell one coherent story. A meta with no `schema_version` is a
    pre-versioning file — treated as current, so old runs still reopen."""
    meta = json.loads(mp.read_text())
    version = meta.get("schema_version", META_SCHEMA_VERSION)
    if not isinstance(version, int) or version > META_SCHEMA_VERSION:
        raise ValueError(f"meta.json schema_version {version!r} is newer than this engine "
                         f"understands (max {META_SCHEMA_VERSION})")
    return meta


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
        only the new entries are appended (no full rewrite). Every append is FSYNC'd (P6 step 4:
        the journal is THE record — a crash after persist() returns must never lose an entry;
        a crash mid-append leaves at most one partial tail line, which the loader tolerates).
        Returns the new persisted count."""
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
                    f.flush()
                    os.fsync(f.fileno())
        else:
            # first write for this run — header + everything so far (atomic + fsync'd). The
            # header carries its own kind ("header") so no on-disk line is kind-less (CLEAN rule),
            # matching Journal.to_ndjson; from_ndjson strips it by its schema_version key.
            lines = [json.dumps({"schema_version": SCHEMA_VERSION, "kind": "header"})]
            lines += [e.model_dump_json() for e in entries]
            _atomic_write(p, "\n".join(lines) + "\n")
        return len(entries)

    def persist(self, subject: SubjectRef, engine, *, prior: int, state: str,
                outcome: str | None = None) -> int:
        """Land the current session state on disk: append the journal (fsync'd), atomically
        rewrite the graph cache — stamped with the journal-head watermark it projects — + meta.
        Returns the new persisted-journal count (feed it back as `prior`). `outcome` is the
        session's own terminal label: an errored run that never reached an engine terminal passes
        'error' (M18) so meta never records a crash as 'open'; None falls back to the engine's
        derived `close_outcome or 'open'` (a running or healthy run — byte-identical to before)."""
        key = subject.key
        d = self.dir_for(key)
        d.mkdir(parents=True, exist_ok=True)
        n = self._append_journal(key, engine.journal, prior)
        head = max((e.seq for e in engine.journal.entries), default=0)
        save_graph(engine.graph, d / "graph.json", journal_seq=head)
        res = engine.result()
        created = self._existing_created_at(d)
        meta = {
            "schema_version": META_SCHEMA_VERSION,
            "key": key,
            "subject": subject.model_dump(),
            "state": state,
            "outcome": outcome if outcome is not None else (res.close_outcome or "open"),
            "close_outcome": res.close_outcome,
            "origin_node": res.origin_node,   # the subject_node role binding's id (P7 step 5)
            "phases_run": list(res.phases_run),
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
    def _cached_graph(self, d: Path, head: int) -> Graph | None:
        """The graph cache — served ONLY when its watermark equals the journal head (R-J4's
        disagreement check, part2 §3). A missing, unreadable, foreign-schema or STALE cache
        returns None: the caller rebuilds from the journal (the truth) and rewrites the cache.
        The watermark verifies freshness; for any deeper doubt the journal remains the
        authority — delete graph.json and it is reprojected."""
        p = d / "graph.json"
        if not p.exists():
            return None
        try:
            data = json.loads(p.read_text())
        except (json.JSONDecodeError, OSError):
            return None                        # unreadable cache — never trusted, never fatal
        if data.get("journal_seq") != head:
            return None                        # stale (or pre-watermark) — rebuild from truth
        try:
            return Graph.from_dict(data)
        except (ValidationError, KeyError, TypeError):
            return None                        # a cache from a different schema era

    def _reopen(self, key: str) -> tuple[RunResult, dict] | None:
        """The shared reopen path, JOURNAL-AUTHORITATIVE: the journal is always read and is the
        truth; the graph cache is consulted only through the watermark check (`_cached_graph`).
        On a stale/corrupt cache the graph is rebuilt from the journal and the cache REWRITTEN
        (self-heal), so a bad cache can never poison a reopen — R-J4's two crash models unify
        (journal = append-crash-tolerant; cache = atomic-rewrite, disagreement-checked).
        The hypothesis store is always replayed from the journal's phase deltas (it is small;
        `HypothesisStore.apply` is graph-independent — scoring binds the graph at query time).
        Returns (RunResult, meta) so meta.json is parsed exactly ONCE per reopen (M19b): both
        `load_result` and `load_bundle` share it, ending load_bundle's second re-parse."""
        d = self.dir_for(key)
        jp, mp = d / "journal.ndjsonl", d / "meta.json"
        if not jp.exists() or not mp.exists():
            return None
        journal = Journal.from_ndjson(jp.read_text())
        meta = _read_meta(mp)                # read + validated once (F8 loud-refuse on a future version)
        subject = SubjectRef.model_validate(meta["subject"])
        # P4: rebind belief scoring with the tunables the run was persisted under (older
        # metas without the key fall back to the model defaults).
        tun = (Tunables.model_validate(meta["tunables"]) if meta.get("tunables")
               else Tunables())
        head = max((e.seq for e in journal.entries), default=0)
        graph = self._cached_graph(d, head)
        if graph is not None:
            store = HypothesisStore()
            for entry in journal.phase_entries():
                store.apply(entry.delta.hypotheses_updated, entry.seq)
            store.bind_scoring(graph, tun)
        else:
            graph, store = rebuild(journal, tunables=tun)
            save_graph(graph, d / "graph.json", journal_seq=head)   # self-heal the cache
        close_outcome = meta.get("close_outcome") or None
        phases_run = [e.phase_id for e in journal.phase_entries() if e.phase_id]
        res = RunResult(
            subject=subject, phases_run=phases_run, graph=graph, hypothesis_store=store,
            journal=journal, confirmed=store.confirmed(), close_outcome=close_outcome,
            origin_node=meta.get("origin_node"))
        return res, meta

    def load_result(self, key: str) -> RunResult | None:
        """Reopen from disk to a `RunResult` (journal-authoritative — see `_reopen`). None when
        the investigation is not on disk."""
        r = self._reopen(key)
        return r[0] if r is not None else None

    def load_bundle(self, key: str, playbook=None) -> dict | None:
        """A read-only reopen payload, snapshot-shaped: `export_bundle` of the disk-rebuilt run
        plus a minimal session envelope (state from meta, `read_only=True`). Returns None when the
        id is not on disk. Shares `_reopen`, so meta.json is read exactly once (M19b). `playbook`
        (passed by SessionManager.reopen, which holds it) supplies the full declared phase rail
        (M22); absent, the rail falls back to the reached phases (a closed run shows all it ran)."""
        r = self._reopen(key)
        if r is None:
            return None
        res, meta = r
        # lazy import: avoids an import cycle at module load
        from ..api.bundle import export_bundle, phase_rail
        bundle = export_bundle(res)
        rail = (phase_rail(playbook) if playbook is not None
                else [{"id": p, "focus": True} for p in res.phases_run])
        return {
            **bundle,
            "session_id": key,
            "state": meta.get("state", "closed"),
            "read_only": True,
            "phase_rail": rail,
            "pending_gate": None,
            "pending_review": None,
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
                meta = _read_meta(mp)
            except (json.JSONDecodeError, OSError, ValueError):
                # unreadable, malformed, OR a future-version meta (F8): skip the row rather than
                # crash the whole listing — reopen refuses loudly, the list stays resilient.
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
