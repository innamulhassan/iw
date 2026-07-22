#!/usr/bin/env python3
"""iw — Investigation Workbench controller (cross-platform).

One script to bring the Investigation Workbench up and down. Backend (FastAPI/uvicorn)
and frontend (Vite) are each a detached child, tracked in a tiny JSON pidfile in this
repo so start/stop/status/logs survive the shell that launched them.

PREREQS: `uv` (backend venv + deps) and `npm` (frontend deps). uv manages the engine's
.venv itself — so there's no venv/pip to set up by hand, and the Windows pip `--user`
trap can't happen. Install uv once:
    macOS/Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh
    Windows:      powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
    (uv can also fetch its own Python 3.11+ — no system Python needed for the backend.)

Works on macOS, Linux, and Windows. On Windows use:

    python iw.py init
    python iw.py start
    python iw.py stop
    python iw.py status
    python iw.py logs

On macOS/Linux you can also run it directly (./iw.py ...) since it has a shebang and the
executable bit is committed.

    iw.py init      install backend (uv sync) + frontend (npm install) deps
    iw.py start     bring up both services (or one with --backend-only / --frontend-only)
    iw.py stop      stop both (or one)
    iw.py restart   stop then start
    iw.py status    show running services and listening ports
    iw.py logs      tail logs for both (or one); -f to follow

Defaults:
    backend   uv run uvicorn on 127.0.0.1:8099
    frontend  vite          on 127.0.0.1:5173
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from typing import NoReturn, Optional

ROOT = pathlib.Path(__file__).resolve().parent
ENGINE = ROOT / "engine"
WORKBENCH = ROOT / "workbench"
STATE = ROOT / ".iw" / "state.json"

BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8099
FRONTEND_PORT = 5173

# Windows puts venv executables in Scripts/; POSIX uses bin/. npm is npm.cmd on Windows.
IS_WINDOWS = os.name == "nt" or sys.platform.startswith("win")
_VENV_BIN = "Scripts" if IS_WINDOWS else "bin"

# Process-tree kill: POSIX has process groups; Windows has taskkill /T. SIGKILL is POSIX-only.
_HAS_SIGKILL = hasattr(signal, "SIGKILL")
_HAS_KILLPG = hasattr(os, "killpg") and hasattr(os, "setsid")


# ─── helpers ────────────────────────────────────────────────────────────────────
def _supports_color() -> bool:
    if IS_WINDOWS:
        # ANSI works on Windows 10+ terminals (and Python 3.12 enables VT mode). Be optimistic
        # but fall back to plain text if stdout isn't a tty.
        return sys.stdout.isatty()
    return sys.stdout.isatty()


_COLOR = _supports_color()


def _c(code: str, msg: str) -> str:
    return f"\033[{code}m{msg}\033[0m" if _COLOR else msg


def _banner(msg: str) -> None:
    print(f"\n{_c('1;36', '▶ ' + msg)}")


def _ok(msg: str) -> None:
    print(f"  {_c('1;32', '✓')} {msg}")


def _warn(msg: str) -> None:
    print(f"  {_c('1;33', '!')} {msg}")


def _die(msg: str, code: int = 1) -> NoReturn:
    print(_c("1;31", f"✗ {msg}"), file=sys.stderr)
    sys.exit(code)


def _venv_python(venv: pathlib.Path) -> pathlib.Path:
    return venv / _VENV_BIN / ("python.exe" if IS_WINDOWS else "python")


def _npm_cmd() -> str:
    """Return the npm executable name. On Windows the real binary is npm.cmd and
    subprocess won't find a bare 'npm' without shell=True — which we want to avoid."""
    if IS_WINDOWS:
        # shutil.which resolves npm.cmd from PATH
        for cand in ("npm.cmd", "npm"):
            found = shutil.which(cand)
            if found:
                return found
    found = shutil.which("npm")
    return found or "npm"


