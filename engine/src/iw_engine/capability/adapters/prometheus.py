"""Prometheus adapter — metrics + alerts. REFERENCE adapter (the template the other 7
follow): a `provider`, an `intents` set, an `effect`, and a pure `normalize(raw)` that
maps the tool's raw JSON shape into typed Operations folding into the incident graph.
The raw shape is realistic and tool-specific; the mock swaps only the query() boundary.
"""
from __future__ import annotations

from ...domain import registry
from ...domain.assertion import Window
from ...domain.common import EvidenceRef
from ...domain.enums import Binding, EdgeType, Effect, NodeType, Source, Species, Stat
from ...domain.operations import AddAssertion, AddEdge, AddNode, Operation
from ..layer import CapabilityMeta


class PrometheusAdapter:
    provider = "prometheus"
    intents = frozenset({"active_alerts", "instant_query", "range_query", "fetch_metrics"})
    effect = Effect.READ
    binding = Binding.REST   # no first-party MCP server — trivial raw REST
    meta = CapabilityMeta(
        summary="Time-series metrics (RED/USE) and firing alerts",
        queries_by="service_name", returns="alerts + metric facts")

    def normalize(self, raw: dict) -> list[Operation]:
        ops: list[Operation] = []
        svc = raw.get("service")
        svc_id: str | None = None
        if svc:
            props = {"service_name": svc["name"], "env": svc["env"]}
            ops.append(AddNode(type=NodeType.SERVICE, props=props))
            svc_id = registry.node_id(NodeType.SERVICE, props)

        for al in raw.get("alerts", []):
            aid = registry.node_id(NodeType.ALERT, {"alert_id": al["id"]})
            ops.append(AddNode(type=NodeType.ALERT,
                               props={"alert_id": al["id"], "rule": al.get("alertname")}))
            ops.append(AddAssertion(subject=aid, name="fired", species=Species.EVENT,
                                    occurred_at=al["at"], observed_at=al["at"],
                                    value={"state": al.get("state", "firing")},
                                    source=Source.PROMETHEUS, source_native_name="fired"))
            if svc_id:
                ops.append(AddEdge(type=EdgeType.FIRED_ON, src=aid, dst=svc_id))

        for m in raw.get("metrics", []):
            subject = m.get("subject", svc_id)
            if not subject:
                continue
            if "subject" in m:
                # An explicitly-subjected metric NAMES the entity the exporter probes —
                # that naming IS discovery (a blackbox probe of certificate:X watches X).
                # Mint the typed node so the reading can land even when the entity's
                # primary discoverer (artifactory for certs, servicenow for flags) has
                # not been called yet: the live retest (2026-07-22) lost 5 days_to_expiry
                # readings to 'unknown subject' ordering — the very facts hinting the
                # planner to look at the cert. node_id is idempotent, so re-minting an
                # already-discovered entity is a no-op upsert.
                self._discover(ops, subject)
            # telemetry gauge/rate/ratio → a summary READING (measured with a window) PLUS a
            # `metric_query` HANDLE (the unifying stream-ladder, 2026-07-23 primitives §3/§8.1: NEVER
            # inline a raw stream — author the judgment-granularity UNIT + a handle to re-fetch the
            # curve live). The fixtures state neither stat nor window, so stat=gauge + a point window
            # at the observation time keeps the reducer's Fact byte-identical; the vendor's own metric
            # name survives on source_native_name; the handle is graph-internal (evidence[], not
            # bundle-serialized) so the goldens stay byte-identical.
            ops.append(AddAssertion(subject=subject, name=m["predicate"], value=m["value"],
                                    unit=m.get("unit"), species=Species.READING,
                                    stat=Stat.GAUGE, window=Window(at=m["at"]),
                                    valid_from=m["at"], observed_at=m["at"],
                                    source=Source.PROMETHEUS, source_reliability=m.get("reliability"),
                                    evidence=[EvidenceRef(kind="metric_query",
                                                          ref=f'{m["predicate"]}{{target="{subject}"}}',
                                                          label=m["predicate"])],
                                    source_native_name=m["predicate"]))
        return ops

    @staticmethod
    def _discover(ops: list[Operation], subject: str) -> None:
        """Mint the typed node an explicit metric subject names ("<type>:<identity>",
        identity parts |-separated in identity-key order). Only a subject that parses to
        a known NodeType with a matching identity-arity is minted — anything else is left
        to the reducer's referential-integrity rejection."""
        tname, _, ident = subject.partition(":")
        if not ident:
            return
        try:
            nt = NodeType(tname)
        except ValueError:
            return
        keys = registry.node_spec(nt).identity_keys
        parts = ident.split("|")
        if not keys or len(parts) != len(keys) or not all(parts):
            return
        ops.append(AddNode(type=nt, props=dict(zip(keys, parts, strict=False))))
