"""Hermetic tests for the LLM client seam (principle 11: judgment is swappable).

Pins: (1) the LLMClient Protocol is structural — the shipped XaiClient/GeminiClient
satisfy it without inheritance; (2) ANY class implementing `complete_json` + `.name`
can drive a LivePlanner (the pluggability contract); (3) the factory precedence
(xAI-first) and the IW_LIVE_PROVIDER override. No network calls — a stub client
returns canned JSON, so LivePlanner.plan() exercises its full reject+repair path
hermetically.
"""
from __future__ import annotations

import email.message
import io
import json
import os
import urllib.error
from unittest import mock

import pytest

from iw_engine.runtime.live_planner import LivePlanner
from iw_engine.runtime.llm_client import (
    GeminiClient,
    LLMClient,
    XaiClient,
    loads_salvage,
    make_llm_client,
    retry_delay,
)


# ── the Protocol is structural (no inheritance needed) ─────────────────────────
def test_shipped_clients_satisfy_protocol():
    """XaiClient and GeminiClient structurally satisfy LLMClient — they have a `.name`
    attribute and a `complete_json(system, user) -> dict` method. No subclassing."""
    x = XaiClient("k", model="grok-4.5")
    g = GeminiClient("k", model="gemini-2.5-flash-lite")
    assert isinstance(x, LLMClient)
    assert isinstance(g, LLMClient)
    assert x.name.startswith("xai/") and g.name.startswith("gemini/")


class _StubClient:
    """A minimal client: returns whatever JSON it's handed. Satisfies LLMClient by
    structure — proves ANY provider can drive the planner with a ~10-line class."""
    def __init__(self, payload: dict):
        self.payload = payload
        self.name = "stub/test"

    def complete_json(self, system: str, user: str) -> dict:
        return self.payload


def test_stub_client_satisfies_protocol():
    assert isinstance(_StubClient({}), LLMClient)


# ── any-LLM pluggability: a stub client drives the LivePlanner end-to-end ──────
def test_stub_client_drives_live_planner_one_phase():
    """The pluggability contract: a stub LLM (canned JSON) drives LivePlanner.plan()
    through its full reject+repair path. The planner never touches the network — it
    just calls client.complete_json and maps the dict to a PlanOutput. A real provider
    (Anthropic/OpenAI/local) plugs in identically by swapping the client."""
    from iw_engine.domain.playbook import PhaseSpec
    from iw_engine.domain.subject import SubjectRef
    from iw_engine.runtime.planner import PlanContext

    # a minimal valid FRAME plan: one add_node on an anomaly the model "sees"
    payload = {
        "reasoning": "stub frame",
        "calls": [],
        "ops": [{"op": "add_node", "type": "anomaly", "anomaly_id": "ANOM-1"}],
        "narrative": "stub",
        "verdict": {"status": "advance", "confidence_level": "high"},
        "next_actions": [],
    }
    planner = LivePlanner(_StubClient(payload), "catalog", "tools", set())
    ctx = PlanContext(
        subject=SubjectRef(domain="app-incident", id="INC-1", kind="incident"),
        phase="frame",
        phase_spec=PhaseSpec(id="frame", goal="seed the symptom", allowed_intents=[]),
        goal="seed the symptom",
        hypotheses=[],
    )
    planner.graph = None   # no live graph ref -> no graph projections (hermetic default)
    out = planner.plan(ctx)
    assert out.phase == "frame"
    assert out.narrative == "stub"
    # the stub's add_node op survived reject+repair and landed in the plan — proving a
    # non-network client fully drives the planner (the any-LLM contract)
    from iw_engine.domain.operations import AddNode
    assert any(isinstance(op, AddNode) and op.type.value == "anomaly" for op in out.ops)


# ── reject+repair on NON-DICT LLM payloads (INV-7; 2026-07-22 review, finding 5) ──
def _ctx():
    from iw_engine.domain.playbook import PhaseSpec
    from iw_engine.domain.subject import SubjectRef
    from iw_engine.runtime.planner import PlanContext
    return PlanContext(
        subject=SubjectRef(domain="app-incident", id="INC-1", kind="incident"),
        phase="frame",
        phase_spec=PhaseSpec(id="frame", goal="g", allowed_intents=[]),
        goal="g", hypotheses=[])


