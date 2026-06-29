"""Browser-backed capabilities — Playwright drives a real Chrome against UI-only tools (ServiceNow,
Datadog, Splunk, an internal portal, …). The capability registry is a JSON FILE (the same shape as the
demo's `capabilities.json`): each entry maps engine intents -> a real tool URL. The operator keeps all
their tools in that file; each opens its own tab; the human logs in; and the agent then reads those
live pages as the intents they back. This swaps ONLY how a capability fetches data — the engine, the
run loop, and the audit trail are unchanged.

Four pieces:
  * BrowserManager — one persistent Chrome context, one TAB per capability (logins persist, several
    tools stay open at once). System Chrome channel + an anti-automation flag get real results from
    bot-hostile sites even headless; falls back to bundled Chromium when Chrome/persistent context is
    unavailable (e.g. CI).
  * Cap / CapabilityStore — the registry, persisted to a JSON file (file-as-DB) so registrations + the
    logged-in/ready flag survive restarts; the office flow is "list all your tool URLs in the file".
  * HybridAdapter — a CapabilityAdapter that, for an intent backed by a registered+ready capability,
    reads the live tab (waiting a bounded time for the human to finish logging in), else returns demo data.

Playwright's sync API is single-threaded, but FastAPI serves each request on its own worker thread, so
ALL Playwright work funnels through one dedicated thread (a 1-worker executor). Registry reads use a
snapshot (`list(...)`) so a concurrent register/remove can't break an in-flight resolve.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

from engine.domain import ProviderKind

# a page is a bot-check / login wall if its (short) text matches these — surfaced so the run degrades
# gracefully and the human can solve it (we never auto-solve a CAPTCHA) then re-read.
_WALL = re.compile(r"unusual traffic|not a robot|detected unusual|verify (you'?re|it'?s) you|"
                   r"sign in to continue|please log ?in|enable cookies", re.I)
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_NOTE = ("The capability layer as a flat list (file-as-DB). Each entry maps the playbook's INTENTS to a "
         "real tool URL the agent opens via the browser. effect = read-only | write. Edit the urls to "
         "your real tools, or register them from the console.")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "cap"


class BrowserManager:
    """One shared Chrome. `open(key, url)` opens a tab the human logs into; `read(key)` returns that
    tab's visible text. Tabs are keyed by capability so several tools stay open + logged-in at once."""

    def __init__(self, headed: bool = True, profile_dir: Optional[str] = None,
                 channel: str = "chrome") -> None:
        self._headed = headed
        self._profile = profile_dir
        self._channel = channel
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pw")  # owns Playwright
        self._pw = None
        self._browser = None      # set only on the non-persistent fallback path
        self._ctx = None
        self._pages: dict[str, object] = {}
        self.mode: Optional[str] = None   # which launch strategy succeeded (diagnostics)

    # ── Playwright lifecycle (all of this runs ON the single pw thread) ───
    def _ensure_ctx(self):
        if self._ctx is not None:
            return self._ctx
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        common = {"headless": not self._headed, "user_agent": _UA, "locale": "en-US",
                  "args": ["--disable-blink-features=AutomationControlled"]}
        if self._profile:   # persistent context: login/cookies survive across runs
            for name, kw in (("chrome-persistent", dict(common, channel=self._channel)),
                             ("chromium-persistent", dict(common))):
                try:
                    self._ctx = self._pw.chromium.launch_persistent_context(self._profile, **kw)
                    self.mode = name
                    return self._ctx
                except Exception:
                    continue
        try:    # last resort: a plain (non-persistent) browser — works anywhere Playwright is installed
            self._browser = self._pw.chromium.launch(headless=not self._headed, channel=self._channel)
            self.mode = "chrome-ephemeral"
        except Exception:
            self._browser = self._pw.chromium.launch(headless=not self._headed)
            self.mode = "chromium-ephemeral"
        self._ctx = self._browser.new_context(user_agent=_UA, locale="en-US")
        return self._ctx

    def _page_for(self, key: str, url: Optional[str] = None, navigate: bool = False):
        ctx = self._ensure_ctx()
        page = self._pages.get(key)
        if page is None:
            page = ctx.new_page()
            self._pages[key] = page
            if url:
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
        elif navigate and url and url != page.url:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
        return page

    # ── public API (each submits a job to the single pw thread) ──────────
    def open(self, key: str, url: str) -> dict:
        def job():
            p = self._page_for(key, url)
            return {"opened": url, "title": p.title(), "mode": self.mode}
        return self._exec.submit(job).result(timeout=90)

    def read(self, key: str, url: Optional[str] = None) -> dict:
        def job():
            p = self._page_for(key, url, navigate=True)
            p.wait_for_timeout(500)
            text = (p.inner_text("body") or "").strip()
            return {"source": "browser", "key": key, "url": p.url, "title": p.title(),
                    "page_text": text[:4000], "wall": bool(_WALL.search(text[:600])),
                    "evidence": [p.url]}
        return self._exec.submit(job).result(timeout=90)

    def is_open(self, key: str) -> bool:
        return key in self._pages

    def close(self, key: str) -> None:
        def job():
            p = self._pages.pop(key, None)
            if p is not None:
                try:
                    p.close()
                except Exception:
                    pass
        self._exec.submit(job).result(timeout=30)


