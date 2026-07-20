"""Engine failure types — drive `error_handler` / retry / on-failure. E4.

A capability raises `TransientError` for something worth retrying (timeout, 503) and
`PermanentError` for something that won't get better (bad request, hard failure). The phase loop
retries transients per `defaults.retry`, and on a permanent failure follows `on_failure`
(`run-remaining` → finish independent steps, report `blocked`; otherwise escalate / fail).
"""
from __future__ import annotations


class TransientError(Exception):
    """Retryable — the engine retries per `defaults.retry` (max, backoff)."""


class PermanentError(Exception):
    """Not retryable — the engine routes to `error_handler` / `on_failure`."""
