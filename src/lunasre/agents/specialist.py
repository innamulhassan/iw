"""Shared specialist-worker agent — DBOps / NetOps / DeployOps all use this.

A specialist is a WORKER (not a supervisor): it runs a plain async tool-loop over
ONE MCP server (via a long-lived MCPLiveSession) and returns a findings text.
Everything that differs between specialists lives in `agents/configs/<id>.yaml`:
  - agent_id, llm.model, tools.use_servers (which MCP server)
  - system_prompt (the specialist's investigation protocol)
  - a2a (host/port/url + skills for the Agent Card)

So `dbops.py` / `netops.py` / `deployops.py` are 3-line entrypoints. The loop +
the 4-layer small-model defense live here, once.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from typing import Any

import structlog
from openai import AsyncOpenAI

from lunasre.agents.base import AgentConfig, load_agent_config, resolve_mcp_servers
from lunasre.agents.tool_loop import (
    rescue_tool_call_from_content,
    to_openai_tools,
    tool_param_index,
)
from lunasre.runtime.a2a_server import (
    A2AMessageRequest,
    A2AMessageResponse,
    build_a2a_app,
    build_agent_card,
    serve,
)
from lunasre.runtime.mcp_session import MCPLiveSession

logger = structlog.get_logger("specialist")


class SpecialistAgent:
    """A config-driven specialist worker."""

    def __init__(self, agent_id: str) -> None:
        self.config: AgentConfig = load_agent_config(agent_id)
        self.log = structlog.get_logger(agent_id)

    async def investigate(
        self, delegation_message: str, context: dict[str, Any] | None = None
    ) -> str:
        """Run one investigation over the specialist's MCP server; return findings.

        Long-lived MCPLiveSession (so stateful servers like mock_logs keep their
        session_id across calls). Uniform for stateless servers too.
        """
        servers = resolve_mcp_servers(self.config)
        if not servers:
            return f"ERROR — {self.config.agent_id} resolved zero MCP servers."
        server = servers[0]

        user_content = delegation_message
        if context:
            user_content += "\n\nContext (from IC):\n" + json.dumps(context, indent=2)

        async with MCPLiveSession(server, agent_id=self.config.agent_id) as live:
            openai_tools = to_openai_tools(live.tool_schemas)
            valid_names = {t["function"]["name"] for t in openai_tools}
            params_index = tool_param_index(openai_tools)
            tools_used: set[str] = set()
            client = AsyncOpenAI(base_url=self.config.llm.base_url, api_key=self.config.llm.api_key)
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": self.config.system_prompt},
                {"role": "user", "content": user_content},
            ]

            iterations = 0
            while iterations < self.config.runtime.max_tool_iterations:
                iterations += 1
                available = [t for t in openai_tools if t["function"]["name"] not in tools_used]
                tools_param = available if available else None
                self.log.info(
                    "specialist.iteration",
                    n=iterations,
                    available=[t["function"]["name"] for t in available],
                )
                resp = await client.chat.completions.create(
                    model=self.config.llm.model,
                    messages=messages,  # type: ignore[arg-type]
                    tools=tools_param,  # type: ignore[arg-type]
                    temperature=self.config.llm.temperature,
                )
                msg = resp.choices[0].message
                assistant_turn: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
                if msg.tool_calls:
                    assistant_turn["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                messages.append(assistant_turn)

                rescued: list[dict[str, Any]] = []
                if not msg.tool_calls:
                    rescue = rescue_tool_call_from_content(
                        msg.content or "", valid_names, params_index
                    )
                    if rescue is not None:
                        rescued = [rescue]
                        messages[-1] = {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": rescue["id"],
                                    "type": "function",
                                    "function": {
                                        "name": rescue["name"],
                                        "arguments": rescue["arguments_str"],
                                    },
                                }
                            ],
                        }

                if not msg.tool_calls and not rescued:
                    self.log.info("specialist.complete", iterations=iterations)
                    return msg.content or ""

                tool_call_iter: list[tuple[str, str, str]] = []
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        tool_call_iter.append(
                            (tc.id, tc.function.name, tc.function.arguments or "{}")
                        )
                for r in rescued:
                    tool_call_iter.append((r["id"], r["name"], r["arguments_str"]))

                for tc_id, tool_name, raw_args in tool_call_iter:
                    if tool_name in tools_used:
                        result_text = (
                            f"NOTE — you already called {tool_name}. Do not repeat it; "
                            "emit the FINDINGS report now."
                        )
                    else:
                        try:
                            args = json.loads(raw_args)
                        except json.JSONDecodeError as e:
                            result_text = f"ERROR — could not parse arguments: {e}"
                        else:
                            try:
                                result_text = await live.call(tool_name, args)
                                tools_used.add(tool_name)
                            except Exception as e:
                                result_text = f"ERROR — tool {tool_name!r} raised: {e!r}"
                    self.log.info(
                        "specialist.tool_call",
                        tool=tool_name,
                        result_preview=result_text[:140] if result_text else "",
                    )
                    messages.append({"role": "tool", "tool_call_id": tc_id, "content": result_text})

            # Max iterations — force a findings report.
            messages.append(
                {"role": "user", "content": "Emit the FINDINGS report now with NO more tool calls."}
            )
            forced = await client.chat.completions.create(
                model=self.config.llm.model,
                messages=messages,  # type: ignore[arg-type]
                tools=None,
                temperature=self.config.llm.temperature,
            )
            return forced.choices[0].message.content or "(no findings produced)"

    def build_app(self):
        """Build the FastAPI A2A app for this specialist."""
        a2a = self.config.a2a
        skills = [(s.id, s.description) for s in (a2a.skills if a2a else [])]
        card = build_agent_card(
            name=self.config.agent_id,
            description=self.config.description.strip(),
            url=a2a.url if a2a else "http://localhost:8000",
            skills=skills,
        )

        async def handler(req: A2AMessageRequest) -> A2AMessageResponse:
            findings = await self.investigate(req.content, req.context)
            return A2AMessageResponse(role="assistant", content=findings)

        return build_a2a_app(card, handler)


def specialist_main(agent_id: str) -> None:
    """Shared CLI entrypoint for every specialist worker."""
    parser = argparse.ArgumentParser(description=f"{agent_id} — A2A specialist server / debug.")
    parser.add_argument("--serve", action="store_true", help="Start the A2A uvicorn server")
    parser.add_argument(
        "--debug-investigate", action="store_true", help="Run one in-process investigation"
    )
    parser.add_argument("--message", default=None, help="(debug) the delegation message")
    parser.add_argument("--service", default="payments-api", help="(debug) affected service")
    parser.add_argument("--debug", action="store_true", help="DEBUG-level logs")
    args = parser.parse_args()

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if args.debug else logging.INFO
        ),
    )

    agent = SpecialistAgent(agent_id)

    if args.serve:
        app = agent.build_app()
        a2a = agent.config.a2a
        host = a2a.host if a2a else "127.0.0.1"
        port = a2a.port if a2a else 8000
        logger.info("specialist.serving", agent=agent_id, host=host, port=port)
        serve(app, host=host, port=port)
        return

    if args.debug_investigate:
        msg = args.message or (
            f"Investigate an alert on {args.service} around 2026-06-15T03:14:22Z. "
            "Gather evidence and report root cause."
        )
        findings = asyncio.run(agent.investigate(msg, {"service": args.service}))
        print(findings)
        return

    parser.print_help()