def _die_if_missing(dep: str, how: str) -> None:
    if shutil.which(dep) is None:
        _die(f"{dep!r} not found on PATH. Install: {how}")


def _rmtree(path: pathlib.Path) -> None:
    """Remove a directory tree, robust to the Windows 'read-only file' trap.

    A bare `shutil.rmtree` raises `PermissionError` on Windows when a tree contains read-only
    files — which `.venv` (uv-materialised packages) and `node_modules` routinely do. That
    makes `init --force` fail on Windows while working on macOS/Linux (where a directory's write
    bit, not the file's, governs unlink). The handler clears the read-only bit and retries the
    failed operation, so `--force` reinstall is reliable on every platform. POSIX is unaffected —
    the handler only fires on the errors Windows raises."""
    if not path.exists():
        return

    def _clear_readonly_and_retry(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass  # best-effort: a truly undeletable path is surfaced by the caller's next check

    # Python 3.12 renamed rmtree's `onerror` (func, path, exc_info) to `onexc` (func, path, exc).
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_clear_readonly_and_retry)
    else:
        shutil.rmtree(path, onerror=_clear_readonly_and_retry)


# ─── state (pidfile) ────────────────────────────────────────────────────────────
def _load_state() -> dict:
    if not STATE.exists():
        return {}
    try:
        return json.loads(STATE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


def _is_running(pid: int) -> bool:
    """Is `pid` alive? On POSIX, `kill(pid, 0)` is the cheap check. On Windows there's
    no signal system, so probe via tasklist /FI (OpenProcess needs ctypes; this is simpler
    and dependency-free)."""
    if pid <= 0:
        return False
    if IS_WINDOWS:
        r = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH", "/FO", "CSV"],
            capture_output=True, text=True,
        )
        # tasklist prints the header-less CSV row for the pid if it exists, else "INFO: ..."
        out = r.stdout.strip()
        return bool(out) and not out.startswith("INFO:")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# ─── port checks ────────────────────────────────────────────────────────────────