def test_live_planner_repairs_non_dict_op_payloads():
    """A string/list/None op or call entry — or a bare-string verdict — must be
    dropped+recorded (reject+repair), never an AttributeError that kills the live
    session. The repair branch itself used to crash on non-dict ops (finding 5)."""
    payload = {
        "reasoning": "adversarial",
        "calls": ["prometheus"],                       # non-dict call entry
        "ops": ["garbage", ["nested"], None,           # non-dict op payloads
                {"op": "add_node", "type": "anomaly", "anomaly_id": "ANOM-1"}],
        "narrative": "n",
        "verdict": "advance",                          # bare-string verdict
    }
    planner = LivePlanner(_StubClient(payload), "catalog", "tools", set(), verbose=False)
    out = planner.plan(_ctx())
    from iw_engine.domain.operations import AddNode
    assert [type(op) for op in out.ops] == [AddNode]   # the one valid op survived
    assert out.calls == []
    assert out.verdict.status.value == "advance"       # bare string coerced to a status
    # one repair per dropped/coerced item: 1 call + 3 ops + 1 verdict
    assert len(planner.repairs) == 5


def test_live_planner_repairs_non_dict_top_level():
    """A top-level JSON array / bare string / None from the model is repaired to an
    EMPTY plan with a recorded repair — not an uncaught raw.get crash."""
    for bad in (["not", "a", "plan"], "just prose", None):
        planner = LivePlanner(_StubClient(bad), "catalog", "tools", set(), verbose=False)
        out = planner.plan(_ctx())
        assert out.ops == [] and out.calls == []
        assert any("non-dict plan payload" in r for r in planner.repairs), bad


