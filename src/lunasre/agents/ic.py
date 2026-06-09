"""Incident Commander (IC) — Phase 2 supervisor.

IC is a multi-node LangGraph supervisor:

    START -> investigate -> [route] -> delegate -> summarize -> END
                                   \\-> summarize -> END

- investigate: IC gathers its OWN evidence via MCP tools (mock_datadog:
  drill_into_alert + tail_logs), discovered at runtime from the MCP registry.
  It parses the alert's `type` + `service` from the drill result.
- route_after_investigate (deterministic edge): if the alert type is in IC's
  delegation map AND the mapped specialist resolves in the agent registry,
  go to `delegate`; else `summarize`.
- delegate: resolve the specialist's Agent Card (L13 discovery), POST the alert
  to its /a2a/message endpoint (L4 A2A), append its findings to the conversation.
- summarize: final LLM call (tools=None) producing the structured report from
  ALL evidence (IC's own tool results + any specialist findings).

This is the real supervisor pattern: deterministic graph edges, LLM-driven nodes.

Run:
    uv run python -m lunasre.agents.ic --alert-id 8472   # db-failure -> delegates to DBOps
    uv run python -m lunasre.agents.ic --alert-id 8473   # network-partition -> no delegate yet
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
from typing import Any, TypedDict

import structlog
from openai import AsyncOpenAI

from lunasre.agents.base import (
    PROJECT_ROOT,
    AgentConfig,
    load_agent_config,
    resolve_mcp_servers,
)
from lunasre.agents.tool_loop import (
    rescue_tool_call_from_content,
    to_openai_tools,
    tool_param_index,
)
from lunasre.registries import AgentRegistry, MCPServerEntry, load_agent_registry
from lunasre.runtime.a2a_client import delegate_to_agent, fetch_agent_card
from lunasre.runtime.graph_factory import compile_supervisor_graph
from lunasre.runtime.mcp_session import MCPLiveSession
from lunasre.runtime.memory import (
    IncidentMemory,
    MemoryStore,
    SqliteMemoryStore,
    default_db_path,
)

logger = structlog.get_logger("ic")


INVESTIGATE_PROMPT = (
    "You are the Incident Commander (IC) for an SRE incident-investigation system.\n"
    "\n"
    "PHASE: EVIDENCE GATHERING. Do NOT write the final report yet.\n"
    "\n"
    "You have two tools:\n"
    "  - drill_into_alert(alert_id) -> alert metadata"
    " (type, severity, service, fired_at, message)\n"
    "  - tail_logs(service, n=20) -> recent log lines for a service\n"
    "\n"
    "Protocol (in order):\n"
    "  STEP 1: Call drill_into_alert ONCE with the given alert_id. Note the service + type.\n"
    "  STEP 2: Call tail_logs ONCE for that service.\n"
    "  STEP 3: Briefly (1-2 sentences) state your preliminary read."
    " STOP — do not call more tools.\n"
    "\n"
    "Rules: never repeat a tool with the same args; after STEP 2 stop calling tools."
)

SUMMARIZE_INSTRUCTION = (
    "Now write the FINAL incident report. Use ALL evidence in this conversation — your own "
    "tool results AND any SPECIALIST FINDINGS provided. Output exactly these fields:\n"
    "  WHAT: one-line incident description\n"
    "  WHEN: timestamp from the alert\n"
    "  SERVICE: affected service\n"
    "  ROOT CAUSE: cite specific log lines / alert fields as evidence; if a specialist was\n"
    "    consulted, incorporate and attribute their finding\n"
    "  REMEDIATION: concrete recommended action\n"
    "  VERIFY NEXT: one or two concrete checks\n"
    "Do not call any tools. Cite only real data from the conversation."
)


class ICState(TypedDict):
    """Supervisor state. Phase-3 HITL fields (proposed_remediation/approved/executed)
    are persisted by the checkpointer across the interrupt→resume boundary."""

    alert_id: str
    messages: list[dict[str, Any]]
    alert_type: str | None
    service: str | None
    alert_payload: dict[str, Any] | None
    iterations: int
    tool_calls_executed: int
    a2a_delegations: list[dict[str, Any]]
    rca_synthesis: str | None
    summary: str | None
    # Phase 3 HITL
    proposed_remediation: str | None
    approved: bool | None
    executed: bool


class ICAgent:
    """Holds IC's config + resolved MCP servers + agent registry, and provides the
    three graph nodes + the routing function."""

    def __init__(
        self,
        config: AgentConfig,
        servers: list[MCPServerEntry],
        agent_registry: AgentRegistry,
        memory: MemoryStore | None = None,
    ) -> None:
        self.config = config
        self.servers = servers
        self.agent_registry = agent_registry
        self.memory = memory
        self.client = AsyncOpenAI(base_url=config.llm.base_url, api_key=config.llm.api_key)

    # ── Node 1: investigate ────────────────────────────────────────────────────────────────────

    async def investigate_node(self, state: ICState) -> dict[str, Any]:
        """Gather IC's own evidence via MCP tools; parse alert_type + service.

        Opens a long-lived MCP session per server (single subprocess for the whole
        loop) and applies the 3-layer small-model defense (tool rotation +
        same-call refusal + content rescue).
        """
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": INVESTIGATE_PROMPT},
            {"role": "user", "content": f"Investigate alert {state['alert_id']}."},
        ]
        iterations = 0
        tool_calls_executed = 0
        alert_type: str | None = None
        service: str | None = None
        alert_payload: dict[str, Any] | None = None

        async with contextlib.AsyncExitStack() as stack:
            # Open live sessions for all resolved servers; merge their tools.
            openai_tools: list[dict[str, Any]] = []
            tool_to_session: dict[str, MCPLiveSession] = {}
            for server in self.servers:
                live = await stack.enter_async_context(
                    MCPLiveSession(server, agent_id=self.config.agent_id)
                )
                for schema in live.tool_schemas:
                    openai_tools.append(to_openai_tools([schema])[0])
                    tool_to_session[schema["name"]] = live
            valid_names = set(tool_to_session.keys())
            params_index = tool_param_index(openai_tools)
            logger.info("ic.tools_discovered", tools=sorted(valid_names))

            tools_used: set[str] = set()
            while iterations < self.config.runtime.max_tool_iterations:
                iterations += 1
                available = [t for t in openai_tools if t["function"]["name"] not in tools_used]
                tools_param = available if available else None
                logger.info(
                    "ic.investigate.iteration",
                    n=iterations,
                    available=[t["function"]["name"] for t in available],
                )
                resp = await self.client.chat.completions.create(
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
                    # Evidence gathering done — model gave a preliminary read.
                    break

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
                            "give your preliminary read and stop."
                        )
                    elif tool_name not in tool_to_session:
                        result_text = f"ERROR — tool {tool_name!r} not available."
                    else:
                        try:
                            args = json.loads(raw_args)
                        except json.JSONDecodeError as e:
                            args = {}
                            result_text = f"ERROR — bad tool arguments JSON: {e}"
                        else:
                            try:
                                result_text = await tool_to_session[tool_name].call(tool_name, args)
                                tools_used.add(tool_name)
                            except Exception as e:
                                result_text = f"ERROR — tool {tool_name!r} raised: {e!r}"

                    # Parse alert metadata from the drill result.
                    if tool_name == "drill_into_alert" and result_text.strip().startswith("{"):
                        with contextlib.suppress(json.JSONDecodeError):
                            payload = json.loads(result_text)
                            if isinstance(payload, dict) and "error" not in payload:
                                alert_payload = payload
                                alert_type = payload.get("type")
                                service = payload.get("service")

                    tool_calls_executed += 1
                    logger.info(
                        "ic.investigate.tool_call",
                        tool=tool_name,
                        result_preview=result_text[:120] if result_text else "",
                    )
                    messages.append({"role": "tool", "tool_call_id": tc_id, "content": result_text})

        # MEMORY RECALL — now that we know the alert type/service, check whether
        # we've seen a similar incident before, and inject it for downstream nodes.
        if self.memory is not None and alert_type is not None:
            recalled = self.memory.recall_similar(alert_type, service, k=3)
            if recalled:
                lines = [
                    f"- [{m.created_at}] alert {m.alert_id} ({m.alert_type} on {m.service}): "
                    f"{m.root_cause}"
                    for m in recalled
                ]
                logger.info("ic.memory.recall", count=len(recalled))
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "MEMORY — similar past incidents (consider whether this is a "
                            "recurrence):\n" + "\n".join(lines)
                        ),
                    }
                )

        logger.info("ic.investigate.done", alert_type=alert_type, service=service)
        return {
            "messages": messages,
            "alert_type": alert_type,
            "service": service,
            "alert_payload": alert_payload,
            "iterations": iterations,
            "tool_calls_executed": tool_calls_executed,
        }

    # ── Conditional edge: route_after_investigate ───────────────────────────────────────────────

    def route_after_investigate(self, state: ICState) -> str:
        """Deterministic supervisor routing: delegate to a specialist or summarize."""
        alert_type = state.get("alert_type")
        deleg = self.config.delegation.by_alert_type
        if alert_type and alert_type in deleg:
            target = deleg[alert_type]
            try:
                self.agent_registry.get(target)
                logger.info("ic.route", decision="delegate", alert_type=alert_type, to=target)
                return "delegate"
            except KeyError:
                logger.warning(
                    "ic.route", decision="summarize", reason=f"{target} not in agent registry"
                )
                return "summarize"
        logger.info("ic.route", decision="summarize", alert_type=alert_type)
        return "summarize"

    # ── Node 2: delegate ────────────────────────────────────────────────────────────────────────

    async def delegate_node(self, state: ICState) -> dict[str, Any]:
        """Resolve the specialist's Agent Card, A2A-delegate, append findings."""
        alert_type = state["alert_type"]
        target_id = self.config.delegation.by_alert_type[alert_type]  # type: ignore[index]
        entry = self.agent_registry.get(target_id)
        card_url = entry.card_url
        base_url = card_url.rsplit("/.well-known/", 1)[0]
        messages = list(state["messages"])
        delegations = list(state["a2a_delegations"])

        alert_desc = (
            json.dumps(state["alert_payload"], indent=2)
            if state.get("alert_payload")
            else f"alert {state['alert_id']} ({alert_type}) on {state.get('service')}"
        )
        delegation_msg = (
            f"You are the {alert_type} specialist. Investigate this alert and report "
            f"evidence + root cause.\n\nAlert:\n{alert_desc}"
        )
        context = {
            "alert_id": state["alert_id"],
            "service": state.get("service"),
            "alert_type": alert_type,
        }

        try:
            card = await fetch_agent_card(card_url)  # L13 discovery + reachability check
            logger.info(
                "ic.a2a.card_resolved",
                agent=card.name,
                skills=[s.id for s in card.skills],
            )
            response = await delegate_to_agent(
                base_url, delegation_msg, context, caller_agent_id=self.config.agent_id
            )
            findings = response.get("content", "")
            delegations.append(
                {"agent": target_id, "card_name": card.name, "status": "ok", "findings": findings}
            )
            logger.info("ic.a2a.delegation_ok", agent=target_id, findings_chars=len(findings))
            messages.append(
                {
                    "role": "user",
                    "content": f"SPECIALIST FINDINGS (from {target_id}):\n{findings}",
                }
            )
        except Exception as e:
            logger.warning("ic.a2a.delegation_failed", agent=target_id, error=repr(e))
            delegations.append({"agent": target_id, "status": "failed", "error": repr(e)})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"NOTE: specialist {target_id} was unreachable ({e!r}). "
                        "Proceed using only your own evidence."
                    ),
                }
            )
        return {"messages": messages, "a2a_delegations": delegations}

    # ── Node 3: summarize ───────────────────────────────────────────────────────────────────────

    async def summarize_node(self, state: ICState) -> dict[str, Any]:
        """Final LLM call (tools=None) — compose the report from all evidence."""
        messages = list(state["messages"])
        messages.append({"role": "user", "content": SUMMARIZE_INSTRUCTION})
        resp = await self.client.chat.completions.create(
            model=self.config.llm.model,
            messages=messages,  # type: ignore[arg-type]
            tools=None,
            temperature=self.config.llm.temperature,
        )
        summary = resp.choices[0].message.content or ""
        messages.append({"role": "assistant", "content": summary})
        remediation = _extract_section(summary, "REMEDIATION") or summary
        logger.info("ic.summarize.done", summary_chars=len(summary))
        return {"summary": summary, "messages": messages, "proposed_remediation": remediation}

    # ── Node 4: execute_remediation (HITL-gated; runs only after human approval) ─────────────────

    async def execute_remediation_node(self, state: ICState) -> dict[str, Any]:
        """Side-effecting step, gated by interrupt_before. The graph PAUSES before
        this node; a human sets `approved`; on resume this runs.

        Simulated execution (the toy doesn't touch real infra). approved → execute;
        rejected → skip. Either way the run completes."""
        messages = list(state["messages"])
        if state.get("approved"):
            logger.info("ic.execute.approved", alert_id=state["alert_id"])
            messages.append(
                {
                    "role": "assistant",
                    "content": (
                        "REMEDIATION EXECUTED (simulated): "
                        + (state.get("proposed_remediation") or "")[:300]
                    ),
                }
            )
            return {"executed": True, "messages": messages}
        logger.info("ic.execute.rejected", alert_id=state["alert_id"])
        messages.append(
            {"role": "assistant", "content": "REMEDIATION REJECTED by human — not executed."}
        )
        return {"executed": False, "messages": messages}

    # ── Node 2.5: rca (escalation; runs after delegate on the delegate path) ────────────────────

    async def rca_node(self, state: ICState) -> dict[str, Any]:
        """Escalate the gathered evidence to the RCA agent for root-cause synthesis.

        RCA is an A2A peer with no tools — it reasons over IC's evidence + the
        specialist's findings. Runs only when a specialist actually returned
        findings; otherwise it's a no-op. Graceful fallback if RCA is unreachable.
        """
        findings = [
            d.get("findings", "")
            for d in state.get("a2a_delegations", [])
            if d.get("status") == "ok" and d.get("findings")
        ]
        if not findings:
            logger.info("ic.rca.skipped", reason="no specialist findings to synthesize")
            return {}

        try:
            entry = self.agent_registry.get("rca-agent")
        except KeyError:
            logger.warning("ic.rca.skipped", reason="rca-agent not in registry")
            return {}

        card_url = entry.card_url
        base_url = card_url.rsplit("/.well-known/", 1)[0]
        messages = list(state["messages"])
        alert_desc = (
            json.dumps(state["alert_payload"], indent=2)
            if state.get("alert_payload")
            else f"alert {state['alert_id']} ({state.get('alert_type')})"
        )
        evidence = (
            "GATHERED EVIDENCE:\n\n"
            f"Alert:\n{alert_desc}\n\n"
            "Specialist findings:\n" + "\n\n".join(findings)
        )
        context = {"alert_id": state["alert_id"], "alert_type": state.get("alert_type")}

        try:
            card = await fetch_agent_card(card_url)
            logger.info("ic.a2a.rca_card_resolved", agent=card.name)
            response = await delegate_to_agent(
                base_url, evidence, context, caller_agent_id=self.config.agent_id
            )
            synthesis = response.get("content", "")
            logger.info("ic.a2a.rca_ok", chars=len(synthesis))
            messages.append(
                {"role": "user", "content": f"RCA SYNTHESIS (from rca-agent):\n{synthesis}"}
            )
            return {"messages": messages, "rca_synthesis": synthesis}
        except Exception as e:
            logger.warning("ic.a2a.rca_failed", error=repr(e))
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"NOTE: RCA agent unreachable ({e!r}). Summarize from the "
                        "specialist findings already gathered."
                    ),
                }
            )
            return {"messages": messages}

    def build(self, hitl: bool = False, checkpointer: Any | None = None):
        """Compile the supervisor graph.

        hitl=False (default): investigate→route→delegate→rca→summarize→END.
        hitl=True: adds an execute_remediation node gated by interrupt_before, so
        the graph pauses for human approval after summarize (requires a checkpointer).
        """
        return compile_supervisor_graph(
            ICState,
            self.investigate_node,
            self.route_after_investigate,
            self.delegate_node,
            self.summarize_node,
            rca_fn=self.rca_node,
            execute_fn=self.execute_remediation_node if hitl else None,
            interrupt_before=["execute_remediation"] if hitl else None,
            checkpointer=checkpointer,
        )


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