def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def _wait_for_port(host: str, port: int, timeout: float = 20.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_in_use(host, port):
            return True
        time.sleep(0.4)
    return False


# ─── install ────────────────────────────────────────────────────────────────────
def _uv() -> str:
    """Resolve the uv binary. uv is a hard requirement for the backend — it manages the
    engine venv + installs from uv.lock, sidestepping the pip `--user` trap entirely (uv
    manages its own environment and never reads global pip.ini)."""
    found = shutil.which("uv")
    if found:
        return found
    # common install locations if not on PATH
    for cand in (pathlib.Path.home() / ".local" / "bin" / "uv",
                 pathlib.Path.home() / ".cargo" / "bin" / "uv"):
        if cand.exists():
            return str(cand)
    _die("uv not found. Install it (one command, no admin):  "
         "https://docs.astral.sh/uv/getting-started/installation/\n"
         "  macOS/Linux:  curl -LsSf https://astral.sh/uv/install.sh | sh\n"
         "  Windows:      powershell -c \"irm https://astral.sh/uv/install.ps1 | iex\"")


def install_backend(force: bool = False) -> bool:
    """Create engine/.venv and install the backend deps via `uv sync` (the [server,dev]
    extras). uv creates the venv, resolves the lockfile, and installs in one fast step —
    and never touches global pip config, so the Windows pip `--user` error can't happen.

    Returns True if (re)installed. Idempotent: a synced .venv is left alone unless `force`."""
    uv = _uv()
    venv = ENGINE / ".venv"
    py = _venv_python(venv)

    if not force and venv.exists() and py.exists():
        # probe: can we import the server? if so, the install is good — skip the sync.
        r = subprocess.run([str(py), "-c", "import iw_engine.api.server"],
                           cwd=ENGINE, capture_output=True)
        if r.returncode == 0:
            _ok(f"backend deps already installed ({venv.relative_to(ROOT)})")
            return False

    if force and venv.exists():
        _rmtree(venv)

    _banner("installing backend deps via uv sync (uv.lock, [server,dev] extras)")
    # --extra installs the optional-dependency groups; uv sync is idempotent and fast.
    subprocess.run([uv, "sync", "--extra", "server", "--extra", "dev"], cwd=ENGINE, check=True)
    _ok("backend deps installed")
    return True


def install_frontend(force: bool = False) -> bool:
    """Run npm install in workbench/. Idempotent: leaves a populated node_modules alone."""
    npm = _npm_cmd()
    if shutil.which(npm) is None and not pathlib.Path(npm).exists():
        _die(f"'npm' not found on PATH. Install Node: https://nodejs.org/en/download/")

    nm = WORKBENCH / "node_modules"
    pkg = WORKBENCH / "package.json"
    if not pkg.exists():
        _die(f"no package.json at {pkg}")
    if not force and nm.exists() and any(nm.iterdir()):
        _ok(f"frontend deps already installed ({nm.relative_to(ROOT)})")
        return False

    if force and nm.exists():
        _rmtree(nm)

    _banner("installing frontend deps (npm install)")
    subprocess.run([npm, "install"], cwd=WORKBENCH, check=True)
    _ok("frontend deps installed")
    return True


def cmd_init(args) -> int:
    _banner("INITIAL SETUP")
    if args.backend_only and args.frontend_only:
        _die("--backend-only and --frontend-only are mutually exclusive")
    if not args.frontend_only:
        install_backend(force=args.force)
    if not args.backend_only:
        install_frontend(force=args.force)
    _banner("setup complete")
    print("    next: python iw.py start")
    return 0


# ─── process launch / kill (cross-platform) ────────────────────────────────────
def _popen_detached(cmd: list[str], cwd: pathlib.Path, log_fd, env: dict) -> int:
    """Launch a detached child that survives this script. On POSIX we make it its own
    session leader (so the pid == pgid, enabling killpg later). On Windows there are no
    sessions; we rely on `taskkill /T` (tree kill) at stop time using the stored pid."""
    kwargs = dict(
        cwd=str(cwd), stdout=log_fd, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL, env=env,
    )
    if _HAS_KILLPG:
        kwargs["start_new_session"] = True
        kwargs["close_fds"] = True
    elif IS_WINDOWS:
        # Mirror POSIX setsid: put the child in its OWN process group so it detaches from this
        # launcher's console (a later Ctrl+C on the launcher won't fell the services) and
        # `taskkill /T` at stop time reaps the whole tree deterministically. CREATE_NEW_PROCESS_GROUP
        # is defined only on Windows; guard so this stays a no-op cross-platform.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    proc = subprocess.Popen(cmd, **kwargs)
    return proc.pid


def _kill_tree(pid: int) -> None:
    """Best-effort kill of a process AND its children. POSIX: SIGTERM the whole group
    (the launcher was its own session leader). Windows: taskkill /T /PID (tree kill)."""
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/T", "/PID", str(pid)],
                       capture_output=True, text=True)
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        # not a group leader (older launch) — kill just the pid
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass


def _kill_tree_hard(pid: int) -> None:
    """Escalation path after graceful TERM didn't land."""
    if IS_WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                       capture_output=True, text=True)
        return
    sig = _HAS_SIGKILL and signal.SIGKILL or signal.SIGTERM
    try:
        os.killpg(pid, sig)
    except OSError:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


# ─── start ──────────────────────────────────────────────────────────────────────
def _start_backend(host: str, port: int) -> Optional[int]:
    uv = _uv()
    venv = ENGINE / ".venv"
    if not venv.exists():
        _warn("backend venv missing — run `python iw.py init` first; skipping backend")
        return None

    if _port_in_use(host, port):
        _warn(f"port {port} already in use — backend assumed up")
        return None

    log = ROOT / ".iw" / "backend.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log_fd = log.open("ab")  # append so restarts keep a history
    env = os.environ.copy()
    # `uv run` resolves the .venv python automatically — no path juggling for uvicorn.exe.
    cmd = [uv, "run", "uvicorn", "iw_engine.api.server:create_server", "--factory",
           "--host", host, "--port", str(port)]
    pid = _popen_detached(cmd, ENGINE, log_fd, env)
    log_fd.close()
    return pid


