# Interactive Workbench — UI/UX Spec (owner-directed)

> The definitive requirements for the interactive investigation workbench. Build to THIS,
> verify completeness against every line. Current `:5183` is the read-only viewer — it is NOT this.

## 1. Start + navigation (most important)
- **Start screen:** a **domain selector** + an **incident-number input** the user types to START an investigation. (Domain e.g. `app-incident`; id e.g. `INC-4821`.)
- **Incident list / history:** the user sees **other incidents**, including **CLOSED** ones; can **open** any to see its chat, graph, ledger, journal — everything. (Persist investigations; list them.)
- **ALL use cases runnable end-to-end:** the selector offers a runnable incident for **every** use case / layer — code-regression · bad-deployment · network · database · firewall · no-change (and more as added). The user enters/picks any incident id and the engine runs the **full** investigation for it. Every layer must work, not just one.

## 2. Chat pane (the interaction — the thing that's missing today)
- A **visible chat pane** where the user converses with the agent (like this session).
- Per **phase**, the agent's turn shows its **reasoning** + **the tool calls it made** (capability calls) as **collapsible / expandable cards** — collapsed by default, expand to see the call + result. Exactly like how tool-calls / background tasks show collapsibly in an agent UI — NOT a flat wall of text.
- **Approval in the loop:** approve / refine / deny the write-gate directly in the chat (human-in-the-loop).

## 3. Journal
- Keeps **everything** per phase (reasoning + every tool call + observations). **Collapsible/expandable**, grouped by phase.

## 4. Graph (interactive)
- **Zoom + pan** (move around freely).
- Every node has a **clear, visible number badge** = its **creation order** (which node was created 1st, 2nd, next…), so you can read the investigation's progression on the graph.
- **Node expansion (ENGINE-driven, not the human):** the **orchestrator / planner** decides which node to expand next to investigate further (the natural investigation) — the graph grows as the agent digs, and each newly-created node gets the next number badge so the human *watches* the progression. The human does **not** drive expansion. *(Human-initiated manual "expand this node" is a **later** feature — not now.)*
- **Click a node → detail panel:** its **static properties** + its **facts / events**. (Different phases may surface different detail on the same node.)

## 5. Phase focus (current scope — owner is focusing here)
*(P7 update: the engine shipped the 5-phase algebra — `engine/src/iw_engine/playbooks/incident.yaml` is the source of truth. TRIAGE folded into the act-entry decision; HYPOTHESIZE folded into the ONE investigate loop; REMEDIATE became ACT, the one write-gated phase. INVESTIGATE may repeat — the stepper collapses repeats onto its single step with an ×N iteration badge.)*
- **Active now:** FRAME · INVESTIGATE (root-cause loop). These get full attention + must be right.
- **Gray out until reached:** ACT · VERIFY · (CLOSE) — greyed in the stepper until the run reaches them (the write-gate lives in ACT).

## 6. Quality bar
- Step back, understand fully with depth, review each requirement, **do it right**. **Always verify + check for completeness** against this spec before saying done.

---

### Engine implications (what the UI needs from the backend)
- Node **`created_by` seq** already exists on every Node → surface it as the badge number (order).
- **Node-expansion** investigation — the **planner/orchestrator** picks the next node to expand (the frontier work from the depth plan); the human does NOT drive it (manual expand deferred).
- **Interactive session** backend (start with domain+id, stream phase turns + tool calls, gate approval) — the session/SSE work.
- **Multi-domain** load (domain selector) — the `domains/<domain>/` loader.
- **Incident persistence + list** (open closed ones) — persist each session's journal; list them.
- Focus the run on FRAME→INVESTIGATE; ACT/VERIFY greyed until reached (P7 phase names).
