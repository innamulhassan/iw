"""Long-lived MCP client session — required for stateful-session servers (mock_logs).

L26.P / L27.P had `fetch_mcp_tools` / `call_mcp_tool` spawn + tear down the
MCP server subprocess on each call. That works for stateless tools like
mock_datadog where each call is independent, but breaks for mock_logs whose
`open_log_session` returns a session_id that subsequent grep/tail calls
expect to find in module-level state.

This module provides `MCPLiveSession` — an async context manager that opens
the MCP `stdio_client` + `ClientSession` once and keeps them alive across
many tool calls.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from lunasre.registries import MCPServerEntry
from lunasre.runtime.audit import audit
from lunasre.runtime.observability import span


class MCPLiveSession:
    """Long-lived MCP client over stdio.

    Use as:
        async with MCPLiveSession(server, agent_id="ic-agent") as ms:
            ms.tool_schemas        # list of {name, description, input_schema}
            await ms.call("tool_name", {"arg": "value"})

    `agent_id` attributes tool calls in the audit log + observability spans
    (L9 + L8). This is the single chokepoint every agent's tool calls flow
    through, so instrumenting it covers all of them.
    """

    def __init__(self, server: MCPServerEntry, agent_id: str = "unknown") -> None:
        if server.transport != "stdio":
            raise NotImplementedError(
                f"MCPLiveSession only supports stdio transport (got {server.transport!r})"
            )
        if not server.command:
            raise ValueError(f"server {server.name!r} has no command for stdio")
        self.server = server
        self.agent_id = agent_id
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self.tool_schemas: list[dict[str, Any]] = []

    async def __aenter__(self) -> MCPLiveSession:
        self._stack = AsyncExitStack()
        await self._stack.__aenter__()
        params = StdioServerParameters(
            command=self.server.command[0],
            args=list(self.server.command[1:]),
            env=None,
        )
        read, write = await self._stack.enter_async_context(stdio_client(params))
        self._session = await self._stack.enter_async_context(ClientSession(read, write))
        await self._session.initialize()
        tool_list = await self._session.list_tools()
        self.tool_schemas = [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema,
            }
            for t in tool_list.tools
        ]
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._stack is not None:
            await self._stack.__aexit__(*exc)
        self._stack = None
        self._session = None

    async def call(self, tool_name: str, args: dict[str, Any]) -> str:
        """Invoke `tool_name` on the live session; return first text content as str.

        Cross-cutting planes wrap this single chokepoint: an OTel span (L8) +
        an audit-log entry (L9) attributed to self.agent_id, for every tool call.
        """
        if self._session is None:
            raise RuntimeError("MCPLiveSession not entered — use `async with`")
        ok = True
        text = ""
        with span("mcp.tool_call", agent=self.agent_id, server=self.server.name, tool=tool_name):
            try:
                result = await self._session.call_tool(tool_name, args)
                if result.content:
                    first = result.content[0]
                    text = first.text if hasattr(first, "text") else str(first)
            except Exception:
                ok = False
                raise
            finally:
                audit().record(
                    agent_id=self.agent_id,
                    action="mcp.tool_call",
                    target=f"{self.server.name}.{tool_name}",
                    args=args,
                    result=text,
                    ok=ok,
                )
        return text