def _start_frontend(host: str, port: int) -> Optional[int]:
    npm = _npm_cmd()
    if shutil.which(npm) is None and not pathlib.Path(npm).exists():
        _warn("npm missing — run `python iw.py init`; skipping frontend")
        return None
    if not (WORKBENCH / "node_modules").exists():
        _warn("frontend node_modules missing — run `python iw.py init`; skipping frontend")
        return None

    if _port_in_use(host, port):
        _warn(f"port {port} already in use — frontend assumed up")
        return None

    log = ROOT / ".iw" / "frontend.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log_fd = log.open("ab")
    env = os.environ.copy()
    # keep the proxy pointed at the backend started by this script
    env.setdefault("VITE_API_TARGET", f"http://{BACKEND_HOST}:{BACKEND_PORT}")
    cmd = [npm, "run", "dev", "--", "--port", str(port), "--host", host, "--strictPort"]
    pid = _popen_detached(cmd, WORKBENCH, log_fd, env)
    log_fd.close()
    return pid


def cmd_start(args) -> int:
    state = _load_state()
    _banner("STARTING SERVICES")

    if args.backend_only and args.frontend_only:
        _die("--backend-only and --frontend-only are mutually exclusive")
    do_b = not args.frontend_only
    do_f = not args.backend_only

    if do_b:
        existing = state.get("backend", {}).get("pid")
        if existing and _is_running(existing):
            _ok(f"backend already running (pid {existing})")
        else:
            pid = _start_backend(args.host, args.backend_port)
            if pid is not None:
                _ok(f"backend starting on http://{args.host}:{args.backend_port} (pid {pid})")
                if _wait_for_port(args.host, args.backend_port, timeout=25):
                    _ok(f"backend ready (port {args.backend_port} listening)")
                else:
                    _warn(f"backend pid {pid} started but port {args.backend_port} not listening yet — see `python iw.py logs backend`")
                state["backend"] = {"pid": pid, "host": args.host, "port": args.backend_port}

    if do_f:
        existing = state.get("frontend", {}).get("pid")
        if existing and _is_running(existing):
            _ok(f"frontend already running (pid {existing})")
        else:
            pid = _start_frontend(args.host, args.frontend_port)
            if pid is not None:
                _ok(f"frontend starting on http://{args.host}:{args.frontend_port} (pid {pid})")
                state["frontend"] = {"pid": pid, "host": args.host, "port": args.frontend_port}
                # vite takes a few seconds to bind; don't hard-fail if slow
                if _wait_for_port(args.host, args.frontend_port, timeout=20):
                    _ok(f"frontend ready (port {args.frontend_port} listening)")

    _save_state(state)
    _print_endpoints(state)
    return 0


# ─── stop / restart ─────────────────────────────────────────────────────────────
def _stop_pid(name: str, pid: int) -> bool:
    """Stop a service. Tries a graceful tree kill, then escalates."""
    if not _is_running(pid):
        _warn(f"{name} pid {pid} not running (stale state)")
        return False
    _kill_tree(pid)
    # give it a moment to die gracefully
    for _ in range(20):
        if not _is_running(pid):
            _ok(f"{name} stopped (pid {pid})")
            return True
        time.sleep(0.15)
    # escalate
    _kill_tree_hard(pid)
    time.sleep(0.5)
    if not _is_running(pid):
        _ok(f"{name} killed (pid {pid})")
        return True
    _warn(f"{name} pid {pid} did not stop")
    return False


