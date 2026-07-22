"""Persistence roundtrip (demo-persistence P6 slice). A live investigation lands on disk as it
drives; a FRESH SessionManager (no shared memory — a simulated backend restart) reopens the SAME
investigation read-only from disk, and the reopened bundle equals the live one field-for-field
because both are `export_bundle` over the journal (the durable source of truth).

Drives the real `code_regression` scenario through the interactive backend (the same twin the
golden suite drives in batch), so this exercises the production write/reopen wiring end-to-end.
"""
from __future__ import annotations

from datetime import UTC, datetime

from iw_engine.domain.subject import SubjectRef
from iw_engine.graph import rebuild
from iw_engine.journal.journal import Journal
from iw_engine.runtime.scenarios import build_manager
from iw_engine.runtime.session import GateDecision, SessionState
from iw_engine.runtime.store import InvestigationStore, safe_key

INCIDENT = "INC-4821"
KEY = f"app-incident:{INCIDENT}"

# the fields both a live snapshot and a disk reopen derive purely from the journal — they must
# be byte-for-byte identical across a restart.
_BUNDLE_FIELDS = ("subject", "outcome", "phases", "graph", "ledger", "journal", "postmortem")


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
    assert live["graph"]["nodes"] and live["ledger"]      # a real, non-empty investigation

    # the durable journal on disk replays to exactly the live graph+ledger
    disk_journal = Journal.from_ndjson((inv_dir / "journal.ndjsonl").read_text())
    g, led = rebuild(disk_journal)
    assert {n.id for n in g.nodes.values()} == {n["id"] for n in live["graph"]["nodes"]}
    assert [h.id for h in led.ranked()] == [h["id"] for h in live["ledger"]]

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