@dataclass
class Cap:
    """One registered browser capability — JUST a url + description (+ which intents it backs). Login is
    on-demand: the agent opens the url and, if a login/auth wall shows, waits for the human to log in in
    the browser window before reading. Config fields persist to the file; the rest is runtime status."""
    key: str
    name: str
    url: str
    description: str = ""
    intents: list[str] = field(default_factory=list)   # engine intents this one tool backs
    effect: str = "read-only"                           # read-only | write
    # ── runtime (NOT persisted to the registry file) ──
    opened: bool = False        # a browser tab has been launched for it
    ready: bool = False         # the human logged in / marked it ready to read live
    reads: int = 0              # how many times the agent has read it live
    last_excerpt: str = ""      # snippet of the last read (for the console)
    wall: bool = False          # a login/bot wall was seen on the last read (human must log in)

    def wire(self) -> dict:
        return asdict(self)

    def config(self) -> dict:   # the persisted registry-file shape (url + description, file-as-DB)
        return {"id": self.key, "label": self.name, "intents": self.intents, "url": self.url,
                "effect": self.effect, "description": self.description}


class CapabilityStore:
    """The capability registry, optionally backed by a JSON file (file-as-DB). With a path it loads on
    init and saves on every mutation; without one it is purely in-memory (tests). Single-user demo —
    mutations are tiny dict ops; resolves snapshot the dict so a concurrent write can't break them."""

    def __init__(self, path: Optional[str] = None) -> None:
        self._path = Path(path) if path else None
        self._note = _NOTE
        self._caps: dict[str, Cap] = {}
        if self._path and self._path.exists():
            self._load()

    def _load(self) -> None:
        data = json.loads(self._path.read_text())          # type: ignore[union-attr]
        self._note = data.get("note") or _NOTE
        self._caps.clear()
        for e in data.get("capabilities", []):
            key = e.get("id") or slugify(e.get("label", ""))
            self._caps[key] = Cap(
                key=key, name=e.get("label", key), url=e.get("url", ""),
                description=e.get("description", e.get("what", "")),   # accept either key
                intents=list(e.get("intents", [])), effect=e.get("effect", "read-only"))

    def _save(self) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(
            {"note": self._note, "capabilities": [c.config() for c in self._caps.values()]}, indent=2))

    def reload(self) -> None:
        """Re-read the file — the operator may have edited it directly (added real tool URLs)."""
        if self._path and self._path.exists():
            self._load()

    def register(self, name: str, url: str, description: str = "", intents: Optional[list[str]] = None,
                 effect: str = "read-only") -> Cap:
        key = slugify(name)
        cap = Cap(key=key, name=name or key, url=url, description=description,
                  intents=list(intents or []), effect=effect)
        self._caps[key] = cap
        self._save()
        return cap

    def list(self) -> list[Cap]:
        return list(self._caps.values())

    def get(self, key: str) -> Optional[Cap]:
        return self._caps.get(key)

    def mark_ready(self, key: str, ready: bool = True) -> Optional[Cap]:
        cap = self._caps.get(key)
        if cap is not None:
            cap.ready = ready
        return cap

    def mark_opened(self, key: str, opened: bool = True) -> Optional[Cap]:
        cap = self._caps.get(key)
        if cap is not None:
            cap.opened = opened
        return cap

    def remove(self, key: str) -> Optional[Cap]:
        cap = self._caps.pop(key, None)
        if cap is not None:
            self._save()
        return cap

    def for_intent(self, intent: str) -> Optional[Cap]:
        """The (read) capability backing this intent — first one with a URL."""
        for c in list(self._caps.values()):
            if intent in c.intents and c.url and c.effect != "write":
                return c
        return None

    def live_intents(self) -> set[str]:
        """Every read intent backed by a configured tool URL — the planner exercises these live."""
        out: set[str] = set()
        for c in list(self._caps.values()):
            if c.url and c.effect != "write":
                out.update(c.intents)
        return out