def cmd_stop(args) -> int:
    state = _load_state()
    if not state:
        print("nothing to stop (no state).")
        return 0
    _banner("STOPPING SERVICES")
    if args.backend_only and args.frontend_only:
        _die("--backend-only and --frontend-only are mutually exclusive")
    do_b = not args.frontend_only
    do_f = not args.backend_only
    changed = False
    if do_b and "backend" in state:
        if _stop_pid("backend", state["backend"]["pid"]):
            del state["backend"]; changed = True
    if do_f and "frontend" in state:
        if _stop_pid("frontend", state["frontend"]["pid"]):
            del state["frontend"]; changed = True
    if changed or state:
        _save_state(state)
    return 0


def cmd_restart(args) -> int:
    # reuse the port/host the services are on (or defaults if none)
    state = _load_state()
    bh = state.get("backend", {}).get("host", args.host)
    bp = state.get("backend", {}).get("port", args.backend_port)
    fp = state.get("frontend", {}).get("port", args.frontend_port)
    # stop phase
    ns = argparse.Namespace(**{**vars(args), "backend_only": args.backend_only,
                               "frontend_only": args.frontend_only})
    cmd_stop(ns)
    time.sleep(0.5)
    # start phase with inherited port/host
    ns2 = argparse.Namespace(host=bh, backend_port=bp, frontend_port=fp,
                             backend_only=args.backend_only,
                             frontend_only=args.frontend_only)
    return cmd_start(ns2)


# ─── status / logs ──────────────────────────────────────────────────────────────
def _print_endpoints(state: dict) -> None:
    """Print the running endpoints as a clearly-labeled summary at the end of start."""
    lines: list[str] = []
    if state.get("frontend"):
        f = state["frontend"]
        lines.append(f"  FRONTEND (open this in browser) →  http://{f['host']}:{f['port']}")
    if state.get("backend"):
        b = state["backend"]
        lines.append(f"  BACKEND  (API, JSON)          →  http://{b['host']}:{b['port']}/catalog")
    if not lines:
        return
    width = max(len(_strip_ansi(ln)) for ln in lines) + 4
    print()
    print(_c("1;36", "┌─ Investigation Workbench is running " + "─" * max(0, width - 38) + "┐"))
    for ln in lines:
        print(_c("1;36", "│") + ln + " " * max(0, width - len(_strip_ansi(ln)) - 2) + _c("1;36", "│"))
    print(_c("1;36", "└" + "─" * width + "┘"))
    print("  stop with:  python iw.py stop")


def _strip_ansi(s: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;]*m", "", s)


def cmd_status(args) -> int:
    state = _load_state()
    print("Investigation Workbench — service status")
    print(f"  platform:  {'Windows' if IS_WINDOWS else 'macOS/Linux'}  "
          f"(venv bin: {_VENV_BIN})")
    print(f"  state file: {STATE.relative_to(ROOT) if STATE.exists() else STATE}")
    found_any = False
    for name in ("backend", "frontend"):
        rec = state.get(name)
        if not rec:
            print(f"  {name:9s} not recorded")
            continue
        alive = _is_running(rec["pid"])
        port = _port_in_use(rec.get("host", "127.0.0.1"), rec.get("port", 0))
        mark = _c("1;32", "●") if (alive and port) else _c("1;31", "○")
        plain = _strip_ansi(mark)
        print(f"  {mark} {name:9s} pid {rec['pid']:>7}  "
              f"{rec.get('host','127.0.0.1')}:{rec.get('port')}  "
              f"{'alive' if alive else 'dead'} / port {'open' if port else 'closed'}")
        found_any = True
    if not found_any:
        print("  no services recorded. run `python iw.py start`.")
    return 0


def _tail_file(path: pathlib.Path, n: int) -> None:
    """Print the last n lines of a file, dependency-free (no `tail` on Windows)."""
    try:
        with path.open("rb") as f:
            data = f.read()
    except OSError as exc:
        print(f"  (could not read {path.name}: {exc})")
        return
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    for ln in lines[-n:]:
        print(ln)


