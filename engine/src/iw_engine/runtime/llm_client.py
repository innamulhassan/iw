"""llm_client.py — the LLM client seam (principle 11: judgment is swappable).

The `LivePlanner` does not care WHICH model answers — it needs ONE method,
`complete_json(system, user) -> dict`, returning the JSON plan documented in
`live_planner._SYSTEM`. Any class with that shape satisfies `LLMClient` and can
drive a live investigation. Two concrete clients ship here (`XaiClient`,
`GeminiClient`); plugging in Anthropic / OpenAI / a local model means writing a
~30-line class with `complete_json` + a `.name`, then either passing it to
`LivePlanner(client=...)` / `live_build_manager(client=...)` directly, or
returning it from a factory registered here.

Selection (`make_llm_client`) is xAI-first by design — set XAI_API_KEY and grok
is used — then Gemini, then None (the caller falls back to the scripted mock).
`IW_LIVE_PROVIDER` overrides the cascade; `IW_LIVE_MODEL` pins the model string.

All HTTP is stdlib `urllib` only — no provider SDK dependency.
"""
from __future__ import annotations

import json
import os
import pathlib
import time
import urllib.error
import urllib.request
from typing import Protocol, runtime_checkable


# ── the Protocol any provider implements ───────────────────────────────────────
@runtime_checkable
class LLMClient(Protocol):
    """The complete surface `LivePlanner` relies on. `name` is for logging/summary
    (read by the run_live script); `complete_json` is the one call in `LivePlanner.plan`.
    A provider maps `(system, user)` into its own wire format internally — that is the
    client's concern, not the planner's."""
    name: str

    def complete_json(self, system: str, user: str) -> dict: ...


# ── shared parse/backoff helpers (reusable by any provider client) ─────────────
def loads_salvage(text: str) -> dict:
    """Parse the model's JSON; if it is fenced or truncated, salvage the outermost object.
    Never re-calls the API (daily quota is precious). Exposed so a provider that cannot
    force JSON mode can reuse the same salvage logic."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text[:4].lower() == "json":
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start >= 0:
        # try progressively shorter suffixes down to the last balanced close brace
        for end in range(len(text), start, -1):
            if text[end - 1] != "}":
                continue
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                continue
    raise RuntimeError(f"unparseable LLM response (len={len(text)}): {text[:160]!r}")


def retry_delay(err: urllib.error.HTTPError, *, fallback: float, cap: float = 90.0) -> float:
    """Honor the server's back-off hint on a 429/503: the `Retry-After` header (seconds) or the
    Google RetryInfo `retryDelay` (e.g. "42s") in the error body — else the caller's exponential
    fallback. Capped so a huge daily-quota hint can't wedge the run for minutes."""
    ra = err.headers.get("Retry-After") if err.headers else None
    if ra:
        try:
            return min(cap, float(ra))
        except ValueError:
            pass
    try:
        body = json.loads(err.read().decode())
        for det in body.get("error", {}).get("details", []):
            delay = det.get("retryDelay")
            if isinstance(delay, str) and delay.endswith("s"):
                return min(cap, float(delay[:-1]))
    except Exception:
        pass
    return min(cap, fallback)


# ── concrete clients (stdlib urllib only) ──────────────────────────────────────
class GeminiClient:
    """Google Gemini generateContent JSON client."""

    def __init__(self, api_key: str, model: str = "gemini-2.5-flash", *, temperature: float = 0.0,
                 min_interval: float = 4.5):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.name = f"gemini/{model}"
        self.min_interval = min_interval   # RPM throttle (free tier ~15 rpm)
        self._last = 0.0

    def complete_json(self, system: str, user: str) -> dict:
        gap = self.min_interval - (time.monotonic() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()
        # the key travels in the x-goog-api-key header (Gemini's documented header auth),
        # NEVER the URL query string — a URL-embedded key leaks into proxy/access logs and
        # into HTTPError.url on any failure (2026-07-22 review, finding 6)
        url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
               f"{self.model}:generateContent")
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": self.temperature,
                                 "responseMimeType": "application/json",
                                 "maxOutputTokens": 8192},
        }
        data = json.dumps(body).encode()
        for attempt in range(6):
            req = urllib.request.Request(url, data=data, headers={
                "Content-Type": "application/json", "x-goog-api-key": self.api_key})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    d = json.load(r)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 503) and attempt < 5:
                    time.sleep(retry_delay(e, fallback=4 * (2 ** attempt)))
                    self._last = time.monotonic()
                    continue
                raise
            parts = d.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
            return loads_salvage(text)   # do NOT re-call on parse error — conserve quota
        raise RuntimeError("LLM call exhausted retries")


