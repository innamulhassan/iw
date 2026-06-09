"""Minimal A2A client — fetch a peer's Agent Card + POST a delegation message.

Pairs with `a2a_server.py`. Phase 4 swaps to the full a2a-sdk ClientFactory
behind the same `delegate_to_agent` interface (no IC code change).
"""

from __future__ import annotations

from typing import Any

import httpx

from lunasre.runtime.a2a_server import AgentCard
from lunasre.runtime.audit import audit
from lunasre.runtime.identity import mint_token
from lunasre.runtime.observability import span


async def fetch_agent_card(card_url: str, timeout: float = 5.0) -> AgentCard:
    """GET an Agent Card from its publication URL and parse it.

    Doubles as a reachability check — raises (httpx error) if the peer is down,
    which IC's delegate node catches to fall back to its own evidence.
    """
    with span("a2a.fetch_card", url=card_url):
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(card_url)
            resp.raise_for_status()
    # Card JSON is camelCase (spec); _CamelModel populates by alias.
    return AgentCard.model_validate(resp.json())


async def delegate_to_agent(
    base_url: str,
    content: str,
    context: dict[str, Any] | None = None,
    timeout: float = 180.0,
    caller_agent_id: str = "ic-agent",
) -> dict[str, Any]:
    """POST a delegation message to a peer agent's /a2a/message endpoint.

    Attaches the caller's workload-identity token (L12) as a Bearer header so the
    callee can verify WHO is calling; wraps the call in an OTel span (L8) + an
    audit entry (L9). Returns the raw response dict; the caller treats `content`
    as opaque — the L4 opacity property.
    """
    payload: dict[str, Any] = {"role": "user", "content": content}
    if context is not None:
        payload["context"] = context
    target = base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {mint_token(caller_agent_id)}"}
    ok = True
    try:
        with span("a2a.delegate", caller=caller_agent_id, target=target):
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{target}/a2a/message", json=payload, headers=headers)
                resp.raise_for_status()
                return resp.json()
    except Exception:
        ok = False
        raise
    finally:
        audit().record(
            agent_id=caller_agent_id,
            action="a2a.delegate",
            target=target,
            args=context or content[:80],
            ok=ok,
        )
