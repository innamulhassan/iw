"""mock_logs — second MCP server (Phase 2). Tools: open_log_session, grep, tail, close_log_session.

This is the **L3 stateful-sessions depth demo**: unlike mock_datadog's one-shot
tools, mock_logs holds a session lifecycle. Open once with (window, service);
follow-up grep/tail queries reuse that opened context cheaply.
"""