def _follow_files(paths: list[pathlib.Path], n: int) -> None:
    """Follow multiple log files for appended lines (Ctrl+C to exit). Pure Python —
    replaces `tail -F`, which doesn't exist on Windows."""
    sizes: dict[pathlib.Path, int] = {}
    # print an initial head so the user sees context
    for p in paths:
        _tail_file(p, n)
        try:
            sizes[p] = p.stat().st_size
        except OSError:
            sizes[p] = 0
    print(_c("1;36", "--- following (Ctrl+C to exit) ---"))
    try:
        while True:
            changed = False
            for p in paths:
                try:
                    new_size = p.stat().st_size
                except OSError:
                    continue
                if new_size < sizes[p]:
                    # file was truncated/rotated — reset
                    sizes[p] = new_size
                    continue
                if new_size > sizes[p]:
                    with p.open("rb") as f:
                        f.seek(sizes[p])
                        chunk = f.read(new_size - sizes[p])
                    sizes[p] = new_size
                    sys.stdout.write(chunk.decode("utf-8", errors="replace"))
                    sys.stdout.flush()
                    changed = True
            if not changed:
                time.sleep(0.5)
    except KeyboardInterrupt:
        print()


def cmd_logs(args) -> int:
    which = args.which or "both"
    files: list[pathlib.Path] = []
    if which in ("backend", "both"):
        files.append(ROOT / ".iw" / "backend.log")
    if which in ("frontend", "both"):
        files.append(ROOT / ".iw" / "frontend.log")
    missing = [p.name for p in files if not p.exists()]
    if missing:
        _warn("no log yet for: " + ", ".join(missing) + " (service may not have started)")
        files = [p for p in files if p.exists()]
    if not files:
        _die("no logs found. start a service first: `python iw.py start`")
    if args.follow:
        names = ", ".join(p.name for p in files)
        _banner(f"tailing ({names}) — Ctrl+C to exit")
        _follow_files(files, args.lines)
        return 0
    # one-shot dump
    for p in files:
        print(f"\n=== {p.name}: {p.relative_to(ROOT)} ===", flush=True)
        _tail_file(p, args.lines)
    return 0


# ─── cli ────────────────────────────────────────────────────────────────────────
def _add_selection(p: argparse.ArgumentParser) -> None:
    p.add_argument("--backend-only", action="store_true", help="only the backend")
    p.add_argument("--frontend-only", action="store_true", help="only the frontend")


def _add_net(p: argparse.ArgumentParser, default_host: str = BACKEND_HOST) -> None:
    p.add_argument("--host", default=default_host, help=f"bind host (default: {default_host})")
    p.add_argument("--backend-port", type=int, default=BACKEND_PORT, help=f"default {BACKEND_PORT}")
    p.add_argument("--frontend-port", type=int, default=FRONTEND_PORT, help=f"default {FRONTEND_PORT}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="iw.py", description="Investigation Workbench controller.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="one-time install of backend + frontend deps")
    sp.add_argument("--force", action="store_true", help="recreate venv / reinstall")
    _add_selection(sp)
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("start", help="start services")
    _add_selection(sp)
    _add_net(sp)
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("stop", help="stop services")
    _add_selection(sp)
    sp.set_defaults(func=cmd_stop)

    sp = sub.add_parser("restart", help="stop then start")
    _add_selection(sp)
    _add_net(sp)
    sp.set_defaults(func=cmd_restart)

    sp = sub.add_parser("status", help="show running services and ports")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("logs", help="show logs (tail). Use -f to follow.")
    sp.add_argument("which", nargs="?", choices=("backend", "frontend", "both"), default="both")
    sp.add_argument("-f", "--follow", action="store_true", help="follow log output")
    sp.add_argument("-n", "--lines", type=int, default=40, help="last N lines (default 40)")
    sp.set_defaults(func=cmd_logs)

    return p


def main() -> int:
    if not ENGINE.exists() or not WORKBENCH.exists():
        _die("run this from the repo root (engine/ and workbench/ must be here)")
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
