"""Cross-platform reliability of the `iw.py` controller (init / start / stop).

`iw.py` lives at the repo root (outside the engine package) and drives BOTH macOS/Linux and
Windows. These tests load it by path and pin the platform-sensitive seams that decide whether
init/start/stop actually work on Windows as well as Mac:

  - `_rmtree` survives the Windows read-only-file trap (else `init --force` fails on Windows).
  - `_popen_detached` wires a real detach (POSIX new-session / Windows own-process-group) so a
    started service survives the launcher and `stop` can reap the whole tree.
  - the pidfile roundtrip (`_load_state`/`_save_state`) that start/stop/status hang off is
    corruption-tolerant.
  - the venv/npm/arg-parsing helpers resolve per-platform.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import stat

import pytest

_IW_PATH = pathlib.Path(__file__).resolve().parents[3] / "iw.py"


def _load_iw():
    spec = importlib.util.spec_from_file_location("iw_cli_under_test", _IW_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


iw = _load_iw()


# ── _rmtree: the Windows read-only-file trap that breaks `init --force` ─────────────
def test_rmtree_removes_tree_with_readonly_file(tmp_path):
    root = tmp_path / "venv-like"
    (root / "sub").mkdir(parents=True)
    f = root / "sub" / "pyvenv.cfg"
    f.write_text("home = /x")
    os.chmod(f, stat.S_IREAD)   # the read-only bit uv/npm leave behind on Windows
    iw._rmtree(root)            # must NOT raise, and must fully remove the tree
    assert not root.exists()


def test_rmtree_missing_path_is_noop(tmp_path):
    iw._rmtree(tmp_path / "does-not-exist")   # no error


# ── _popen_detached: a real detach so a started service outlives the launcher ───────
def test_popen_detached_wires_platform_detach(tmp_path, monkeypatch):
    captured: dict = {}

    class _FakeProc:
        pid = 4242

    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured.update(kwargs)
        return _FakeProc()

    monkeypatch.setattr(iw.subprocess, "Popen", _fake_popen)
    log = tmp_path / "svc.log"
    with log.open("ab") as fd:
        pid = iw._popen_detached(["uv", "run", "uvicorn"], tmp_path, fd, {"K": "V"})

    assert pid == 4242
    assert captured["cmd"] == ["uv", "run", "uvicorn"]
    assert captured["env"] == {"K": "V"}
    if iw._HAS_KILLPG:                       # POSIX: own session -> killpg at stop
        assert captured["start_new_session"] is True
        assert captured["close_fds"] is True
    elif iw.IS_WINDOWS:                       # Windows: own process group -> taskkill /T
        assert "creationflags" in captured


# ── pidfile roundtrip: the state start/stop/status all key off ─────────────────────
def test_state_roundtrip_and_corruption_tolerance(tmp_path, monkeypatch):
    state_file = tmp_path / ".iw" / "state.json"
    monkeypatch.setattr(iw, "STATE", state_file)

    assert iw._load_state() == {}            # absent -> empty (no crash)
    iw._save_state({"backend": {"pid": 123, "host": "127.0.0.1", "port": 8099}})
    assert state_file.exists()
    assert iw._load_state()["backend"]["pid"] == 123

    state_file.write_text("{ this is not json")
    assert iw._load_state() == {}            # corrupt -> empty, start/stop still work


# ── per-platform helper resolution ─────────────────────────────────────────────────
def test_venv_python_is_platform_correct(tmp_path):
    py = iw._venv_python(tmp_path / "venv")
    assert py.name == ("python.exe" if iw.IS_WINDOWS else "python")
    assert py.parent.name == iw._VENV_BIN   # Scripts on Windows, bin on POSIX


def test_npm_cmd_returns_a_command_string():
    cmd = iw._npm_cmd()
    assert isinstance(cmd, str) and cmd


# ── arg parsing for every subcommand (init/start/stop/restart/status/logs) ─────────
@pytest.mark.parametrize("cmd", ["init", "start", "stop", "restart", "status", "logs"])
def test_build_parser_accepts_each_subcommand(cmd):
    ns = iw.build_parser().parse_args([cmd])
    assert callable(ns.func)


def test_logs_parser_flags():
    ns = iw.build_parser().parse_args(["logs", "backend", "-f", "-n", "10"])
    assert ns.which == "backend" and ns.follow is True and ns.lines == 10


def test_start_parser_defaults_ports():
    ns = iw.build_parser().parse_args(["start"])
    assert ns.backend_port == iw.BACKEND_PORT and ns.frontend_port == iw.FRONTEND_PORT