def _extract_section(text: str, header: str) -> str | None:
    """Pull a `HEADER: ...` section out of a structured report (best-effort).

    Returns the text from the header to the next ALL-CAPS header, or None.
    """
    import re

    m = re.search(rf"{re.escape(header)}\s*:?\s*(.+?)(?:\n[A-Z][A-Z ]{{2,}}:|\Z)", text, re.S)
    return m.group(1).strip() if m else None


def _make_ic_agent(use_memory: bool) -> ICAgent:
    """Construct an ICAgent (config + servers + registry + optional memory)."""
    config = load_agent_config("ic-agent")
    servers = resolve_mcp_servers(config)
    if not servers:
        raise RuntimeError(f"IC resolved zero MCP servers — check {config.registries.mcp}")
    agent_registry = load_agent_registry(PROJECT_ROOT / config.registries.agent)
    memory: MemoryStore | None = SqliteMemoryStore() if use_memory else None
    return ICAgent(config, servers, agent_registry, memory=memory)


def _initial_state(alert_id: str) -> ICState:
    return {
        "alert_id": alert_id,
        "messages": [],
        "alert_type": None,
        "service": None,
        "alert_payload": None,
        "iterations": 0,
        "tool_calls_executed": 0,
        "a2a_delegations": [],
        "rca_synthesis": None,
        "summary": None,
        "proposed_remediation": None,
        "approved": None,
        "executed": False,
    }


