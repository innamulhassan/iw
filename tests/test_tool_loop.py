"""Tests for the shared tool-loop helpers + the 3-shape small-model rescue parser."""

from __future__ import annotations

from lunasre.agents.tool_loop import (
    rescue_tool_call_from_content,
    to_openai_tools,
    tool_param_index,
)

_SCHEMAS = [
    {
        "name": "grep",
        "description": "search logs",
        "input_schema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}, "pattern": {"type": "string"}},
            "required": ["session_id", "pattern"],
        },
    },
    {
        "name": "tail",
        "description": "tail logs",
        "input_schema": {
            "type": "object",
            "properties": {"session_id": {"type": "string"}, "n": {"type": "integer"}},
            "required": ["session_id"],
        },
    },
]
_TOOLS = to_openai_tools(_SCHEMAS)
_VALID = {"grep", "tail"}
_PARAMS = tool_param_index(_TOOLS)


def test_to_openai_tools_shape():
    assert _TOOLS[0]["type"] == "function"
    assert _TOOLS[0]["function"]["name"] == "grep"
    assert "parameters" in _TOOLS[0]["function"]


def test_param_index():
    assert _PARAMS["grep"] == {"session_id", "pattern"}
    assert _PARAMS["tail"] == {"session_id", "n"}


def test_rescue_shape1_openai_nested():
    content = '{"id": "x", "type": "function", "function": {"name": "grep", "arguments": "{}"}}'
    r = rescue_tool_call_from_content(content, _VALID, _PARAMS)
    assert r is not None and r["name"] == "grep"


def test_rescue_shape2_flat():
    content = '{"name": "tail", "arguments": {"session_id": "s1", "n": 5}}'
    r = rescue_tool_call_from_content(content, _VALID, _PARAMS)
    assert r is not None and r["name"] == "tail"


def test_rescue_shape3_bare_args_infers_grep():
    """The failure mode from L28.P standalone DBOps: model emits bare args."""
    content = '{"session_id": "log-payments-api-abc", "pattern": "connection"}'
    r = rescue_tool_call_from_content(content, _VALID, _PARAMS)
    assert r is not None
    assert r["name"] == "grep"  # {session_id, pattern} uniquely matches grep


def test_rescue_shape3_needs_params_index():
    """Without the params index, bare args cannot be inferred."""
    content = '{"session_id": "s1", "pattern": "x"}'
    assert rescue_tool_call_from_content(content, _VALID, None) is None


def test_rescue_rejects_non_tool_json():
    assert rescue_tool_call_from_content('{"hello": "world"}', _VALID, _PARAMS) is None


def test_rescue_rejects_plain_text():
    assert rescue_tool_call_from_content("WHAT: an incident report", _VALID, _PARAMS) is None


def test_rescue_rejects_unknown_tool_name():
    content = '{"name": "delete_everything", "arguments": {}}'
    assert rescue_tool_call_from_content(content, _VALID, _PARAMS) is None
