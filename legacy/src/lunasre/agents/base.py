"""Shared base for LunaSRE agents.

This module holds the contract every agent shares — config loading, registry
resolution, MCP tool discovery and invocation. The IC agent (Chunk 2) is the
first user; Phase-2 specialists (DBOps / NetOps / DeployOps / RCA / PM Writer /
Grok Reviewer) reuse the same helpers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, Field

from lunasre.registries import (
    MCPServerEntry,
    load_gateway_registry,
    load_mcp_registry,
)

logger = structlog.get_logger(__name__)


# ─── Config models (one Pydantic model per top-level YAML block in agents/configs/<id>.yaml) ──


class LLMConfig(BaseModel):
    """Where + how the agent talks to a model.

    `base_url` is always the LiteLLM proxy. `model` is one of the `model_name`s
    declared in `infra/litellm_config.yaml`. Swapping vendors = changing `model`.
    """

    base_url: str
    api_key: str = "not-validated-locally"
    model: str
    temperature: float = 0.2


class RegistryConfig(BaseModel):
    """Paths (relative to project root) to the YAML registry seed files.

    `kind` selects the MCP registry BACKING: "file" (direct) or "gateway" (the
    MCP Gateway — adds per-agent tool scope + credential isolation). Swapping
    file→gateway changes the backing, not a line of agent code. `scopes` points
    at the gateway scope file when kind == "gateway".
    """

    mcp: str
    agent: str
    kind: str = "file"
    scopes: str | None = None


class ToolsConfig(BaseModel):
    """How the agent picks MCP tools — by explicit server name (Chunk 2) or by
    capability (Phase 2+; cleaner because IC needn't know which server hosts
    which capability)."""

    use_servers: list[str] = Field(default_factory=list)
    use_capabilities: list[str] = Field(default_factory=list)


class RuntimeConfig(BaseModel):
    """Per-agent runtime tunables. Phase 4 adds `checkpointer:` for durability."""

    graph: str
    max_tool_iterations: int = 4


class DelegationConfig(BaseModel):
    """Optional delegation map — `by_alert_type: { db-failure: dbops-agent }`.

    Phase 2: IC consults this to decide whether (and to whom) to delegate via A2A.
    """

    by_alert_type: dict[str, str] = Field(default_factory=dict)


class SkillSpec(BaseModel):
    """One Agent-Card skill (A2A L13 capability description)."""

    id: str
    description: str


class A2AConfig(BaseModel):
    """Optional A2A server binding — present on agents that run as A2A peers
    (DBOps / NetOps / DeployOps / RCA / PM Writer / Grok Reviewer). Absent on the
    IC supervisor, which is the A2A *client*, not a served peer (Phase 2)."""

    host: str = "127.0.0.1"
    port: int = 8003
    url: str = "http://localhost:8003"
    skills: list[SkillSpec] = Field(default_factory=list)


class AgentConfig(BaseModel):
    """The full agent contract loaded from agents/configs/<id>.yaml.

    ONE config model for every agent. Optional blocks (`delegation`, `a2a`,
    `system_prompt`) are present only for the agents that use them — IC carries
    `delegation`; the specialist workers carry `a2a` + `system_prompt`. This
    avoids per-agent config subclasses.
    """

    agent_id: str
    role: str
    description: str = ""
    system_prompt: str = ""
    llm: LLMConfig
    registries: RegistryConfig
    tools: ToolsConfig
    runtime: RuntimeConfig
    delegation: DelegationConfig = DelegationConfig()
    a2a: A2AConfig | None = None


# ─── Project-root marker detection (so paths in config can be relative) ────────────────────────


def find_project_root(start: Path) -> Path:
    """Walk up from `start` looking for the project root marker (pyproject.toml)."""
    for parent in [start, *start.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    raise RuntimeError(f"could not find pyproject.toml ancestor of {start}")


PROJECT_ROOT = find_project_root(Path(__file__).resolve())


# ─── Config loading ────────────────────────────────────────────────────────────────────────────


def _config_path_for(agent_id: str) -> Path:
    """Map an agent_id like 'ic-agent' or 'rca-agent' to its YAML file."""
    # Strip trailing "-agent" / "-writer" / "-reviewer" suffixes for the file stem.
    stem = agent_id
    for suffix in ("-agent",):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return PROJECT_ROOT / "src" / "lunasre" / "agents" / "configs" / f"{stem}.yaml"


def load_agent_config(agent_id: str) -> AgentConfig:
    """Load + validate an agent's YAML config.

    Raises `pydantic.ValidationError` if the YAML doesn't match the schema —
    catches drift between ARCHITECTURE.md §5 and what's on disk at load time.
    """
    path = _config_path_for(agent_id)
    if not path.exists():
        raise FileNotFoundError(
            f"no config for agent {agent_id!r} at {path} "
            f"(expected file: agents/configs/{path.stem}.yaml)"
        )
    data = yaml.safe_load(path.read_text())
    config = AgentConfig(**data)
    logger.debug(
        "agent.config.loaded",
        agent_id=agent_id,
        model=config.llm.model,
        servers=config.tools.use_servers,
        capabilities=config.tools.use_capabilities,
    )
    return config


# ─── Registry resolution ───────────────────────────────────────────────────────────────────────


def _load_mcp_registry_for(config: AgentConfig):
    """Load the MCP registry whose path is named in the agent's config.

    Honors `registries.kind`: "file" → direct file registry; "gateway" →
    GatewayMCPRegistry (per-agent scope). Both satisfy the same lookup interface
    (.get / .find_by_capability / .all), so resolve_mcp_servers is unchanged —
    the swap-without-agent-change proof.
    """
    path = PROJECT_ROOT / config.registries.mcp
    if config.registries.kind == "gateway":
        scopes_path = PROJECT_ROOT / config.registries.scopes if config.registries.scopes else None
        return load_gateway_registry(path, config.agent_id, scopes_path)
    return load_mcp_registry(path)


def resolve_mcp_servers(config: AgentConfig) -> list[MCPServerEntry]:
    """Resolve the MCP servers this agent is configured to talk to.

    Resolution order: explicit `use_servers` first (preserve order), then
    `use_capabilities` (each capability resolves to one or more servers via
    the registry's capability index). Duplicates dropped.
    """
    registry = _load_mcp_registry_for(config)
    resolved: list[MCPServerEntry] = []
    seen: set[str] = set()

    for name in config.tools.use_servers:
        if name in seen:
            continue
        resolved.append(registry.get(name))
        seen.add(name)

    for capability in config.tools.use_capabilities:
        for entry in registry.find_by_capability(capability):
            if entry.name in seen:
                continue
            resolved.append(entry)
            seen.add(entry.name)

    logger.debug(
        "agent.servers.resolved",
        agent_id=config.agent_id,
        servers=[s.name for s in resolved],
    )
    return resolved


# ─── MCP tool fetching + invocation (the L3 boundary) ──────────────────────────────────────────


def _stdio_params_for(server: MCPServerEntry) -> StdioServerParameters:
    """Build StdioServerParameters from a registry entry's command vector."""
    if server.transport != "stdio":
        raise NotImplementedError(
            f"only stdio transport supported in Chunk 2 "
            f"(got {server.transport!r} for {server.name})"
        )
    if not server.command:
        raise ValueError(f"server {server.name!r} has no command for stdio transport")
    return StdioServerParameters(
        command=server.command[0],
        args=list(server.command[1:]),
        env=None,
    )


async def fetch_mcp_tools(server: MCPServerEntry) -> list[dict[str, Any]]:
    """Spawn the MCP server as a subprocess (stdio), call `tools/list`, return tool schemas.

    The returned dicts use the OpenAI function-calling shape:
        {"name": str, "description": str, "input_schema": JSON-Schema-object}

    Translation to the full OpenAI `tools=[{type:function, function:{...}}]` array
    happens at the call site (agent code), so test fixtures can introspect the raw
    schemas without coupling to the OpenAI parameter shape.
    """
    params = _stdio_params_for(server)
    fetched: list[dict[str, Any]] = []
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tool_list = await session.list_tools()
            for tool in tool_list.tools:
                fetched.append(
                    {
                        "name": tool.name,
                        "description": tool.description or "",
                        "input_schema": tool.inputSchema,
                    }
                )
    logger.debug(
        "mcp.tools.fetched",
        server=server.name,
        tools=[t["name"] for t in fetched],
    )
    return fetched


async def call_mcp_tool(server: MCPServerEntry, tool_name: str, args: dict[str, Any]) -> str:
    """Spawn the MCP server and invoke a single tool by name.

    Returns the tool's text content. MCP tools can return multiple content blocks
    (text / image / resource ref); we currently take the first text block and
    string-coerce anything else. Sufficient for Chunk 2's mock_datadog (text only).
    """
    params = _stdio_params_for(server)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, args)
            if not result.content:
                return ""
            first = result.content[0]
            if hasattr(first, "text"):
                return first.text
            return str(first)
