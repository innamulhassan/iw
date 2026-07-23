"""Persistence roundtrip (demo-persistence P6 slice). A live investigation lands on disk as it
drives; a FRESH SessionManager (no shared memory — a simulated backend restart) reopens the SAME
investigation read-only from disk, and the reopened bundle equals the live one field-for-field
because both are `export_bundle` over the journal (the durable source of truth).

Drives the real `code_regression` scenario through the interactive backend (the same twin the
golden suite drives in batch), so this exercises the production write/reopen wiring end-to-end.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from iw_engine.domain.subject import SubjectRef
from iw_engine.graph import rebuild
from iw_engine.journal.journal import Journal
from iw_engine.runtime.scenarios import build_manager
from iw_engine.runtime.session import GateDecision, SessionState
from iw_engine.runtime.store import META_SCHEMA_VERSION, InvestigationStore, _default_root, safe_key

INCIDENT = "INC-4821"
KEY = f"app-incident:{INCIDENT}"

# the fields both a live snapshot and a disk reopen derive purely from the journal — they must
# be byte-for-byte identical across a restart.
_BUNDLE_FIELDS = ("subject", "outcome", "phases", "graph", "hypotheses", "journal", "postmortem")


def _canon(v):
    """Canonicalize timestamps to the same UTC instant so a live/disk comparison is format-blind.
    A datetime inside a free-form node `props` dict survives the NDJSON journal roundtrip as an
    ISO string, and pydantic's 'Z' form differs textually from `datetime.isoformat()`'s '+00:00' —
    same moment, different text. Everything else compares by value unchanged."""
    if isinstance(v, datetime):
        return v.astimezone(UTC).isoformat()
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(UTC).isoformat()
        except ValueError:
            return v
    if isinstance(v, dict):
        return {k: _canon(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_canon(x) for x in v]
    return v


def test_safe_key_sanitizes_the_colon():
    assert safe_key("app-incident:INC-4821") == "app-incident_INC-4821"
    assert ":" not in safe_key(KEY) and "/" not in safe_key("a/b:c")


def _subject() -> SubjectRef:
    return SubjectRef(domain="app-incident", id=INCIDENT, kind="incident")


def test_live_run_persists_and_reopens_read_only_after_restart(tmp_path):
    root = tmp_path / "investigations"

    # ── a live run: drive to the write-gate, then approve to close ──────────────
    mgr = build_manager(store=InvestigationStore(root))
    session = mgr.create(_subject())
    assert session.state == SessionState.SUSPENDED       # paused at the human write-gate

    inv_dir = root / safe_key(KEY)
    for name in ("journal.ndjsonl", "graph.json", "meta.json"):
        assert (inv_dir / name).exists(), f"{name} must be written while the run is live"

    session.answer_gate(GateDecision.APPROVE, actor="alice@oncall")
    assert session.state == SessionState.CLOSED
    live = session.snapshot()
    assert live["graph"]["nodes"] and live["hypotheses"]  # a real, non-empty investigation

    # the durable journal on disk replays to exactly the live graph + hypothesis store
    disk_journal = Journal.from_ndjson((inv_dir / "journal.ndjsonl").read_text())
    g, store = rebuild(disk_journal)
    assert {n.id for n in g.nodes.values()} == {n["id"] for n in live["graph"]["nodes"]}
    assert [h.id for h in store.ranked()] == [h["id"] for h in live["hypotheses"]]

    # ── simulate a backend restart: a brand-new manager, no shared memory ───────
    fresh = build_manager(store=InvestigationStore(root))
    assert fresh.get(KEY) is None, "nothing is in the fresh manager's memory"

    reopened = fresh.reopen(KEY)                          # lazy-load from disk (the GET /sessions/{id} path)
    assert reopened is not None and reopened["read_only"] is True
    assert reopened["session_id"] == KEY

    # the reopened bundle equals the live one on every journal-derived field (served-JSON level)
    for f in _BUNDLE_FIELDS:
        assert _canon(reopened[f]) == _canon(live[f]), f"reopened {f!r} differs from the live bundle"

    # GET /sessions merges the on-disk investigation even though memory is empty
    listed = {r["id"]: r for r in fresh.list()}
    assert KEY in listed and listed[KEY]["persisted"] is True


def test_reopen_of_unknown_id_is_none(tmp_path):
    fresh = build_manager(store=InvestigationStore(tmp_path))
    assert fresh.reopen("app-incident:NOPE") is None


def test_append_only_journal_grows_without_rewriting_the_header(tmp_path):
    """The NDJSON schema-version header is written once; subsequent folds append (not rewrite)."""
    root = tmp_path / "investigations"
    mgr = build_manager(store=InvestigationStore(root))
    mgr.create(_subject())
    text = (root / safe_key(KEY) / "journal.ndjsonl").read_text()
    assert text.count('"schema_version"') == 1
    # header + at least the phases driven to the gate, each its own NDJSON line
    assert len([ln for ln in text.splitlines() if ln.strip()]) > 1
    # and it re-parses through the existing loader
    assert Journal.from_ndjson(text).entries


# ── P6 step 4: journal-authoritative hardening ─────────────────────────────────
def _driven_store(tmp_path):
    """A completed run on disk + its live bundle, via the production write path."""
    root = tmp_path / "investigations"
    mgr = build_manager(store=InvestigationStore(root))
    session = mgr.create(_subject())
    session.answer_gate(GateDecision.APPROVE)
    return root, session.snapshot()


def test_graph_cache_carries_the_journal_watermark(tmp_path):
    root, _ = _driven_store(tmp_path)
    d = root / safe_key(KEY)
    cache = json.loads((d / "graph.json").read_text())
    journal = Journal.from_ndjson((d / "journal.ndjsonl").read_text())
    head = max(e.seq for e in journal.entries)
    assert cache["journal_seq"] == head, "the cache must be stamped with the head it projects"


def test_stale_cache_is_ignored_and_healed(tmp_path):
    """R-J4's disagreement check: a cache whose watermark disagrees with the journal head is
    NEVER served — the reopen equals the journal truth, and the cache is rewritten."""
    root, live = _driven_store(tmp_path)
    d = root / safe_key(KEY)
    cache = json.loads((d / "graph.json").read_text())
    poisoned = {**cache, "journal_seq": cache["journal_seq"] - 1,
                "nodes": [], "assertions": [], "edges": []}   # stale AND gutted
    (d / "graph.json").write_text(json.dumps(poisoned))

    fresh = build_manager(store=InvestigationStore(root))
    reopened = fresh.reopen(KEY)
    assert reopened is not None
    for f in _BUNDLE_FIELDS:
        assert _canon(reopened[f]) == _canon(live[f]), \
            f"a stale cache poisoned the reopened {f!r}"
    healed = json.loads((d / "graph.json").read_text())
    assert healed["journal_seq"] == cache["journal_seq"] and healed["nodes"], \
        "the stale cache must be rewritten from the journal (self-heal)"


def test_corrupt_cache_never_poisons_reopen(tmp_path):
    root, live = _driven_store(tmp_path)
    d = root / safe_key(KEY)
    (d / "graph.json").write_text("{ not json at all")
    fresh = build_manager(store=InvestigationStore(root))
    reopened = fresh.reopen(KEY)
    assert reopened is not None
    for f in _BUNDLE_FIELDS:
        assert _canon(reopened[f]) == _canon(live[f])
    assert json.loads((d / "graph.json").read_text())["nodes"], "cache healed"


def test_fresh_cache_hit_equals_journal_rebuild(tmp_path):
    """Both load paths — watermark-hit (cache) and miss (rebuild) — must serve the SAME
    bundle: the cache is an optimization, never an alternate truth."""
    root, _ = _driven_store(tmp_path)
    d = root / safe_key(KEY)
    via_cache = build_manager(store=InvestigationStore(root)).reopen(KEY)
    (d / "graph.json").unlink()                                # force the rebuild path
    via_journal = build_manager(store=InvestigationStore(root)).reopen(KEY)
    for f in _BUNDLE_FIELDS:
        assert _canon(via_cache[f]) == _canon(via_journal[f])


def test_journal_appends_are_fsynced(tmp_path, monkeypatch):
    import os as _os
    calls = []
    real = _os.fsync
    monkeypatch.setattr("os.fsync", lambda fd: (calls.append(fd), real(fd))[1])
    _driven_store(tmp_path)
    assert calls, "every journal append (and atomic rewrite) must fsync before returning"


def test_partial_tail_journal_still_reopens(tmp_path):
    """A crash mid-append leaves a truncated last line — the reopen tolerates it (the
    surviving complete entries ARE the record)."""
    root, _ = _driven_store(tmp_path)
    d = root / safe_key(KEY)
    with (d / "journal.ndjsonl").open("a") as f:
        f.write('{"seq": 999, "ts": "2026-07-')                # the torn tail
    (d / "graph.json").unlink()                                # and no cache to lean on
    fresh = build_manager(store=InvestigationStore(root))
    reopened = fresh.reopen(KEY)
    assert reopened is not None and reopened["read_only"] is True
    assert reopened["graph"]["nodes"], "the surviving journal entries still serve the reopen"


def test_errored_run_persists_outcome_error_not_open(tmp_path):
    """M18: a crashed drive persists `outcome='error'` in meta.json (and the on-disk list row) —
    the disk record of a crashed run must never say 'open'. Pairs with the in-memory _outcome/
    list_view fix (test_transitions.test_errored_drive_closes_with_error_cause_and_outcome)."""
    import pathlib

    import iw_engine
    from iw_engine.runtime import load_playbook
    from iw_engine.runtime.session import InvestigationSession

    class _BoomPlanner:
        def plan(self, ctx):
            raise RuntimeError("live transport died mid-drive")

    root = tmp_path / "investigations"
    pb = load_playbook(pathlib.Path(iw_engine.__file__).parent / "playbooks" / "incident.yaml")
    session = InvestigationSession(_subject(), pb, _BoomPlanner(),
                                   store=InvestigationStore(root))
    session._drive_and_clear()                                  # crash the drive → error terminal
    assert session.state == SessionState.CLOSED

    meta = json.loads((root / safe_key(KEY) / "meta.json").read_text())
    assert meta["outcome"] == "error"                          # NOT the default "open"
    row = {r["id"]: r for r in InvestigationStore(root).list_disk()}[KEY]
    assert row["outcome"] == "error"                           # GET /sessions never shows a phantom 'open'


# ── M19: DB-seam hygiene ───────────────────────────────────────────────────────
def test_future_meta_version_is_refused_loudly(tmp_path):
    """M19a / F8: a meta.json from a NEWER schema is REFUSED on reopen, never misread — the same
    loud-refuse the journal (`from_ndjson`) and graph (`from_dict`) already do, so all three
    on-disk artifacts tell ONE version story. The list path stays resilient (skips the untrusted
    row rather than crashing the whole GET /sessions)."""
    root, _ = _driven_store(tmp_path)
    mp = root / safe_key(KEY) / "meta.json"
    meta = json.loads(mp.read_text())
    meta["schema_version"] = META_SCHEMA_VERSION + 1
    mp.write_text(json.dumps(meta))
    store = InvestigationStore(root)
    with pytest.raises(ValueError, match="newer than this engine"):
        store.load_result(KEY)
    with pytest.raises(ValueError, match="newer than this engine"):
        store.load_bundle(KEY)
    assert store.list_disk() == []                             # untrusted row skipped, list not fatal


def test_reopen_reads_meta_once(tmp_path, monkeypatch):
    """M19b: `load_bundle` shares `_reopen` with `load_result`, so meta.json is read + validated
    exactly ONCE per reopen — it used to re-parse meta.json a second time after load_result."""
    root, _ = _driven_store(tmp_path)
    from iw_engine.runtime import store as store_mod
    calls = {"n": 0}
    real = store_mod._read_meta

    def _spy(mp):
        calls["n"] += 1
        return real(mp)

    monkeypatch.setattr(store_mod, "_read_meta", _spy)
    assert InvestigationStore(root).load_bundle(KEY) is not None
    assert calls["n"] == 1, "meta.json must be read via _reopen exactly once (was parsed twice)"


def test_data_root_env_override(monkeypatch, tmp_path):
    """M19c: `IW_DATA_ROOT` relocates the store root off the package-relative default (which points
    into site-packages on a pip-install, where no writable `data/` exists)."""
    monkeypatch.setenv("IW_DATA_ROOT", str(tmp_path / "custom"))
    assert _default_root() == tmp_path / "custom" / "investigations"
    monkeypatch.delenv("IW_DATA_ROOT")
    assert _default_root().parts[-2:] == ("data", "investigations")   # in-repo fallback
