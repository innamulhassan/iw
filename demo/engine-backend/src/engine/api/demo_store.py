"""Project the live LangGraph engine's run state into the DEMO incident-JSON schema that
`ux-console.html` / `viewer.html` read (and poll every 2s), and write it to `incidents/<INC>.json`.

This is the bridge that makes the existing mock UI render a REAL run: the engine stays unchanged; this
just shapes its state (phase records + the incident graph) into the file the consoles already animate.
The schema mirrors the hand-authored exemplars (INC-2290.json): incident · status · current_phase ·
pending_gate · graph{nodes,edges} · phases[{steps}].
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional


# The engine's `NodeKind` is coarse (system | incident | change | alert); a system node carries its
# finer category in `layer` (business|app|database|network|storage|compute|location|external). The demo
# graph-schema wants the FINE node_types (app | service | database | storage | compute | network |
# external | cluster | change | alert | incident). This bridge translates one vocabulary into the other —
# non-system kinds pass through; system nodes resolve to their layer/type. (Hand-authored exemplars use
# the fine vocabulary directly, which is why they validate but the engine projection did not.)
_LAYER_TO_NODETYPE = {
    "app": "app", "service": "service", "database": "database", "storage": "storage",
    "compute": "compute", "network": "network", "external": "external",
    "location": "external", "business": "service",
}


def _node_kind(node) -> str:
    """Engine Node.kind -> demo graph-schema node_type."""
    k = getattr(node, "kind", None)
    if k != "system":
        return k or "service"                       # incident / change / alert pass through (all valid)
    return (_LAYER_TO_NODETYPE.get(getattr(node, "layer", None) or "")
            or _LAYER_TO_NODETYPE.get(getattr(node, "type", None) or "")
            or "service")                           # layer wins; type backs it up; generic fallback


def _phase_of_node(node_id: str, edges_by_node: dict) -> str:
    return edges_by_node.get(node_id, "assess")


def _node_health(node) -> str:
    """Engine health verdict -> demo health enum (subject set separately on the incident root)."""
    for f in node.facts:
        st = getattr(f, "impact_state", None)
        if st is not None and getattr(st, "value", st) != "ok":
            return "impacted"
    if "suspect" in (node.labels or []):
        return "suspect"
    if "cause" in (node.labels or []):
        return "cause"
    return "healthy"


def _facts(node) -> list[dict]:
    out = []
    for f in node.facts:
        out.append({"key": f.key, "value": f.value, "source": f.source,
                    "field": f.evidence_ref or "", "at": f.observed_at or ""})
    return out


def _assess_symptom(records: list[dict]) -> str:
    for r in records:
        out = r.get("output") or {}
        if out.get("symptom"):
            return out["symptom"]
    return ""


def _headline(step: dict) -> str:
    kind = step.get("kind")
    intent = (step.get("input") or {}).get("intent")
    if kind == "tool_call":
        return f"Read {intent or step.get('capability') or 'tool'}"
    if kind == "reasoning":
        return (step.get("note") or "Reasoning")[:90]
    if kind == "suggestion":
        return "Proposed a remediation"
    if kind == "decision":
        return "Operator decision"
    return kind or "step"


def _short(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result[:240]
    try:
        s = json.dumps(result, ensure_ascii=False)
    except Exception:
        s = str(result)
    return s[:240]


def _project_step(rec: dict, step: dict) -> dict:
    sid = f"{rec.get('id', rec.get('phase'))}#{step.get('seq')}"
    kind = step.get("kind")
    intent = (step.get("input") or {}).get("intent")
    out: dict[str, Any] = {
        "id": sid, "seq": step.get("seq"), "kind": kind,
        "actor": {"id": "engine", "role": "agent"},
        "headline": _headline(step), "started_at": step.get("at"), "status": "ok",
    }
    if kind == "tool_call":
        out["capability"] = step.get("capability")
        out["intent"] = intent
        out["tool"] = {"id": step.get("capability"), "action": "read", "target": intent}
        out["result"] = _short(step.get("result"))
        if step.get("evidence"):
            out["evidence"] = step["evidence"][0] if isinstance(step["evidence"], list) else step["evidence"]
    elif kind == "reasoning":
        out["note"] = step.get("note")
    elif kind == "decision":
        # the engine records a gate decision as Step(result={"decision": "approve"}, note=actor).
        # the demo schema requires a `verdict` on decision steps ({approve|refine|deny|close|hold}) and
        # attributes the decision to the OPERATOR who authorized it (engine records the actor in `note`).
        res = step.get("result")
        verdict = ""
        if isinstance(res, dict):
            verdict = str(res.get("decision") or "")
        elif isinstance(res, str):
            verdict = res
        out["verdict"] = verdict or "approve"
        out["result"] = _short(res)
        who = step.get("note") or "operator"
        out["actor"] = {"id": who if isinstance(who, str) else "operator", "role": "operator"}
        out["headline"] = f"Operator {out['verdict']}"
    elif kind in ("suggestion",):
        out["result"] = _short(step.get("result") or step.get("note"))
    return out


def project_demo(subject: dict, values: dict, graph, *, status: str,
                 pending_gate: Optional[dict] = None, title: Optional[str] = None) -> dict:
    """Build the demo incident document from the engine's run state + graph."""
    records: list[dict] = values.get("phase_records", [])
    inc_id = subject["id"]
    cur_phase = records[-1].get("phase") if records else "assess"

    # which phase first touched each node/edge (best-effort: by the record that was open when added —
    # the engine doesn't tag nodes by phase, so we attribute to the current phase progression)
    nodes: list[dict] = []
    nodes.append({"id": inc_id, "label": inc_id, "kind": "incident", "health": "subject",
                  "phase": records[0].get("phase") if records else "assess",
                  "summary": _assess_symptom(records) or (title or inc_id),
                  "facts": []})
    seen = {inc_id}
    for nid in graph.node_ids():
        if nid in seen:
            continue
        seen.add(nid)
        try:
            n = graph.raw_node(nid)
        except Exception:
            continue
        nodes.append({"id": n.id, "label": n.name or n.id, "kind": _node_kind(n), "type": n.type,
                      "layer": n.layer, "health": _node_health(n), "phase": cur_phase,
                      "summary": n.summary or "", "labels": list(n.labels or []), "facts": _facts(n)})

    edges: list[dict] = []
    # link the incident root to the top-level systems so the graph is connected for the viewer
    try:
        raw_edges = list(graph._g.edges(data=True))   # noqa: SLF001 — same package, read-only projection
    except Exception:
        raw_edges = []
    for (u, v, d) in raw_edges:
        edges.append({"from": u, "to": v, "type": d.get("type", "depends_on"),
                      "phase": cur_phase, "basis": (d.get("props") or {}).get("basis", "")})
    # attach the root incident to any node that has no incoming edge (so it's reachable in the viewer)
    targets = {e["to"] for e in edges}
    for n in nodes[1:]:
        if n["id"] not in targets and n.get("layer") in (None, "app", "business"):
            edges.append({"from": inc_id, "to": n["id"], "type": "affects", "phase": "assess",
                          "basis": "in incident scope"})
            break

    phases: list[dict] = []
    for r in records:
        phases.append({
            "phase": r.get("phase"), "state": r.get("state"), "id": r.get("id"),
            "subject": subject, "goal": r.get("goal", ""), "summary": r.get("summary") or "",
            "opened_at": r.get("opened_at"), "closed_at": r.get("closed_at"),
            "steps": [_project_step(r, s) for s in r.get("steps", [])],
            "output": r.get("output"),
        })

    return {
        "incident": inc_id, "title": title or _assess_symptom(records) or inc_id,
        "subject": subject, "status": status, "current_phase": cur_phase,
        "pending_gate": pending_gate, "schema": "2.0",
        "graph": {"nodes": nodes, "edges": edges}, "phases": phases,
    }


class IncidentWriter:
    """Writes incidents/<INC>.json (the file the consoles poll). Atomic-ish: write tmp then replace."""

    def __init__(self, incidents_dir: str) -> None:
        self.dir = Path(incidents_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, inc: str, doc: dict) -> None:
        path = self.dir / f"{inc}.json"
        tmp = self.dir / f".{inc}.json.tmp"
        tmp.write_text(json.dumps(doc, indent=2, ensure_ascii=False))
        tmp.replace(path)
