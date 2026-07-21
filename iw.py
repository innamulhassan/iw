#!/usr/bin/env python3
"""iw — Investigation Workbench controller.

One script to bring the Investigation Workbench up and down. Backend (FastAPI/uvicorn)
and frontend (Vite) are each a detached child, tracked in a tiny JSON pidfile in this
repo so start/stop/status/logs survive the shell that launched them.

    ./iw.py init      install backend + frontend deps (intelligent; skips what's already done)
    ./iw.py start     bring up both services (or one with --backend / --frontend)
    ./iw.py stop      stop both (or one)
    ./iw.py restart   stop then start
    ./iw.py status    show running services and listening ports
    ./iw.py logs      tail logs for both (or one); -f to follow

Run `./iw.py <command> --help` for command-specific flags. Defaults:

    backend   uvicorn on 127.0.0.1:8099
    frontend  vite   on 127.0.0.1:5173
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import time
from typing import Optional

ROOT = pathlib.Path(__file__).resolve().parent
ENGINE = ROOT / "engine"
WORKBENCH = ROOT / "workbench"
STATE = ROOT / ".iw" / "state.json"

BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8099
FRONTEND_PORT = 5173

# ─── helpers ────────────────────────────────────────────────────────────────────
def _banner(msg: str) -> None:
    print(f"\n\033[1;36m▶ {msg}\033[0m")


def _ok(msg: str) -> None:
    print(f"  \033[1;32m✓\033[0m {msg}")


def _warn(msg: str) -> None:
    print(f"  \033[1;33m!\033[0m {msg}")


def _die(msg: str, code: int = 1) -> "NoReturn":  # noqa: F821
    print(f"\033[1;31m✗ {msg}\033[0m", file=sys.stderr)
    sys.exit(code)


def _die_if_missing(dep: str, how: str) -> None:
    if shutil.which(dep) is None:
        _die(f"{dep!r} not found on PATH. Install: {how}")


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


def _read_pid(pid: int) -> str:
    """Return start-time if the pid is alive, else empty string."""
    try:
        with open(f"/proc/{pid}/stat") as f:  # Linux: cheap, reliable
            return f.read().split()[21]
    except OSError:
        pass
    # macOS / fallback: kill -0 twice still isn't re-exec safe, but combined with a
    # port check it's good enough for a local dev controller.
    try:
        os.kill(pid, 0)
    except OSError:
        return ""
    return "alive"


def _is_running(pid: int) -> bool:
    return bool(_read_pid(pid))


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
def install_backend(force: bool = False) -> bool:
    """Create engine/.venv and install the editable package + [server,dev] extras.

    Returns True if (re)installed. Idempotent: a valid venv with the package importable
    is left alone unless `force`."""
    venv = ENGINE / ".venv"
    py = venv / "bin" / "python"
    _die_if_missing("python3", "use a python3.11+ (e.g. pyenv or brew install python)")

    if not force and venv.exists() and py.exists():
        # probe: can we import the server? if so, the install is good.
        r = subprocess.run([str(py), "-c", "import iw_engine.api.server"],
                           cwd=ENGINE, capture_output=True)
        if r.returncode == 0:
            _ok(f"backend deps already installed ({venv.relative_to(ROOT)})")
            return False

    if venv.exists() and (force or not py.exists()):
        shutil.rmtree(venv)

    _banner(f"creating python venv at {venv.relative_to(ROOT)}")
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    _ok("venv created")

    _banner("installing backend deps (pyproject.toml with [server,dev] extras)")
    cmd = [str(py), "-m", "pip", "install", "--upgrade", "pip"]
    subprocess.run(cmd, cwd=ENGINE, check=True)
    cmd = [str(py), "-m", "pip", "install", "-e", ".[server,dev]"]
    subprocess.run(cmd, cwd=ENGINE, check=True)
    _ok("backend deps installed")
    return True


def install_frontend(force: bool = False) -> bool:
    """Run npm install in workbench/. Idempotent: leaves a populated node_modules alone."""
    _die_if_missing("npm", "https://nodejs.org/en/download/")

    nm = WORKBENCH / "node_modules"
    pkg = WORKBENCH / "package.json"
    if not pkg.exists():
        _die(f"no package.json at {pkg}")
    if not force and nm.exists() and any(nm.iterdir()):
        _ok(f"frontend deps already installed ({nm.relative_to(ROOT)})")
        return False

    if force and nm.exists():
        shutil.rmtree(nm)

    _banner("installing frontend deps (npm install)")
    subprocess.run(["npm", "install"], cwd=WORKBENCH, check=True)
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
    print("    next: ./iw.py start")
    return 0


# ─── start ──────────────────────────────────────────────────────────────────────
def _start_backend(host: str, port: int) -> Optional[int]:
    py = ENGINE / ".venv" / "bin" / "python"
    uv = ENGINE / ".venv" / "bin" / "uvicorn"
    if not py.exists():
        _warn("backend venv missing — run `./iw.py init` first; skipping backend")
        return None

    if _port_in_use(host, port):
        _warn(f"port {port} already in use — backend assumed up")
        return None

    log = ROOT / ".iw" / "backend.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log_fd = log.open("ab")  # append so restarts keep a history
    env = os.environ.copy()
    cmd = [str(uv), "iw_engine.api.server:create_server", "--factory",
           "--host", host, "--port", str(port)]
    # detach from our terminal so it survives this script exiting
    proc = subprocess.Popen(cmd, cwd=ENGINE, stdout=log_fd, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env, start_new_session=True)
    log_fd.close()
    return proc.pid


def _start_frontend(host: str, port: int) -> Optional[int]:
    if shutil.which("npm") is None:
        _warn("npm missing — run `./iw.py init`; skipping frontend")
        return None
    if not (WORKBENCH / "node_modules").exists():
        _warn("frontend node_modules missing — run `./iw.py init`; skipping frontend")
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
    # vite reads PORT only if the user sets it; pass via the CLI instead
    cmd = ["npm", "run", "dev", "--", "--port", str(port), "--host", host]
    proc = subprocess.Popen(cmd, cwd=WORKBENCH, stdout=log_fd, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, env=env, start_new_session=True)
    log_fd.close()
    return proc.pid


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
                    _warn(f"backend pid {pid} started but port {args.backend_port} not listening yet — see ./iw.py logs backend")
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
    """Stop a service started with start_new_session=True. The pid is its process-group
    id, so killpg takes down the launcher AND its children (e.g. npm → vite) together —
    SIGTERM-ing only the wrapper pid would orphan the vite child."""
    if not _is_running(pid):
        _warn(f"{name} pid {pid} not running (stale state)")
        return False
    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError as exc:
        _warn(f"could not signal {name} pgid {pid}: {exc}")
        return False
    # give it a moment to die gracefully
    for _ in range(20):
        if not _is_running(pid):
            _ok(f"{name} stopped (pid {pid})")
            return True
        time.sleep(0.15)
    # escalate
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass
    time.sleep(0.3)
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
    if state.get("backend"):
        b = state["backend"]
        print(f"    backend  → http://{b['host']}:{b['port']}/catalog")
    if state.get("frontend"):
        f = state["frontend"]
        print(f"    frontend → http://{f['host']}:{f['port']}")


def cmd_status(args) -> int:
    state = _load_state()
    print("Investigation Workbench — service status")
    print(f"  state file: {STATE.relative_to(ROOT) if STATE.exists() else STATE}")
    found_any = False
    for name in ("backend", "frontend"):
        rec = state.get(name)
        if not rec:
            print(f"  {name:9s} not recorded")
            continue
        alive = _is_running(rec["pid"])
        port = _port_in_use(rec.get("host", "127.0.0.1"), rec.get("port", 0))
        mark = "\033[1;32m●\033[0m" if (alive and port) else "\033[1;31m○\033[0m"
        print(f"  {mark} {name:9s} pid {rec['pid']:>7}  "
              f"{rec.get('host','127.0.0.1')}:{rec.get('port')}  "
              f"{'alive' if alive else 'dead'} / port {'open' if port else 'closed'}")
        found_any = True
    if not found_any:
        print("  no services recorded. run `./iw.py start`.")
    return 0


def cmd_logs(args) -> int:
    which = args.which or "both"
    files: list[tuple[str, pathlib.Path]] = []
    if which in ("backend", "both"):
        files.append(("backend", ROOT / ".iw" / "backend.log"))
    if which in ("frontend", "both"):
        files.append(("frontend", ROOT / ".iw" / "frontend.log"))
    missing = [name for name, p in files if not p.exists()]
    if missing:
        _warn("no log yet for: " + ", ".join(missing) + " (service may not have started)")
        files = [(n, p) for n, p in files if p.exists()]
    if not files:
        _die("no logs found. start a service first: ./iw.py start")
    if args.follow:
        _banner(f"tailing ({' '.join(n for n,_ in files)}) — Ctrl+C to exit")
        # tail -f multiple files with headers
        cmd = ["tail", "-n", str(args.lines), "-F", *(str(p) for _, p in files)]
        try:
            subprocess.run(cmd, check=False)
        except KeyboardInterrupt:
            pass
        return 0
    # one-shot dump. Flush before each tail so the header lands above the content —
    # tail writes straight to the fd and would otherwise outrun Python's stdout buffer.
    for name, p in files:
        print(f"\n=== {name}: {p.relative_to(ROOT)} ===", flush=True)
        subprocess.run(["tail", "-n", str(args.lines), str(p)], check=False)
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
