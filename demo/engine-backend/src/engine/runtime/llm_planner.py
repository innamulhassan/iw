"""The real, LLM-backed planner (the "P9" piece) — drives a phase's agent loop with a model
instead of a script. xAI Grok via the OpenAI-compatible API; swap provider/model with env vars.

Implements the Planner Protocol (see planner.py): plan / next_action / update_output / wants_operator.
Single-use demo wiring — one model, deterministic temperature, JSON-mode for the structured calls.

Env:
  XAI_API_KEY   (required to actually call the model)
  XAI_BASE_URL  (default https://api.x.ai/v1 — set to http://localhost:4000 for a LiteLLM proxy)
  XAI_MODEL     (default grok-3 — set to your available Grok model, or a proxy role name)
"""
from __future__ import annotations

import json
import os
from typing import Callable, Optional

from engine.domain import PhaseSpec, Playbook
from engine.domain.outputs import OUTPUT_TYPES

_SYS = (
    "You are the investigation engine of an incident-triage workbench. You run ONE phase of a "
    "governed investigation at a time (assess -> root-cause -> remediation -> verify-close). You may "
    "only use the intents you are given; you never invent tool names. You ground every conclusion in "
    "the evidence gathered this run. When asked for structured output you return STRICT JSON that "
    "validates against the provided JSON Schema, using ONLY values seen in the evidence."
)

# Persona for the free-form operator chat channel (distinct from _SYS, the engine's structured
# investigation persona). Used by LLMPlanner.chat() — the conversational side channel that runs
# ALONGSIDE an investigation, not part of the regulator-grade Step audit trail.
_CHAT_SYS = (
    "You are the Investigation Workbench's assistant. The operator is a senior SRE / engineer-leader. "
    "Answer questions about the current incident, the system architecture, the investigation's progress, "
    "or SRE practice — concisely and directly, no preamble. You can see the current incident context "
    "(phase, symptom, the systems in scope) appended below. If you don't know something from the "
    "available evidence, say so rather than guessing at specifics."
)


def _step_brief(rec) -> list[dict]:
    """Compact the steps gathered so far so the model can reason over them (not the raw objects)."""
    out = []
    for s in rec.steps:
        kind = s.kind.value if hasattr(s.kind, "value") else s.kind
        if kind in ("tool_call", "reasoning") and (s.capability or s.result):
            out.append({"kind": kind, "capability": s.capability,
                        "input": s.input, "result": s.result})
    return out


