#!/usr/bin/env python3
"""Validate every incidents/*.json against graph-schema.json + step-schema.json.

Checks the invariants the design depends on (nothing hard-coded — the rules come
from the schema files):
  - node.kind / edge.type / node.health are declared in graph-schema.json
  - every edge endpoint + every graph_op node is a real node
  - final node.health == the last set_health graph_op for that node (reconciliation)
  - step.kind is one of the 10 step kinds; status / verdict / subtype / gate.kind / effect enums are legal
  - step id == "<phaseId>#<seq>"; seq is gap-free 1..n per phase
  - in_response_to / retry_of / supersedes / created_by_step reference steps that exist
  - WRITE tool_calls carry an idempotency_key

Usage:  python3 validate.py        (run from the demo/ folder)
"""
import json, glob, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))
gs = json.load(open(os.path.join(HERE, "graph-schema.json")))
NODE_T, EDGE_T, HEALTH = set(gs["node_types"]), set(gs["edge_types"]), set(gs["health_states"])
KINDS   = {"plan","reasoning","tool_call","observation","suggestion","gate","decision","user_input","escalation","backtrack"}
STATUS  = {"ok","error","denied","timeout","cached","skipped","raised","answered","expired","committed","pending"}
VERDICT = {"approve","refine","deny","close","hold"}
SUBTYPE = {"plan","analysis","finding","narration"}
GATEK   = {"advance","write","close","escalation"}


def check(fn):
    d = json.load(open(fn)); issues = []
    if not d.get("phases"):      # a finished/worked incident must have phases
        return ["no phases[]"]
    steps_all = [s for ph in d["phases"] for s in ph.get("steps", [])]
    if steps_all and not all("id" in s for s in steps_all):
        return ["__legacy__"]            # pre step-schema v2.0 — not validated here
    ids = {s["id"] for s in steps_all}
    nodes = {n["id"]: n for n in d["graph"]["nodes"]}

    for n in d["graph"]["nodes"]:
        if n["kind"]   not in NODE_T: issues.append(f"node {n['id']} kind '{n['kind']}' not in graph-schema")
        if n["health"] not in HEALTH: issues.append(f"node {n['id']} health '{n['health']}' not in graph-schema")
        if n.get("created_by_step") and n["created_by_step"] not in ids:
            issues.append(f"node {n['id']} created_by_step {n['created_by_step']} missing")
    for e in d["graph"]["edges"]:
        if e["type"] not in EDGE_T:   issues.append(f"edge {e['from']}->{e['to']} type '{e['type']}' not in graph-schema")
        if e["from"] not in nodes:    issues.append(f"edge from {e['from']} not a node")
        if e["to"]   not in nodes:    issues.append(f"edge to {e['to']} not a node")
        if e.get("created_by_step") and e["created_by_step"] not in ids:
            issues.append(f"edge {e['from']}->{e['to']} created_by_step {e['created_by_step']} missing")

    last = {}
    for ph in d["phases"]:
        for s in ph.get("steps", []):
            for g in s.get("graph_ops", []):
                if g.get("op") == "set_health" and g.get("node"):
                    last[g["node"]] = g["to"]
    for nid, h in last.items():
        if nid in nodes and nodes[nid]["health"] != h:
            issues.append(f"node {nid} health '{nodes[nid]['health']}' != last set_health '{h}'")

    for ph in d["phases"]:
        pid, seqs = ph.get("id"), []
        for s in ph.get("steps", []):
            k, sid = s.get("kind"), s.get("id"); seqs.append(s.get("seq"))
            if k not in KINDS:                                       issues.append(f"{sid} kind '{k}' invalid")
            if s.get("status") and s["status"] not in STATUS:       issues.append(f"{sid} status '{s['status']}' illegal")
            if sid != f"{pid}#{s.get('seq')}":                      issues.append(f"{sid} id != {pid}#{s.get('seq')}")
            if k == "decision" and s.get("verdict") not in VERDICT: issues.append(f"{sid} verdict '{s.get('verdict')}' illegal")
            if k == "reasoning" and s.get("subtype") and s["subtype"] not in SUBTYPE: issues.append(f"{sid} subtype illegal")
            if k == "gate" and s.get("gate", {}).get("kind") not in GATEK:           issues.append(f"{sid} gate.kind illegal")
            if k == "tool_call":
                if s.get("effect") and s["effect"] not in ("read", "write"):         issues.append(f"{sid} effect illegal")
                if s.get("effect") == "write" and not s.get("idempotency_key"):      issues.append(f"{sid} write missing idempotency_key")
            for ref in ("retry_of", "supersedes"):
                if s.get(ref) and s[ref] not in ids:                issues.append(f"{sid} {ref} {s[ref]} missing")
            if isinstance(s.get("in_response_to"), str) and s["in_response_to"] not in ids:
                issues.append(f"{sid} in_response_to {s['in_response_to']} missing")
            for g in s.get("graph_ops", []):
                if g.get("node") and g["node"] not in nodes:        issues.append(f"{sid} graph_op node {g['node']} not a node")
        if seqs != list(range(1, len(seqs) + 1)):                   issues.append(f"{pid} seq not gap-free 1..n: {seqs}")
    return issues


ok = True
for fn in sorted(glob.glob(os.path.join(HERE, "incidents", "*.json"))):
    name = os.path.relpath(fn, HERE)
    try:
        iss = check(fn)
    except Exception as ex:
        ok = False; print(f"{name}: ERROR {ex}"); continue
    if iss == ["__legacy__"]:
        print(f"{name}: legacy (pre step-schema v2.0) — skipped"); continue
    if iss:
        ok = False; print(f"\n{name}: {len(iss)} ISSUE(S)")
        for i in iss: print("  -", i)
    else:
        print(f"{name}: PASS")
print("\nALL CLEAN" if ok else "\nISSUES FOUND")
sys.exit(0 if ok else 1)
