"""The per-session run-owner lock — a lease + heartbeat. B8.2.

Advancing a session takes this lock; the holder is the transient run-owner and drives the agent
loop one step at a time, so no second run starts for the incident. A slow tool keeps the lease by
heartbeating; a dead owner lets the lease expire → another server steals it and resumes from the
checkpoint. The real impl is a Postgres advisory lock on the subject id; this in-memory lease is the
mockable equivalent. The clock is injectable so lease expiry is deterministic in tests.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable


@runtime_checkable
class RunLock(Protocol):
    def acquire(self, session_id: str, owner: str, ttl: float = 30.0) -> Optional[str]: ...
    def heartbeat(self, session_id: str, token: str, ttl: float = 30.0) -> bool: ...
    def release(self, session_id: str, token: str) -> bool: ...
    def holder(self, session_id: str) -> Optional[str]: ...


@dataclass
class _Lease:
    owner: str
    token: str
    expires_at: float


class InMemoryRunLock:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._leases: dict[str, _Lease] = {}
        self._counter = 0

    def _live(self, session_id: str) -> Optional[_Lease]:
        lease = self._leases.get(session_id)
        if lease is None or lease.expires_at <= self._clock():
            return None                                  # free, or expired (crashed owner)
        return lease

    def acquire(self, session_id: str, owner: str, ttl: float = 30.0) -> Optional[str]:
        if self._live(session_id) is not None:
            return None                                  # held by a live owner
        self._counter += 1
        token = f"lease-{self._counter}"
        self._leases[session_id] = _Lease(owner, token, self._clock() + ttl)
        return token

    def heartbeat(self, session_id: str, token: str, ttl: float = 30.0) -> bool:
        lease = self._leases.get(session_id)
        if lease is None or lease.token != token or lease.expires_at <= self._clock():
            return False
        lease.expires_at = self._clock() + ttl
        return True

    def release(self, session_id: str, token: str) -> bool:
        lease = self._leases.get(session_id)
        if lease and lease.token == token:
            del self._leases[session_id]
            return True
        return False

    def holder(self, session_id: str) -> Optional[str]:
        lease = self._live(session_id)
        return lease.owner if lease else None
