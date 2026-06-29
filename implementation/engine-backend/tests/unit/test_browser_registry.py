"""Browser-capability registry — offline contract tests (no Playwright, no network).

Covers: the CapabilityStore (register / resolve-by-intent / ready / remove / file-as-DB round-trip /
reload), the HybridAdapter routing (registered+ready -> live read, unregistered/unready/write -> demo
fallback, the bounded login-wait, wall + error passthrough), and the /capabilities HTTP surface via
TestClient with a FAKE browser + an in-memory store injected.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from engine.api.browser_tool import CapabilityStore, HybridAdapter, slugify
from engine.api.serve import build_app
from engine.domain import ProviderKind


# ── fakes ────────────────────────────────────────────────────────────────
class FakeBrowser:
    """Stands in for BrowserManager — records opens/reads, returns canned page text. No real browser."""

    def __init__(self, page_text="LIVE PAGE about SRE incident", wall=False, raise_on_read=False):
        self.mode = "fake"
        self.opened: list[tuple] = []
        self.reads: list[tuple] = []
        self.closed: list[str] = []
        self._text = page_text
        self._wall = wall
        self._raise = raise_on_read

    def open(self, key, url):
        self.opened.append((key, url))
        return {"opened": url, "title": "Fake", "mode": self.mode}

    def read(self, key, url=None):
        self.reads.append((key, url))
        if self._raise:
            raise RuntimeError("boom")
        return {"source": "browser", "key": key, "url": url, "title": "Fake",
                "page_text": self._text, "wall": self._wall, "evidence": [url or "fake://"]}

    def is_open(self, key):
        return any(k == key for k, _ in self.opened)

    def close(self, key):
        self.closed.append(key)


class FakeDemo:
    def invoke(self, capability_id, input):
        return {"demo": True, "capability": capability_id, "intent": (input or {}).get("intent")}


def _adapter(store, browser=None, **kw):
    return HybridAdapter(ProviderKind.mcp_remote, browser or FakeBrowser(), FakeDemo(), store, **kw)


# ── store ─────────────────────────────────────────────────────────────────
def test_slugify():
    assert slugify("Google Search") == "google-search"
    assert slugify("  ServiceNow!! ") == "servicenow"
    assert slugify("") == "cap"


def test_register_multi_intent_and_resolve():
    s = CapabilityStore()
    cap = s.register("ServiceNow", "https://sn", "tickets", ["incident-source", "topology"])
    assert cap.key == "servicenow" and cap.intents == ["incident-source", "topology"]
    assert s.for_intent("incident-source").key == "servicenow"
    assert s.for_intent("topology").key == "servicenow"
    assert s.for_intent("telemetry") is None


def test_for_intent_prefers_ready_and_needs_url():
    s = CapabilityStore()
    a = s.register("Tool A", "https://a", "", ["topology"])
    b = s.register("Tool B", "https://b", "", ["topology"])
    s.register("No URL", "", "", ["topology"])             # no url -> never resolved
    assert s.for_intent("topology").key == a.key           # first with a url when none ready
    b.ready = True
    assert s.for_intent("topology").key == b.key           # the ready one wins


def test_ready_intents_unions_only_ready_with_url():
    s = CapabilityStore()
    s.register("X", "https://x", "", ["topology", "metrics"]).ready = True
    s.register("Y", "", "", ["logs"]).ready = True         # no url -> excluded
    s.register("Z", "https://z", "", ["traces"])           # not ready
    assert s.ready_intents() == {"topology", "metrics"}


def test_remove():
    s = CapabilityStore()
    s.register("X", "https://x", "", ["topology"])
    assert s.remove("x").name == "X"
    assert s.remove("x") is None and s.get("x") is None


# ── file-as-DB ──────────────────────────────────────────────────────────────
def test_file_backed_load_save_roundtrip(tmp_path):
    p = tmp_path / "capabilities.json"
    p.write_text(json.dumps({"note": "n", "capabilities": [
        {"id": "servicenow", "label": "ServiceNow", "intents": ["incident-source"],
         "url": "https://sn", "effect": "read-only", "fields": ["a"], "what": "tix", "ready": True}]}))
    s = CapabilityStore(str(p))
    cap = s.get("servicenow")
    assert cap.intents == ["incident-source"] and cap.ready is True and cap.fields == ["a"]
    # a mutation persists to the file
    s.register("Datadog", "https://dd", "metrics", ["telemetry"])
    on_disk = json.loads(p.read_text())["capabilities"]
    keys = {c["id"] for c in on_disk}
    assert keys == {"servicenow", "datadog"}
    # a fresh store reads exactly what was written (round-trip)
    assert {c.key for c in CapabilityStore(str(p)).list()} == {"servicenow", "datadog"}


def test_reload_picks_up_external_edits(tmp_path):
    p = tmp_path / "capabilities.json"
    p.write_text(json.dumps({"capabilities": []}))
    s = CapabilityStore(str(p))
    assert s.list() == []
    p.write_text(json.dumps({"capabilities": [
        {"id": "splunk", "label": "Splunk", "intents": ["logs"], "url": "https://sp"}]}))
    s.reload()
    assert s.for_intent("logs").key == "splunk"


# ── adapter routing ────────────────────────────────────────────────────────
def test_unregistered_intent_uses_demo():
    s = CapabilityStore()
    out = _adapter(s).invoke("cmdb__topo", {"intent": "telemetry"})
    assert out == {"demo": True, "capability": "cmdb__topo", "intent": "telemetry"}


def test_ready_capability_reads_live_tab():
    s = CapabilityStore()
    cap = s.register("Google", "https://g/q", "", ["incident-source"])
    cap.ready = True
    br = FakeBrowser(page_text="results about payments latency")
    out = _adapter(s, br).invoke("cmdb__topo", {"intent": "incident-source"})
    assert out["source"] == "browser" and out["capability_name"] == "Google"
    assert br.reads == [("google", "https://g/q")]
    assert cap.reads == 1 and cap.last_excerpt.startswith("results about")


def test_write_capability_is_not_browser_read():
    s = CapabilityStore()
    cap = s.register("Runbook", "https://rb", "", ["remediation-action"], effect="write")
    cap.ready = True
    out = _adapter(s, FakeBrowser()).invoke("bl__failover", {"intent": "remediation-action"})
    assert out["demo"] is True            # a write tool is never auto-opened for reading


def test_structural_intent_stays_demo_so_graph_seeds():
    s = CapabilityStore()
    cap = s.register("ServiceNow", "https://sn", "", ["topology"])   # CMDB mapped to topology
    cap.ready = True
    out = _adapter(s, FakeBrowser()).invoke("cmdb__topo", {"intent": "topology"})
    assert out["demo"] is True            # topology keeps demo data so the graph fold builds nodes


def test_wall_flag_is_recorded():
    s = CapabilityStore()
    cap = s.register("Walled", "https://w", "", ["logs"])
    cap.ready = True
    out = _adapter(s, FakeBrowser(page_text="Our systems have detected unusual traffic", wall=True)).invoke(
        "x", {"intent": "logs"})
    assert out["wall"] is True and cap.wall is True


def test_browser_error_falls_back_to_demo():
    s = CapabilityStore()
    s.register("Boom", "https://b", "", ["logs"]).ready = True
    out = _adapter(s, FakeBrowser(raise_on_read=True)).invoke("x", {"intent": "logs"})
    assert out["demo"] is True and "browser_error" in out


def test_unready_capability_waits_then_falls_back():
    s = CapabilityStore()
    s.register("Slow", "https://s", "", ["logs"])          # registered, never made ready
    calls = {"n": 0}
    out = _adapter(s, wait_timeout=1.0, poll=0.5, sleep=lambda _: calls.__setitem__("n", calls["n"] + 1)
                   ).invoke("x", {"intent": "logs"})
    assert out["demo"] is True and "browser_pending" in out
    assert calls["n"] == 2                                  # 1.0 / 0.5 = 2 polls, then degrade


def test_unready_capability_reads_once_login_completes():
    s = CapabilityStore()
    cap = s.register("Login", "https://l", "", ["logs"])
    br = FakeBrowser()

    def flip(_):                                            # human finishes login mid-wait
        cap.ready = True

    out = _adapter(s, br, wait_timeout=5.0, poll=0.5, sleep=flip).invoke("x", {"intent": "logs"})
    assert out["source"] == "browser" and br.reads == [("login", "https://l")]


# ── HTTP surface ───────────────────────────────────────────────────────────
@pytest.fixture
def client():
    store = CapabilityStore()              # in-memory — does not touch the real capabilities.json
    browser = FakeBrowser()
    app = build_app(browser=browser, store=store)
    c = TestClient(app)
    c.fake_browser = browser   # type: ignore[attr-defined]
    c.store = store            # type: ignore[attr-defined]
    return c


def test_register_capability_opens_tab(client):
    r = client.post("/capabilities", json={"name": "ServiceNow", "url": "https://sn/inc",
                                           "description": "ticket", "intents": ["incident-source", "topology"]})
    assert r.status_code == 200
    cap = r.json()["capability"]
    assert cap["key"] == "servicenow" and cap["opened"] is True and cap["ready"] is False
    assert cap["intents"] == ["incident-source", "topology"]
    assert ("servicenow", "https://sn/inc") in client.fake_browser.opened


def test_list_ready_remove(client):
    client.post("/capabilities", json={"name": "Tool", "url": "https://t", "intents": ["topology"]})
    assert len(client.get("/capabilities").json()["capabilities"]) == 1
    assert client.post("/capabilities/tool/ready", json={"ready": True}).json()["capability"]["ready"] is True
    assert client.store.ready_intents() == {"topology"}
    assert client.delete("/capabilities/tool").json()["removed"] == "tool"
    assert "tool" in client.fake_browser.closed
    assert client.get("/capabilities").json()["capabilities"] == []


def test_ready_unknown_capability_404(client):
    assert client.post("/capabilities/nope/ready", json={"ready": True}).status_code == 404


def test_register_demo_caps(client):
    out = client.post("/capabilities/demo").json()["capabilities"]
    intents = {i for c in out for i in c["intents"]}
    assert intents == {"incident-source", "similar-incidents"}
    assert {c["key"] for c in out} == {"google-search", "google-images"}