def _store_incident(memory: MemoryStore | None, alert_id: str, state: dict[str, Any]) -> None:
    if memory is None or not state.get("alert_type"):
        return
    root_cause = state.get("rca_synthesis") or "(no RCA synthesis; see summary)"
    memory.store_incident(
        IncidentMemory(
            alert_id=alert_id,
            alert_type=state.get("alert_type"),
            service=state.get("service"),
            root_cause=root_cause[:600],
            summary=(state.get("summary") or "")[:1200],
            created_at=_now_iso(),
        )
    )
    logger.info("ic.memory.stored", alert_id=alert_id)


def _hitl_ckpt_path() -> str:
    return str(default_db_path().parent / "hitl_checkpoints.db")


async def run(
    alert_id: str,
    *,
    use_memory: bool = True,
    durable: bool = False,
    thread_id: str | None = None,
) -> ICState:
    """Compose IC + run one investigation through the supervisor graph.

    - use_memory: recall similar past incidents before delegating + store this one after.
    - durable: compile with an AsyncSqlite checkpointer (state persisted per thread_id;
      the crash/resume payoff lands with Phase-3 HITL, but the mechanism is wired here).
    """
    agent = _make_ic_agent(use_memory)
    initial_state = _initial_state(alert_id)

    if durable:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        ckpt_path = str(default_db_path().parent / "checkpoints.db")
        async with AsyncSqliteSaver.from_conn_string(ckpt_path) as saver:
            graph = agent.build(checkpointer=saver)
            cfg = {"configurable": {"thread_id": thread_id or alert_id}}
            final_state = await graph.ainvoke(initial_state, config=cfg)
    else:
        graph = agent.build()
        final_state = await graph.ainvoke(initial_state)

    _store_incident(agent.memory, alert_id, final_state)
    return final_state  # type: ignore[return-value]