class HybridAdapter:
    """A CapabilityAdapter that reads the live tool tab for an intent backed by a registered capability,
    and falls back to demo data otherwise. Login is ON-DEMAND: if the first read shows a login/auth
    wall, it waits (re-reading) for the human to log in in the browser window, then reads the real page.
    Same `invoke(capability_id, input) -> dict` interface as DemoAdapter."""

    # intents whose RESULT seeds the incident graph (structured nodes/edges via TopologyFold). A live
    # page read returns text, not nodes, so these always use demo data — otherwise the graph would be
    # empty. The tool is still read live via its other (text) intents.
    STRUCTURAL_INTENTS = frozenset({"topology"})

    def __init__(self, kind: ProviderKind, manager: BrowserManager, demo, store: CapabilityStore,
                 *, wait_timeout: float = 20.0, poll: float = 0.5,
                 structural_intents=STRUCTURAL_INTENTS,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.kind = kind
        self._mgr = manager
        self._demo = demo
        self._store = store
        self._wait_timeout = wait_timeout
        self._poll = poll
        self._structural = set(structural_intents)
        self._sleep = sleep

    def invoke(self, capability_id: str, input: dict) -> dict:
        intent = (input or {}).get("intent")
        if intent in self._structural:                       # keep the graph seeding from demo topology
            return self._demo.invoke(capability_id, input)
        cap = self._store.for_intent(intent) if intent else None
        if not (cap and cap.url):                            # nothing configured for this intent
            return self._demo.invoke(capability_id, input)
        if not getattr(cap, "ready", False):                 # not marked ready -> demo data (the office
            return self._demo.invoke(capability_id, input)   # flow: open + log in + mark Ready first)
        try:
            r = self._mgr.read(cap.key, cap.url)             # open + read the live tab
            # on-demand login: if a login/auth wall shows, wait for the human to log in (re-read until
            # the wall clears), then take the real page. wait_timeout=0 (headless / no human) skips it.
            waited = 0.0
            while r.get("wall") and waited < self._wait_timeout:
                self._sleep(self._poll)
                waited += self._poll
                r = self._mgr.read(cap.key)
            if r.get("wall"):        # still not logged in -> demo data is cleaner than a login page
                cap.wall = True
                fb = self._demo.invoke(capability_id, input)
                fb["browser_wall"] = f"{cap.name}: login required — used demo data"
                return fb
            cap.reads += 1
            cap.last_excerpt = (r.get("page_text") or "")[:200]
            cap.wall = False
            r["capability_name"] = cap.name
            r["intent"] = intent
            return r
        except Exception as exc:   # never break the run on a browser hiccup — fall back to demo data
            fb = self._demo.invoke(capability_id, input)
            fb["browser_error"] = str(exc)[:160]
            return fb