class LLMPlanner:
    def __init__(self, playbook: Playbook, *, model: Optional[str] = None,
                 base_url: Optional[str] = None, api_key: Optional[str] = None,
                 max_actions: int = 6,
                 live_intents: Optional[Callable[[], set[str]]] = None) -> None:
        from openai import OpenAI  # imported here so the package imports without openai for tests
        self.model = model or os.environ.get("XAI_MODEL", "grok-3")
        self.client = OpenAI(
            base_url=base_url or os.environ.get("XAI_BASE_URL", "https://api.x.ai/v1"),
            api_key=api_key or os.environ.get("XAI_API_KEY", "missing-key"),
        )
        self.max_actions = max_actions
        # which intents are backed by a registered+ready LIVE browser capability right now — the planner
        # always exercises these in-phase, so the operator's real tools are demonstrably used.
        self._live_intents = live_intents or (lambda: set())
        # phase id -> output schema name (e.g. "assess" -> "AssessResult"), from the playbook
        self._out = {p.id: p.output for p in playbook.phases}
        # minimum evidence (tool calls) before a phase may conclude — stops the model concluding on
        # one call, so it actually investigates (and the graph + outputs get real depth)
        self._floor = {"assess": 3, "root-cause": 3, "remediation": 1, "verify-close": 2}

    def _called(self, rec) -> list[str]:
        out = []
        for s in rec.steps:
            kind = s.kind.value if hasattr(s.kind, "value") else s.kind
            if kind == "tool_call" and isinstance(s.input, dict) and s.input.get("intent"):
                out.append(s.input["intent"])
        return out

    # ── transport ────────────────────────────────────────────────────────
    def _chat(self, user: str, *, json_mode: bool = False) -> str:
        kwargs: dict = {"model": self.model, "temperature": 0,
                        "messages": [{"role": "system", "content": _SYS},
                                     {"role": "user", "content": user}]}
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        r = self.client.chat.completions.create(**kwargs)
        return r.choices[0].message.content or ""

    def chat(self, messages: list[dict], *, system: str = _CHAT_SYS) -> str:
        """Free-form operator chat — the conversational side channel (NOT part of the engine's
        structured investigation loop). `messages` is the full conversation history as OpenAI-style
        {role, content} dicts; this is the only place conversation history is carried (the engine's
        _chat rebuilds a one-shot each call). Reuses the same xAI Grok client; uses a warmer
        temperature than the engine's deterministic temp=0 so the reply reads naturally."""
        msgs = [{"role": "system", "content": system}] + [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]
        r = self.client.chat.completions.create(model=self.model, temperature=0.3, messages=msgs)
        return r.choices[0].message.content or ""

    # ── Planner Protocol ─────────────────────────────────────────────────
    def plan(self, state: dict, phase: PhaseSpec) -> str:
        subj = state.get("subject", {})
        try:
            txt = self._chat(
                f"Incident: {json.dumps(subj)}. Phase '{phase.id}' — goal: {phase.goal}. "
                f"Intents available this phase: {list(phase.needs)}. "
                "State your plan for this phase in ONE concise sentence."
            )
            return txt.strip().splitlines()[0][:200] or f"Plan {phase.id}"
        except Exception as exc:  # offline / no key — degrade, don't crash the run
            return f"Plan {phase.id} (model unavailable: {type(exc).__name__})"

    def next_action(self, state: dict, rec, allowed: list[str]) -> Optional[tuple[str, dict]]:
        called = self._called(rec)
        if len(called) >= self.max_actions:
            return None
        # ticket-first: always read the incident source before anything else — standard practice ("what
        # does the ticket say?"), and it's the capability the operator backs with the live tool (browser).
        if "incident-source" in allowed and "incident-source" not in called:
            return "incident-source", {"intent": "incident-source"}
        # map-next: always pull topology before concluding — standard SRE practice, and it seeds the
        # incident graph the operator reads.
        if "topology" in allowed and "topology" not in called:
            return "topology", {"intent": "topology"}
        # live-tools-next: exercise every registered+ready browser capability valid for this phase, so
        # the operator's real UI tools are demonstrably read by the agent (not just the demo data).
        for live in sorted(self._live_intents()):
            if live in allowed and live not in called:
                return live, {"intent": live}
        floor = self._floor.get(rec.phase, 2)
        uncalled = [i for i in allowed if i not in called]
        nxt = None
        try:
            data = json.loads(self._chat(json.dumps({
                "phase": rec.phase, "goal": rec.goal,
                "already_gathered": called, "still_available": uncalled,
                "evidence_so_far": _step_brief(rec),
                "instruction": ("Investigate THOROUGHLY — follow the evidence (trace the slow path, "
                                "check the dependency BELOW the symptom, confirm recent changes). Choose "
                                "the NEXT single intent from still_available, or 'done' ONLY once you "
                                'have solid evidence for a confident output. Reply JSON: '
                                '{"next":"<intent>|done","why":"..."}'),
            }), json_mode=True))
            nxt = data.get("next")
        except Exception:
            nxt = None
        if nxt and nxt != "done" and nxt in allowed:
            return nxt, {"intent": nxt}
        # the model wants to stop (or returned junk): honor it only past the evidence floor; otherwise
        # keep gathering an as-yet-uncalled intent.
        if len(called) < floor and uncalled:
            return uncalled[0], {"intent": uncalled[0]}
        return None

    def update_output(self, state: dict, rec) -> dict:
        # don't even attempt the output until the evidence floor is met — keeps `sufficient()` False so
        # the loop keeps investigating instead of concluding on the first call.
        if len(self._called(rec)) < self._floor.get(rec.phase, 2):
            return rec.output or {}
        oname = self._out.get(rec.phase, "")
        otype = OUTPUT_TYPES.get(oname)
        if otype is None:
            return rec.output or {}
        base = {"phase": rec.phase, "goal": rec.goal, "evidence": _step_brief(rec),
                "subject": state.get("subject", {}), "output_json_schema": otype.model_json_schema()}
        err: Optional[str] = None
        for _ in range(2):   # one retry, feeding the validation error back so the model can fix it
            try:
                instr = ("Produce the phase output as JSON that STRICTLY validates against "
                         "output_json_schema, grounded ONLY in evidence. Include EVERY required field.")
                if err:
                    instr += f" Your previous JSON was INVALID: {err}. Fix it; include all required fields."
                data = json.loads(self._chat(json.dumps({**base, "instruction": instr}), json_mode=True))
                otype.model_validate(data)   # only a schema-valid output trips the loop's stop test
                return data
            except Exception as exc:
                err = str(exc)[:200]
        return self._minimal(oname, rec, state)   # safety net — a phase never hard-fails on a flaky reply

    def _minimal(self, oname: str, rec, state: dict) -> dict:
        """A deterministic, schema-valid minimal output from the evidence — used only if the model
        can't produce a valid one, so a phase completes instead of crashing the run."""
        if oname == "AssessResult":
            sym = ""
            for s in rec.steps:
                r = s.result if isinstance(s.result, dict) else {}
                if r.get("short_description"):
                    sym = r["short_description"]
                    break
            return {"incident_type": "performance",
                    "symptom": sym or str(state.get("subject", {}).get("id", "incident")),
                    "impact_assessment": {}}
        if oname == "RootCauseResult":
            return {"candidates": [{"cause": "primary suspect from gathered evidence",
                                    "confidence": {"value": 0.6, "basis": "engine evidence"}, "rank": 1}]}
        if oname == "VerifyResult":
            return {"recovered": True}
        return {}   # RemediationResult validates empty (all fields default)

    def wants_operator(self, state: dict, rec) -> bool:
        return False   # single-use demo: the HITL is the write-gate, not mid-phase questions