# ── HITL (Phase 3): run-to-interrupt, then resume after human approval ──────────────────────────


async def run_hitl(
    alert_id: str, *, thread_id: str | None = None, use_memory: bool = True
) -> tuple[str, dict[str, Any]]:
    """Run IC until it PAUSES at the human-approval gate (interrupt_before execute).

    Returns (thread_id, paused_state). The state carries the report + the
    proposed_remediation awaiting approval. State is persisted in a file
    checkpointer so a SEPARATE process/request can resume it via resume_hitl().
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    agent = _make_ic_agent(use_memory)
    tid = thread_id or f"incident-{alert_id}"
    async with AsyncSqliteSaver.from_conn_string(_hitl_ckpt_path()) as saver:
        graph = agent.build(hitl=True, checkpointer=saver)
        cfg = {"configurable": {"thread_id": tid}}
        await graph.ainvoke(
            _initial_state(alert_id), config=cfg
        )  # stops before execute_remediation
        snap = await graph.aget_state(cfg)
    logger.info("ic.hitl.paused", thread_id=tid, next=snap.next)
    return tid, dict(snap.values)


async def resume_hitl(
    alert_id: str, thread_id: str, approved: bool, *, use_memory: bool = True
) -> dict[str, Any]:
    """Resume a paused HITL run from its checkpoint with the human's decision."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    agent = _make_ic_agent(use_memory)
    async with AsyncSqliteSaver.from_conn_string(_hitl_ckpt_path()) as saver:
        graph = agent.build(hitl=True, checkpointer=saver)
        cfg = {"configurable": {"thread_id": thread_id}}
        await graph.aupdate_state(cfg, {"approved": approved})
        final_state = await graph.ainvoke(None, config=cfg)  # resumes into execute_remediation
    logger.info("ic.hitl.resumed", thread_id=thread_id, approved=approved)
    _store_incident(agent.memory, alert_id, final_state)
    return dict(final_state)