class XaiClient:
    """OpenAI-compatible client for xAI grok (chat/completions, JSON response)."""

    def __init__(self, api_key: str, model: str = "grok-4.5", *, temperature: float = 0.0):
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.name = f"xai/{model}"

    def complete_json(self, system: str, user: str) -> dict:
        url = "https://api.x.ai/v1/chat/completions"
        body = {
            "model": self.model,
            "temperature": self.temperature,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
        }
        data = json.dumps(body).encode()
        for attempt in range(6):
            req = urllib.request.Request(url, data=data, headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"})
            try:
                with urllib.request.urlopen(req, timeout=120) as r:
                    d = json.load(r)
                return json.loads(d["choices"][0]["message"]["content"])
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 503) and attempt < 5:
                    time.sleep(4 * (2 ** attempt))
                    continue
                raise
        raise RuntimeError("LLM call exhausted retries")


# ── the provider registry + factory ────────────────────────────────────────────
# Each provider: (env-var-name, client-class, default-model). Order = precedence.
# To plug in a new LLM: add a class above (with `complete_json` + `.name`) and an
# entry here. The cascade tries each in order; first present key wins.
_PROVIDERS: list[tuple[str, type, str]] = [
    ("XAI_API_KEY", XaiClient, "grok-4.5"),
    # gemini-2.5-flash-lite was RETIRED by Google for new users (404 "no longer available",
    # observed 2026-07-22) — gemini-2.5-flash is the current low-cost generateContent default.
    ("GEMINI_API_KEY", GeminiClient, "gemini-2.5-flash"),
]
# legacy fallback path for the Gemini key (kept so existing setups keep working)
_GEMINI_KEY_FILE = pathlib.Path.home() / ".secrets" / "stock" / "gemini-api-key.txt"


def make_llm_client(model: str | None = None) -> LLMClient | None:
    """Resolve a live LLM client from the environment. Precedence:

      1. IW_LIVE_PROVIDER=<name>  — force a provider from _PROVIDERS by its env var's stem
                                     (e.g. IW_LIVE_PROVIDER=xai -> XaiClient even if a Gemini
                                     key is also present).
      2. XAI_API_KEY               — xAI/Grok (the default live path).
      3. GEMINI_API_KEY            — Gemini.
      4. ~/.secrets/stock/gemini-api-key.txt — legacy Gemini key file (back-compat).
      5. None                       — no key present; caller falls back to the scripted mock.

    `model` overrides the provider default; IW_LIVE_MODEL env is the secondary override.
    Returns None when no key is configured — never raises on "no key"."""
    pinned = model or os.environ.get("IW_LIVE_MODEL")
    forced = os.environ.get("IW_LIVE_PROVIDER", "").strip().lower()

    def _build(env_name: str, cls: type, default_model: str) -> LLMClient | None:
        key = os.environ.get(env_name)
        if key and key.strip():
            return cls(key.strip(), model=pinned or default_model)
        return None

    # explicit override wins
    if forced:
        for env_name, cls, default_model in _PROVIDERS:
            if env_name.removesuffix("_API_KEY").lower() == forced:
                c = _build(env_name, cls, default_model)
                if c:
                    return c

    # standard cascade
    for env_name, cls, default_model in _PROVIDERS:
        c = _build(env_name, cls, default_model)
        if c:
            return c

    # legacy Gemini key file (back-compat for existing setups)
    if _GEMINI_KEY_FILE.exists():
        try:
            key = _GEMINI_KEY_FILE.read_text().strip()
        except OSError:
            key = ""
        if key:
            return GeminiClient(key, model=pinned or "gemini-2.5-flash")

    return None
