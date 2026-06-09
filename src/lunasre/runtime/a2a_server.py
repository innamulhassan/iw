"""Minimal A2A server for LunaSRE Phase 2.

What this is:
- A FastAPI app that publishes a **spec-shaped Agent Card** at
  /.well-known/agent.json and exposes a thin POST /a2a/message endpoint that
  invokes an injected async handler.

Why a hand-rolled card model instead of `a2a.types.AgentCard`:
- The installed a2a-sdk ships AgentCard as a **protobuf** message whose internal
  shape (interfaces / transport bindings) differs from the public Agent Card JSON
  and is awkward to construct + serialize for a simple card endpoint. The A2A
  Agent Card is fundamentally a JSON document; this module emits **spec-compliant
  camelCase JSON** (protocolVersion, defaultInputModes, capabilities, skills, ...)
  via a small Pydantic model with field aliases.
- The full a2a-sdk (AgentExecutor + RequestHandler + 3 transport bindings +
  task lifecycle) is the Phase-4 upgrade. L28.P demonstrates the A2A *seam*
  (card publication + cross-agent JSON delegation), not the full lifecycle.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import uvicorn
from fastapi import FastAPI, Request
from pydantic import BaseModel, ConfigDict, Field


def _camel(s: str) -> str:
    head, *tail = s.split("_")
    return head + "".join(w.capitalize() for w in tail)


class _CamelModel(BaseModel):
    """Base: snake_case in Python, camelCase in the served JSON (A2A spec keys)."""

    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True)


class AgentSkillCard(_CamelModel):
    id: str
    name: str
    description: str
    tags: list[str] = Field(default_factory=list)


class AgentCapabilitiesCard(_CamelModel):
    streaming: bool = False
    push_notifications: bool = False


class AgentCard(_CamelModel):
    """Spec-shaped A2A Agent Card (the public JSON document)."""

    name: str
    description: str
    url: str
    version: str = "0.1.0"
    protocol_version: str = "0.3.0"
    default_input_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    default_output_modes: list[str] = Field(default_factory=lambda: ["text/plain"])
    capabilities: AgentCapabilitiesCard = Field(default_factory=AgentCapabilitiesCard)
    skills: list[AgentSkillCard] = Field(default_factory=list)


class A2AMessageRequest(BaseModel):
    """Minimal A2A delegation request — text in. A full a2a-sdk message is richer
    (parts / role / metadata); L28.P needs text-in / text-out."""

    role: str = "user"
    content: str
    context: dict | None = None


class A2AMessageResponse(BaseModel):
    role: str = "assistant"
    content: str
    artifacts: list[dict] | None = None


def build_agent_card(
    name: str,
    description: str,
    url: str,
    skills: list[tuple[str, str]],
    version: str = "0.1.0",
) -> AgentCard:
    """Build a spec-shaped AgentCard. `skills` = list of (id, description) tuples."""
    return AgentCard(
        name=name,
        description=description,
        url=url,
        version=version,
        capabilities=AgentCapabilitiesCard(streaming=False, push_notifications=False),
        skills=[
            AgentSkillCard(
                id=sid,
                name=sid,
                description=sdesc,
                tags=["sre", "incident-investigation"],
            )
            for sid, sdesc in skills
        ],
    )


def build_a2a_app(
    card: AgentCard,
    handler: Callable[[A2AMessageRequest], Awaitable[A2AMessageResponse]],
) -> FastAPI:
    """FastAPI app: publish `card` at /.well-known/agent.json + route POST /a2a/message."""
    app = FastAPI(title=card.name, version=card.version)

    @app.get("/.well-known/agent.json")
    async def get_agent_card() -> dict:
        # by_alias → spec camelCase keys (protocolVersion, defaultInputModes, ...).
        return card.model_dump(by_alias=True)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "agent": card.name}

    @app.post("/a2a/message", response_model=A2AMessageResponse)
    async def handle_message(req: A2AMessageRequest, request: Request) -> A2AMessageResponse:
        # Identity (L12): verify the caller's workload-identity token + attribute.
        # Governance (L9): audit the inbound delegation. Permissive unless
        # LUNASRE_ENFORCE_IDENTITY=1 (then caller_from_authorization raises 403-ish).
        from lunasre.runtime.audit import audit
        from lunasre.runtime.identity import caller_from_authorization

        caller, verified = caller_from_authorization(request.headers.get("authorization"))
        audit().record(
            agent_id=card.name,
            action="a2a.receive",
            target=f"from:{caller}",
            args={"verified": verified},
        )
        return await handler(req)

    return app


def serve(app: FastAPI, host: str, port: int) -> None:
    """Run the A2A app under uvicorn (blocking)."""
    uvicorn.run(app, host=host, port=port, log_level="warning")