async def stream_hitl(alert_id: str, *, use_memory: bool = True):
    """Async generator of {event, data} dicts for the AG-UI SSE layer.

    Streams one event per graph node as it completes (investigate / delegate /
    rca / summarize) — live progress — then a final `awaiting_approval` event
    carrying the report + thread_id, at which point the graph is paused at the
    human-approval gate. The web layer maps these to Server-Sent Events; the
    browser renders them and POSTs the approval, which resume_hitl() picks up.
    """
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    agent = _make_ic_agent(use_memory)
    tid = f"incident-{alert_id}"
    async with AsyncSqliteSaver.from_conn_string(_hitl_ckpt_path()) as saver:
        graph = agent.build(hitl=True, checkpointer=saver)
        cfg = {"configurable": {"thread_id": tid}}
        yield {"event": "run_started", "data": {"alert_id": alert_id, "thread_id": tid}}
        async for chunk in graph.astream(
            _initial_state(alert_id), config=cfg, stream_mode="updates"
        ):
            for node, update in chunk.items():
                data: dict[str, Any] = {"node": node}
                update = update or {}
                if node == "investigate":
                    data["alert_type"] = update.get("alert_type")
                    data["service"] = update.get("service")
                elif node == "delegate":
                    data["delegated_to"] = [
                        d.get("agent") for d in (update.get("a2a_delegations") or [])
                    ]
                elif node == "rca":
                    data["rca"] = bool(update.get("rca_synthesis"))
                elif node == "summarize":
                    data["summary"] = update.get("summary")
                yield {"event": "node", "data": data}
        snap = await graph.aget_state(cfg)
        if snap.next and "execute_remediation" in snap.next:
            yield {
                "event": "awaiting_approval",
                "data": {
                    "thread_id": tid,
                    "summary": snap.values.get("summary"),
                    "proposed_remediation": snap.values.get("proposed_remediation"),
                    "delegations": [
                        d.get("agent")
                        for d in (snap.values.get("a2a_delegations") or [])
                        if d.get("status") == "ok"
                    ],
                    "rca": bool(snap.values.get("rca_synthesis")),
                },
            }
        else:
            yield {"event": "done", "data": {"summary": snap.values.get("summary")}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the LunaSRE Incident Commander supervisor.")
    parser.add_argument("--alert-id", required=True, help="Alert ID (8472 / 8473 / 8474).")
    parser.add_argument(
        "--no-memory", action="store_true", help="Disable incident memory recall/store."
    )
    parser.add_argument(
        "--durable", action="store_true", help="Compile with a SQLite checkpointer (durable graph)."
    )
    parser.add_argument("--debug", action="store_true", help="DEBUG-level logs.")
    args = parser.parse_args()

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if args.debug else logging.INFO
        ),
    )

    final = asyncio.run(run(args.alert_id, use_memory=not args.no_memory, durable=args.durable))

    print()
    print("=" * 72)
    print(f"  IC incident report — alert {final['alert_id']}")
    print("=" * 72)
    print(final["summary"] or "(empty)")
    print()
    delegations = final.get("a2a_delegations") or []
    if delegations:
        print("  A2A delegations:")
        for d in delegations:
            status = d.get("status")
            if status == "ok":
                print(f"    - {d['agent']} ({d.get('card_name')}): OK")
            else:
                print(f"    - {d['agent']}: {status} ({d.get('error', '')})")
    else:
        print("  A2A delegations: none (handled by IC alone)")
    print(f"  RCA synthesis: {'yes' if final.get('rca_synthesis') else 'no'}")
    print(
        f"  investigate iterations: {final['iterations']}   "
        f"tool calls: {final['tool_calls_executed']}"
    )
    print("=" * 72)


if __name__ == "__main__":
    main()
