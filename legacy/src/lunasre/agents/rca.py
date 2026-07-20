"""RCA agent — root-cause synthesis (Phase 2).

An A2A peer with NO MCP tools. IC escalates to RCA after a specialist returns:
RCA reads the gathered evidence (IC's findings + specialist findings) and produces
one root-cause synthesis. A pure-reasoning aggregator — the "long-context RCA"
node in ARCHITECTURE.md §1 step 9 (design target Gemini; on Ollama for now).

    uv run python -m lunasre.agents.rca --serve              # A2A server :8002
    uv run python -m lunasre.agents.rca --debug-synthesize
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import structlog
from openai import AsyncOpenAI

from lunasre.agents.base import load_agent_config
from lunasre.runtime.a2a_server import (
    A2AMessageRequest,
    A2AMessageResponse,
    build_a2a_app,
    build_agent_card,
    serve,
)

logger = structlog.get_logger("rca")


async def synthesize(evidence_message: str, context: dict | None = None) -> str:
    """One LLM call (no tools) producing a root-cause synthesis from the evidence."""
    config = load_agent_config("rca-agent")
    client = AsyncOpenAI(base_url=config.llm.base_url, api_key=config.llm.api_key)
    user_content = evidence_message
    if context:
        import json

        user_content += "\n\nIncident context:\n" + json.dumps(context, indent=2)
    resp = await client.chat.completions.create(
        model=config.llm.model,
        messages=[
            {"role": "system", "content": config.system_prompt},
            {"role": "user", "content": user_content},
        ],
        temperature=config.llm.temperature,
    )
    synthesis = resp.choices[0].message.content or ""
    logger.info("rca.synthesized", chars=len(synthesis))
    return synthesis


def build_rca_app():
    config = load_agent_config("rca-agent")
    a2a = config.a2a
    card = build_agent_card(
        name=config.agent_id,
        description=config.description.strip(),
        url=a2a.url if a2a else "http://localhost:8002",
        skills=[(s.id, s.description) for s in (a2a.skills if a2a else [])],
    )

    async def handler(req: A2AMessageRequest) -> A2AMessageResponse:
        synthesis = await synthesize(req.content, req.context)
        return A2AMessageResponse(role="assistant", content=synthesis)

    return build_a2a_app(card, handler)


def main() -> None:
    parser = argparse.ArgumentParser(description="RCA agent — A2A synthesis server / debug.")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--debug-synthesize", action="store_true")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if args.debug else logging.INFO
        ),
    )

    if args.serve:
        config = load_agent_config("rca-agent")
        app = build_rca_app()
        host = config.a2a.host if config.a2a else "127.0.0.1"
        port = config.a2a.port if config.a2a else 8002
        logger.info("rca.serving", host=host, port=port)
        serve(app, host=host, port=port)
        return

    if args.debug_synthesize:
        ev = (
            "GATHERED EVIDENCE:\n"
            "IC: alert 8472 db-failure on payments-api at 03:14:22Z.\n"
            "DBOps: connection pool exhausted (max=200) at 03:13:55Z; OOM kill 03:14:00Z; "
            "reaper thread blocked on lock held by killed process 03:14:25Z; replica lag 55s."
        )
        print(asyncio.run(synthesize(ev, {"alert_id": "8472"})))
        return

    parser.print_help()


if __name__ == "__main__":
    main()
