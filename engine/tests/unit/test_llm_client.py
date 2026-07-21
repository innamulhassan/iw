"""Hermetic tests for the LLM client seam (principle 11: judgment is swappable).

Pins: (1) the LLMClient Protocol is structural — the shipped XaiClient/GeminiClient
satisfy it without inheritance; (2) ANY class implementing `complete_json` + `.name`
can drive a LivePlanner (the pluggability contract); (3) the factory precedence
(xAI-first) and the IW_LIVE_PROVIDER override. No network calls — a stub client
returns canned JSON, so LivePlanner.plan() exercises its full reject+repair path
hermetically.
"""
from __future__ import annotations

import os
from unittest import mock

from iw_engine.domain.enums import Phase
from iw_engine.runtime.live_planner import LivePlanner
from iw_engine.runtime.llm_client import (
    GeminiClient,
    LLMClient,
    XaiClient,
    make_llm_client,
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
        phase=Phase.FRAME,
        phase_spec=PhaseSpec(id=Phase.FRAME, goal="seed the symptom", allowed_intents=[]),
        goal="seed the symptom",
        graph_view={},
        hypotheses=[],
    )
    planner.graph = None   # bypass render_graph_full (uses the live graph ref)
    out = planner.plan(ctx)
    assert out.phase == Phase.FRAME
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
        phase=Phase.FRAME,
        phase_spec=PhaseSpec(id=Phase.FRAME, goal="g", allowed_intents=[]),
        goal="g", graph_view={}, hypotheses=[])


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
