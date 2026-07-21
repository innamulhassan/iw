# Troubleshooting Guide

Common fixes when `iw.py init` / `start` / `stop` fails — especially on **Windows**
and behind a **company proxy / JFrog Artifactory**.

Open this file on the machine where things are failing and follow the steps.
Everything here is meant to be run in **cmd** or **PowerShell** on Windows.

---

## 1. Fix the Artifactory / registry issue (company network)

If `python iw.py init` fails while pip or npm is trying to download packages —
e.g. timeouts, `Could not fetch URL`, `403`, `SSL: CERTIFICATE_FAILED`, or it hangs —
your company blocks the public PyPI/npm registries and forces traffic through a
**JFrog Artifactory mirror**. `iw.py` needs to be pointed at that mirror.

### Step 1 — Steal the working config from your OTHER project

You said another project works fine on the same machine. Its pip/npm config already
points at JFrog — copy that config into `iw`. Run these **inside that working project's
folder** to find the settings:

**For pip (Python):**
```cmd
:: global pip config (most common place)
type "%APPDATA%\pip\pip.ini"

:: project-local pip config (if any)
type pip.ini
type pip.conf

:: search project files for registry/index settings
findstr /S /I "index-url artifactory jfrog registry" *.toml *.cfg *.ini

:: environment variables
echo %PIP_INDEX_URL%
echo %PIP_EXTRA_INDEX_URL%
```

**For npm:**
```cmd
:: global npm config (MOST COMMON — this is usually where it lives)
type "%USERPROFILE%\.npmrc"

:: project-local npm config
type .npmrc

:: show the registry npm actually resolves to right now
npm config get registry

:: environment variables
echo %npm_config_registry%
```

### Step 2 — Recognize the JFrog URLs

You're hunting for lines that look like this (your company name + repo names differ):

```
# pip — a PyPI mirror inside JFrog
index-url = https://YOURCOMPANY.jfrog.io/artifactory/api/pypi/pypi-virtual/simple

# npm — an npm mirror inside JFrog
registry=https://YOURCOMPANY.jfrog.io/artifactory/api/npm/npm-virtual/
```

If there's an auth/credentials line next to them, copy that too — it looks like one of:
- `_auth = <long base64 string>` (npm)
- `always-auth = true` (npm)
- `username = ...` / `password = ...` (pip)
- The URL itself contains a token: `https://<user>:<token>@yourcompany.jfrog.io/...`

### Step 3 — Apply the same config to the `iw` project

Create two small config files **inside the `iw` repo** so pip/npm use JFrog automatically:

**`engine\pip.ini`:**
```ini
[install]
index-url = PASTE_YOUR_PIP_INDEX_URL_HERE
trusted-host = YOURCOMPANY.jfrog.io
```

**`workbench\.npmrc`:**
```
registry=PASTE_YOUR_NPM_REGISTRY_URL_HERE
# plus the auth line you found, e.g.:
# _auth=PASTE_TOKEN_HERE
# always-auth=true
```

Then re-run:
```cmd
python iw.py init
```

pip and npm will now pull from JFrog, not the public registries. No code changes
needed — `iw.py` inherits these config files automatically.

### Step 4 (optional) — or use environment variables instead

If you'd rather not create config files, set these in a terminal, then run `init`
in the SAME terminal:

```cmd
set PIP_INDEX_URL=https://YOURCOMPANY.jfrog.io/artifactory/api/pypi/pypi-virtual/simple
set npm_config_registry=https://YOURCOMPANY.jfrog.io/artifactory/api/npm/npm-virtual/
python iw.py init
```

(PowerShell uses `$env:PIP_INDEX_URL = "..."` instead of `set`.)

---

## 2. `uv not found` / install uv

The backend is now driven by **`uv`** (it manages `engine/.venv` itself and installs from
`uv.lock`). This is a deliberate change: uv never reads global `pip.ini`, so the Windows
**"cannot perform a --user install" error is gone for good** — that pip trap can't happen
anymore. If you still see that old pip error, you're on a pre-uv version:
```cmd
git pull
python iw.py init
```

uv is a hard requirement now. Install it once (no admin needed):
```cmd
:: Windows (PowerShell)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

:: macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```
Then **open a new terminal** (so `uv` is on PATH) and re-run `python iw.py init`. uv can
also fetch its own Python 3.11+ — you don't need a system Python for the backend.

To pin/upgrade uv itself later: `uv self update`.

---

## 3. `init` fails during backend install (uv sync)

Two usual causes:
1. **Artifactory not configured** — see Section 1 above. uv respects `UV_INDEX_URL` /
   `UV_DEFAULT_INDEX` env vars and a `[tool.uv]` section in pyproject.toml; the same
   JFrog index you'd give pip applies. For a one-off:
   ```cmd
   set UV_DEFAULT_INDEX=https://YOURCOMPANY.jfrog.io/artifactory/api/pypi/pypi-virtual/simple
   python iw.py init
   ```
2. **Half-installed .venv from a failed run** — start clean:
   ```cmd
   python iw.py init --force
   ```
   `--force` wipes `engine\.venv` and `workbench\node_modules` and rebuilds them.

---

## 4. `start` fails or "port already in use"

Something else is using port 8099 or 5173. Find and free it:
```cmd
:: what's using port 8099?
netstat -ano | findstr :8099
:: (last column is the PID; kill it)
taskkill /PID <that-pid> /F

:: same for 5173
netstat -ano | findstr :5173
taskkill /PID <that-pid> /F
```

Or start `iw` on different ports:
```cmd
python iw.py start --backend-port 8100 --frontend-port 5174
```

---

## 5. `stop` doesn't kill the service (Windows)

Windows process trees can be stubborn. If `python iw.py stop` reports success but the
port is still occupied, force-kill manually:
```cmd
:: find the process holding the port
netstat -ano | findstr :8099
:: kill it and its children
taskkill /PID <pid> /T /F
```

Then clean the state file so `iw.py status` isn't confused:
```cmd
del .iw\state.json
```

---

## 6. Check what `iw.py` thinks is going on

Always useful:
```cmd
python iw.py status     :: what services are recorded, alive?
python iw.py logs -n 50 :: last 50 lines of both logs
python iw.py logs -f    :: follow logs live (Ctrl+C to exit)
```

The logs live in `.iw\backend.log` and `.iw\frontend.log`. Open them directly if
`iw.py logs` isn't showing what you need.

---

## 7. I need help — what to paste back

When reporting an issue, include:
1. The **exact command** you ran.
2. The **full error output** (copy-paste, don't paraphrase).
3. Output of `python iw.py status`.
4. Output of `python iw.py logs -n 30` for whichever service failed.

---

## Quick reference — the working setup on Windows

```cmd
:: 1. install uv once (no admin)
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
:: → open a NEW terminal so uv is on PATH

:: 2. clone + run
git clone https://github.com/innamulhassan/iw.git
cd iw
python iw.py init     :: uv sync (backend) + npm install (frontend)
python iw.py start
:: → open http://127.0.0.1:5173 in your browser
python iw.py stop
```

If `init` fails on downloads, it's the Artifactory config — Section 1.
