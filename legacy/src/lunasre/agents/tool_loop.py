"""Shared tool-calling helpers + small-model defense — used by every agent.

These were born in L27.P inside ic.py; L28.P extracts them so IC (supervisor) and
DBOps (worker) — and every future specialist — share one copy.

The defense pattern (origin: L27.P, routed n-watch to the learning playbook):
small / local models (7-8B) don't always follow the OpenAI tool-call contract.
Two failure modes and their structural (not prompt-based) fixes:

  1. Looping on the same tool          -> TOOL ROTATION (caller drops used tools
                                          from the next iteration's tools=[] list).
  2. Emitting a tool-call as content   -> rescue_tool_call_from_content() parses it.

`to_openai_tools` + `rescue_tool_call_from_content` live here; tool rotation is
caller-side loop logic (it needs the per-iteration `tools_used` set).
"""

from __future__ import annotations

import json
from typing import Any


def to_openai_tools(tool_schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate MCPLiveSession / fetch_mcp_tools schemas -> OpenAI tools=[] shape.

    Input items: {"name", "description", "input_schema"} (JSON Schema object).
    Output items: {"type": "function", "function": {"name", "description", "parameters"}}.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["input_schema"],
            },
        }
        for t in tool_schemas
    ]


def tool_param_index(openai_tools: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Map tool name -> set of its parameter property names.

    Used by rescue shape 3 (bare-args inference): if a model emits just the
    arguments dict, its key set can identify which tool it meant.
    """
    index: dict[str, set[str]] = {}
    for t in openai_tools:
        fn = t.get("function", {})
        name = fn.get("name")
        props = (fn.get("parameters") or {}).get("properties") or {}
        if name:
            index[name] = set(props.keys())
    return index


def rescue_tool_call_from_content(
    content: str,
    valid_tool_names: set[str],
    tool_params: dict[str, set[str]] | None = None,
) -> dict[str, Any] | None:
    """Recover a tool call that a small model emitted as `content` text.

    Returns a normalized {"id", "name", "arguments_str"} dict if `content` parses
    as (or implies) a tool call for one of `valid_tool_names`; else None.

    Three shapes seen in practice with small / local models:
      1. OpenAI-nested: {"id", "type": "function", "function": {"name", "arguments"}}
      2. flat:          {"name": "tool_name", "arguments": {...}}
      3. bare args:     {"session_id": ..., "pattern": ...}  (no name at all)
         -> inferred by matching the key set against `tool_params` (exact match
            to exactly one tool). Requires `tool_params` to be passed.
    """
    if not content:
        return None
    stripped = content.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return None
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    # Shape 1 — OpenAI-nested.
    fn = obj.get("function")
    if isinstance(fn, dict) and isinstance(fn.get("name"), str):
        name = fn["name"]
        if name not in valid_tool_names:
            return None
        raw_args = fn.get("arguments", "{}")
        args_str = raw_args if isinstance(raw_args, str) else json.dumps(raw_args)
        return {"id": obj.get("id", "rescued-0"), "name": name, "arguments_str": args_str}

    # Shape 2 — flat.
    if isinstance(obj.get("name"), str) and obj["name"] in valid_tool_names:
        raw_args = obj.get("arguments", {})
        args_str = raw_args if isinstance(raw_args, str) else json.dumps(raw_args)
        return {"id": obj.get("id", "rescued-0"), "name": obj["name"], "arguments_str": args_str}

    # Shape 3 — bare args: infer the tool from the key set.
    if tool_params:
        keys = set(obj.keys())
        candidates = [
            name
            for name, params in tool_params.items()
            if name in valid_tool_names and keys and keys.issubset(params) and (keys & params)
        ]
        # Prefer an exact key-set match; else accept a unique subset match.
        exact = [name for name in candidates if tool_params[name] == keys]
        chosen = exact[0] if len(exact) == 1 else (candidates[0] if len(candidates) == 1 else None)
        if chosen is not None:
            return {"id": "rescued-0", "name": chosen, "arguments_str": json.dumps(obj)}

    return None