# ── the Gemini key travels in a header, never the URL (2026-07-22 review, finding 6) ──
class _Resp:
    def __init__(self, payload: dict):
        import json as _json
        self._data = _json.dumps(payload).encode()

    def read(self, *a):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_gemini_key_sent_in_header_not_url():
    captured = {}

    def fake_urlopen(req, timeout=None):
        captured["req"] = req
        return _Resp({"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]})

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
        c = GeminiClient("sk-secret", min_interval=0.0)
        out = c.complete_json("system", "user")
    assert out == {"ok": 1}
    req = captured["req"]
    assert "sk-secret" not in req.full_url          # the secret never appears in the URL
    assert "key=" not in req.full_url               # no ?key= query param at all
    assert req.get_header("X-goog-api-key") == "sk-secret"   # header auth instead


# ── factory precedence + override (env-driven, monkey-patched) ─────────────────
def test_factory_returns_none_when_no_key():
    """No XAI_API_KEY, no GEMINI_API_KEY, no key file -> None (caller falls back to mock)."""
    env = {"XAI_API_KEY": "", "GEMINI_API_KEY": "", "IW_LIVE_PROVIDER": "", "IW_LIVE_MODEL": ""}
    with mock.patch.dict(os.environ, env, clear=True), \
         mock.patch("iw_engine.runtime.llm_client._GEMINI_KEY_FILE") as f:
        f.exists.return_value = False
        assert make_llm_client() is None


def test_factory_xai_first_when_both_keys_present():
    """xAI wins when both XAI_API_KEY and GEMINI_API_KEY are set (the documented default)."""
    env = {"XAI_API_KEY": "xai-key", "GEMINI_API_KEY": "gem-key",
           "IW_LIVE_PROVIDER": "", "IW_LIVE_MODEL": ""}
    with mock.patch.dict(os.environ, env, clear=True):
        c = make_llm_client()
    assert isinstance(c, XaiClient)
    assert c.name == "xai/grok-4.5"


def test_factory_gemini_when_only_gemini_key():
    env = {"XAI_API_KEY": "", "GEMINI_API_KEY": "gem-key",
           "IW_LIVE_PROVIDER": "", "IW_LIVE_MODEL": ""}
    with mock.patch.dict(os.environ, env, clear=True):
        c = make_llm_client()
    assert isinstance(c, GeminiClient)


def test_factory_provider_override_forces_gemini_even_with_xai_key():
    """IW_LIVE_PROVIDER=gemini forces Gemini even when XAI_API_KEY is set."""
    env = {"XAI_API_KEY": "xai-key", "GEMINI_API_KEY": "gem-key",
           "IW_LIVE_PROVIDER": "gemini", "IW_LIVE_MODEL": ""}
    with mock.patch.dict(os.environ, env, clear=True):
        c = make_llm_client()
    assert isinstance(c, GeminiClient)


def test_factory_model_pin_via_env():
    """IW_LIVE_MODEL overrides the provider's default model string."""
    env = {"XAI_API_KEY": "xai-key", "GEMINI_API_KEY": "",
           "IW_LIVE_PROVIDER": "", "IW_LIVE_MODEL": "grok-custom"}
    with mock.patch.dict(os.environ, env, clear=True):
        c = make_llm_client()
    assert isinstance(c, XaiClient)
    assert c.model == "grok-custom"


# ── the verdict band comes from the playbook tunables, not a planner constant ──
def test_verdict_confidence_uses_tunables_confidence_band():
    """The coarse verdict level maps to a numeric via ctx.tunables.confidence_band —
    a playbook that re-tunes the band moves the planner's verdict confidence with it
    (no hardcoded _BAND constant in the planner)."""
    from dataclasses import replace

    from iw_engine.domain.playbook import Tunables

    payload = {"reasoning": "r", "narrative": "n",
               "verdict": {"status": "advance", "confidence_level": "high"}}
    ctx = replace(_ctx(), tunables=Tunables(
        confidence_band={"low": 0.2, "med": 0.5, "high": 0.95}))
    planner = LivePlanner(_StubClient(payload), "catalog", "tools", set(), verbose=False)
    out = planner.plan(ctx)
    assert out.verdict.confidence.value == 0.95   # the playbook's band, not 0.9


# ── loads_salvage on adversarial model output (never re-calls the API) ─────────
def test_salvage_fenced_json_block():
    """A ```json fenced block (the classic markdown-wrapped answer) parses cleanly."""
    assert loads_salvage('```json\n{"a": 1}\n```') == {"a": 1}


def test_salvage_fenced_block_without_language_tag():
    assert loads_salvage('```\n{"a": 1}\n```') == {"a": 1}


def test_salvage_json_embedded_in_prose():
    """'Sure! Here is the plan: {...}. Hope that helps.' — the outermost object is
    salvaged from surrounding chatter."""
    text = 'Sure! Here is the plan: {"ok": true}. Hope that helps.'
    assert loads_salvage(text) == {"ok": True}


def test_salvage_balanced_object_with_truncated_tail():
    """A complete object followed by a truncated second one (cut off mid-stream)
    salvages the balanced prefix instead of raising."""
    text = '{"plan": {"x": 1}}\nextra prose {"partial": '
    assert loads_salvage(text) == {"plan": {"x": 1}}


def test_salvage_takes_first_object_of_concatenated_pair():
    assert loads_salvage('{"a": 1}{"b": 2}') == {"a": 1}


@pytest.mark.parametrize("bad", [
    "",                             # empty response
    None,                           # provider returned no text at all
    "total garbage no braces",      # pure prose, nothing to salvage
    '{"a": 1, "b": ',               # truncated before any balanced close
    '{"a": [1, 2',                  # truncated inside an array
])
def test_salvage_unrecoverable_raises_without_recall(bad):
    """Unsalvageable output raises RuntimeError (the caller decides; salvage NEVER
    re-calls the API — quota is precious)."""
    with pytest.raises(RuntimeError, match="unparseable LLM response"):
        loads_salvage(bad)


# ── the 429/503 retry loop (mocked urlopen; no network, no real sleeps) ────────
def _http_error(code: int, headers: dict | None = None, body: bytes = b"") -> urllib.error.HTTPError:
    hdrs = email.message.Message()
    for k, v in (headers or {}).items():
        hdrs[k] = v
    return urllib.error.HTTPError("https://api.example/x", code, "err", hdrs, io.BytesIO(body))


def test_retry_delay_honors_retry_after_header():
    assert retry_delay(_http_error(429, {"Retry-After": "7"}), fallback=99.0) == 7.0


def test_retry_delay_caps_huge_retry_after():
    """A huge daily-quota hint is capped so it can't wedge the run for minutes."""
    assert retry_delay(_http_error(429, {"Retry-After": "600"}), fallback=4.0) == 90.0


def test_retry_delay_honors_google_retrydelay_body():
    body = json.dumps({"error": {"details": [{"retryDelay": "42s"}]}}).encode()
    assert retry_delay(_http_error(429, body=body), fallback=99.0) == 42.0


def test_retry_delay_falls_back_when_no_hint():
    assert retry_delay(_http_error(503), fallback=8.0) == 8.0
    assert retry_delay(_http_error(503), fallback=500.0) == 90.0   # fallback capped too


def test_retry_delay_ignores_non_numeric_retry_after():
    """An HTTP-date Retry-After (unsupported format) falls through to the fallback."""
    assert retry_delay(
        _http_error(429, {"Retry-After": "Wed, 22 Jul 2026 09:00:00 GMT"}), fallback=6.0) == 6.0


def test_gemini_429_loop_honors_retry_after_then_raises():
    """Six attempts, each 429 with Retry-After: 7 — the loop sleeps the SERVER's hint
    (not the exponential fallback) five times, then re-raises the HTTPError."""
    sleeps: list[float] = []
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise _http_error(429, {"Retry-After": "7"})

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
         mock.patch("time.sleep", side_effect=sleeps.append):
        c = GeminiClient("k", min_interval=0.0)
        with pytest.raises(urllib.error.HTTPError):
            c.complete_json("system", "user")
    assert calls["n"] == 6            # initial try + 5 retries, then exhausted
    assert sleeps == [7.0] * 5        # Retry-After honored on every back-off


def test_gemini_503_retries_then_succeeds():
    """One 503 carrying a Google RetryInfo retryDelay, then success — the loop sleeps
    exactly the hinted 3s and returns the parsed payload."""
    sleeps: list[float] = []
    body = json.dumps({"error": {"details": [{"retryDelay": "3s"}]}}).encode()
    errors = [_http_error(503, body=body)]

    def fake_urlopen(req, timeout=None):
        if errors:
            raise errors.pop()
        return _Resp({"candidates": [{"content": {"parts": [{"text": '{"ok": 1}'}]}}]})

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
         mock.patch("time.sleep", side_effect=sleeps.append):
        c = GeminiClient("k", min_interval=0.0)
        out = c.complete_json("system", "user")
    assert out == {"ok": 1}
    assert sleeps == [3.0]


def test_gemini_400_raises_immediately_without_retry():
    """A non-retryable status (400) must raise on the FIRST attempt — no sleeps."""
    sleeps: list[float] = []
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise _http_error(400)

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
         mock.patch("time.sleep", side_effect=sleeps.append):
        c = GeminiClient("k", min_interval=0.0)
        with pytest.raises(urllib.error.HTTPError):
            c.complete_json("system", "user")
    assert calls["n"] == 1 and sleeps == []


def test_xai_429_exponential_backoff_then_raises():
    """XaiClient backs off 4*(2**attempt) on persistent 429 and raises after 6 attempts."""
    sleeps: list[float] = []
    calls = {"n": 0}

    def fake_urlopen(req, timeout=None):
        calls["n"] += 1
        raise _http_error(429)

    with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen), \
         mock.patch("time.sleep", side_effect=sleeps.append):
        c = XaiClient("k")
        with pytest.raises(urllib.error.HTTPError):
            c.complete_json("system", "user")
    assert calls["n"] == 6
    assert sleeps == [4, 8, 16, 32, 64]
