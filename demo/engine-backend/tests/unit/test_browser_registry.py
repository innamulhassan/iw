"""Browser-capability registry + demo-store projector — offline contract tests (no Playwright, no net).

Registry is file-as-DB (url + description); login is on-demand (no ready flag) — the adapter reads, and
if a login wall shows it waits then falls back to demo data. Covers the store, the HybridAdapter routing
(configured -> live read · unconfigured/structural/write -> demo · wall -> wait then demo · wall clears
-> live), the /capabilities surface, and the engine->demo-incident-JSON projection.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from engine.api.browser_tool import CapabilityStore, HybridAdapter, slugify
from engine.api.demo_store import project_demo
from engine.api.serve import build_app
from engine.domain import ProviderKind


# ── fakes ────────────────────────────────────────────────────────────────
class FakeBrowser:
    def __init__(self, page_text="LIVE PAGE about SRE incident", wall=False, raise_on_read=False):
        self.mode = "fake"
        self.opened: list = []
        self.reads: list = []
        self.closed: list = []
        self._text = page_text
        self.wall = wall
        self._raise = raise_on_read

    def open(self, key, url):
        self.opened.append((key, url))
        return {"opened": url, "title": "Fake", "mode": self.mode}

    def read(self, key, url=None):
        self.reads.append((key, url))
        if self._raise:
            raise RuntimeError("boom")
        return {"source": "browser", "key": key, "url": url, "title": "Fake",
                "page_text": self._text, "wall": self.wall, "evidence": [url or "fake://"]}

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
    cap = s.register("ServiceNow", "https://sn", "tickets", ["incident-source", "logs"])
    assert cap.key == "servicenow" and cap.intents == ["incident-source", "logs"]
    assert s.for_intent("incident-source").key == "servicenow"
    assert s.for_intent("telemetry") is None


def test_for_intent_needs_url_and_skips_write():
    s = CapabilityStore()
    s.register("No URL", "", "", ["logs"])
    assert s.for_intent("logs") is None                    # no url -> not resolvable
    s.register("Runbook", "https://rb", "", ["remediation-action"], effect="write")
    assert s.for_intent("remediation-action") is None      # write tools aren't browser-read
    s.register("Datadog", "https://dd", "", ["telemetry"])
    assert s.for_intent("telemetry").key == "datadog"


def test_live_intents_url_and_read_only_only():
    s = CapabilityStore()
    s.register("A", "https://a", "", ["telemetry", "metrics"])
    s.register("B", "", "", ["logs"])                      # no url -> excluded
    s.register("W", "https://w", "", ["remediation-action"], effect="write")  # write -> excluded
    assert s.live_intents() == {"telemetry", "metrics"}


def test_remove():
    s = CapabilityStore()
    s.register("X", "https://x", "", ["logs"])
    assert s.remove("x").name == "X"
    assert s.remove("x") is None and s.get("x") is None


def test_file_backed_roundtrip_and_reload(tmp_path):
    p = tmp_path / "capabilities.json"
    p.write_text(json.dumps({"note": "n", "capabilities": [
        {"id": "servicenow", "label": "ServiceNow", "intents": ["incident-source"],
         "url": "https://sn", "effect": "read-only", "description": "tix"}]}))
    s = CapabilityStore(str(p))
    assert s.get("servicenow").description == "tix"
    s.register("Datadog", "https://dd", "metrics", ["telemetry"])
    assert {c["id"] for c in json.loads(p.read_text())["capabilities"]} == {"servicenow", "datadog"}
    # external edit + reload
    p.write_text(json.dumps({"capabilities": [
        {"id": "splunk", "label": "Splunk", "intents": ["logs"], "url": "https://sp"}]}))
    s.reload()
    assert {c.key for c in s.list()} == {"splunk"}


# ── adapter routing ────────────────────────────────────────────────────────
def test_unconfigured_intent_uses_demo():
    s = CapabilityStore()
    assert _adapter(s).invoke("c", {"intent": "telemetry"})["demo"] is True


def test_configured_intent_reads_live():
    s = CapabilityStore()
    s.register("Datadog", "https://dd", "", ["telemetry"])
    s.mark_ready("datadog")                                  # only read live once marked ready
    br = FakeBrowser(page_text="p99 4200ms")
    out = _adapter(s, br).invoke("c", {"intent": "telemetry"})
    assert out["source"] == "browser" and out["capability_name"] == "Datadog"
    assert br.reads[0] == ("datadog", "https://dd")
    assert s.get("datadog").reads == 1


def test_structural_intent_stays_demo_so_graph_seeds():
    s = CapabilityStore()
    s.register("CMDB", "https://sn", "", ["topology"])
    assert _adapter(s, FakeBrowser()).invoke("c", {"intent": "topology"})["demo"] is True


def test_persistent_wall_falls_back_to_demo():
    s = CapabilityStore()
    s.register("Datadog", "https://dd", "", ["telemetry"])
    s.mark_ready("datadog")                                  # only read live once marked ready
    out = _adapter(s, FakeBrowser(wall=True), wait_timeout=1.0, poll=0.5, sleep=lambda _: None
                   ).invoke("c", {"intent": "telemetry"})
    assert out["demo"] is True and "browser_wall" in out
    assert s.get("datadog").wall is True


def test_wall_clears_after_login_then_reads_live():
    s = CapabilityStore()
    s.register("Datadog", "https://dd", "", ["telemetry"])
    s.mark_ready("datadog")                                  # only read live once marked ready
    br = FakeBrowser(wall=True)
    out = _adapter(s, br, wait_timeout=5.0, poll=0.5,
                   sleep=lambda _: setattr(br, "wall", False)   # human logs in mid-wait
                   ).invoke("c", {"intent": "telemetry"})
    assert out["source"] == "browser" and s.get("datadog").reads == 1


def test_browser_error_falls_back_to_demo():
    s = CapabilityStore()
    s.register("Boom", "https://b", "", ["telemetry"])
    s.mark_ready("boom")                                     # only read live once marked ready
    out = _adapter(s, FakeBrowser(raise_on_read=True)).invoke("c", {"intent": "telemetry"})
    assert out["demo"] is True and "browser_error" in out

def test_not_ready_uses_demo_data():
    s = CapabilityStore()
    s.register("Datadog", "https://dd", "", ["telemetry"])
    # NOT marked ready -> demo data (the office flow: open + log in + mark Ready first)
    out = _adapter(s, FakeBrowser()).invoke("c", {"intent": "telemetry"})
    assert out["demo"] is True and s.get("datadog").reads == 0


# ── HTTP surface ───────────────────────────────────────────────────────────
@pytest.fixture
def client():
    store = CapabilityStore()              # in-memory — does not touch the real capabilities.json
    app = build_app(browser=FakeBrowser(), store=store)
    c = TestClient(app)
    c.store = store                        # type: ignore[attr-defined]
    return c


def test_register_and_list(client):
    r = client.post("/capabilities", json={"name": "ServiceNow", "url": "https://sn",
                                           "description": "tix", "intents": ["incident-source"]})
    assert r.status_code == 200 and r.json()["capability"]["intents"] == ["incident-source"]
    assert len(client.get("/capabilities").json()["capabilities"]) == 1


def test_demo_caps_and_remove(client):
    out = client.post("/capabilities/demo").json()["capabilities"]
    assert {c["key"] for c in out} == {"google-search", "google-images"}
    assert client.delete("/capabilities/google-search").json()["removed"] == "google-search"


# ── projector ───────────────────────────────────────────────────────────────
class _FakeGraph:
    def node_ids(self):
        return []

    class _G:
        @staticmethod
        def edges(data=False):
            return []
    _g = _G()


def test_project_demo_shapes_the_incident_json():
    subject = {"domain": "app-incident", "id": "INC-9", "kind": "incident"}
    values = {"phase_records": [{
        "id": "INC-9:assess:1", "phase": "assess", "state": "done", "goal": "assess it",
        "output": {"symptom": "p99 spike"},
        "steps": [{"seq": 1, "kind": "tool_call", "capability": "cmdb__topo",
                   "input": {"intent": "incident-source"}, "result": {"x": 1}, "at": "09:00"}],
    }]}
    doc = project_demo(subject, values, _FakeGraph(), status="waiting_approval",
                       pending_gate={"phase": "remediation", "proposal": "p"})
    assert doc["incident"] == "INC-9" and doc["status"] == "waiting_approval"
    assert doc["title"] == "p99 spike" and doc["current_phase"] == "assess"
    assert doc["graph"]["nodes"][0]["health"] == "subject"     # incident root
    ph = doc["phases"][0]
    assert ph["phase"] == "assess" and ph["steps"][0]["intent"] == "incident-source"
    assert ph["steps"][0]["headline"].startswith("Read")
