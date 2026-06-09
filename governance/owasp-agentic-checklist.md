# OWASP Agentic Top-10 (2026) — LunaSRE posture

> Governance (L9). The OWASP Top 10 for Agentic Applications is the security
> checklist for agent systems. This file documents LunaSRE's posture per item:
> what the toy does today, and what production hardening adds. Pairs with the
> append-only `audit_log` (`runtime/audit.py`) and the per-agent identity
> (`runtime/identity.py`).

| # | OWASP Agentic risk | LunaSRE posture (toy) | Production hardening |
|---|---|---|---|
| ASI-01 | **Agent authorization / control hijacking** | Each agent is a workload identity (signed token, `runtime/identity.py`); A2A calls carry + verify it. MCP Gateway scope (`registries/gateway.py`) enforces per-agent least-privilege tool access. | Real OIDC (Authelia/Okta) + gateway holding tool creds; strict mode (`LUNASRE_ENFORCE_IDENTITY=1`). |
| ASI-02 | **Tool misuse / excessive agency** | Tools are mock + read-only; the only side-effecting step (`execute_remediation`) is **HITL-gated** (human approves before execution). | Real tools behind the gateway with scoped, audited, rate-limited access. |
| ASI-03 | **Privilege compromise** | Per-agent scope (gateway) — IC can't reach DB-only tools; RCA has zero tool scope. | Short-lived per-agent tokens; deny-by-default scopes. |
| ASI-04 | **Resource / cost exhaustion** | `max_tool_iterations` caps per-agent tool loops; tool rotation forces convergence. | LLM-gateway spend caps (LiteLLM), per-agent quotas. |
| ASI-05 | **Cascading / multi-agent failures** | A2A delegation is opacity-preserving; specialist failure → IC falls back to its own evidence (graceful, audited). | Circuit breakers + timeouts per peer; bulkheads. |
| ASI-06 | **Memory / context poisoning** | Memory recall is advisory ("similar past incidents") and clearly framed; the model still gathers fresh evidence. | Signed memory entries; provenance on recalled context. |
| ASI-07 | **Insecure output / injection** | Findings are treated as data, not instructions; the human gate sits before any execution. | Output validation; structured tool-result schemas. |
| ASI-08 | **Identity spoofing / repudiation** | A2A tokens are signed + verified; every action is recorded in the append-only audit log attributed to the verified caller. | JWS-signed Agent Cards; tamper-evident audit store / SIEM. |
| ASI-09 | **Observability gaps** | OpenTelemetry spans (`runtime/observability.py`) wrap every tool call + A2A hop + run; one trace per incident. | OTLP → Phoenix/Datadog; alerting on anomalous traces. |
| ASI-10 | **Governance / accountability** | This checklist + the audit log + the HITL approval record give a who-did-what-and-who-approved-it trail. | ISO 42001 / NIST AI RMF mapping; periodic audit review. |

**Three-layer model (OWASP):** Usage layer (the human gate + UI) · Agent layer (MCP scope + A2A identity + audit) · Model layer (LiteLLM gateway — model selection + spend, production). LunaSRE exercises all three at toy grade.

**Audit evidence:** every tool call and A2A delegation is in `.lunasre/audit.db` (`runtime/audit.py`); query with `AuditLog().recent()`.
