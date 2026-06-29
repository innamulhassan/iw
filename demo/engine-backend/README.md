# Engine backend — incident-triage investigation engine

Python implementation of the engine described in `../../design/v2/` (master: `03-design.html`;
schemas: `04-data-model.html`; contract: `00-PRD.md`). Built phase-by-phase per
`../IMPLEMENTATION-PLAN.md` — each component design-validated and unit-tested before the next.

## Layout

```
src/engine/
  domain/        # P1 · Pydantic models — the data model (04-data-model)
  graph_runtime/ # P2 · networkx graph + tool surface + render-slice + fold (B9)
  capability/    # P3 · registry + intent resolver + govern() + adapters (Part C)
  runtime/       # P4 · LangGraph engine: loader, compile, phase loop, gate (Part B)
  session/       # P5 · live session: lock + channel (B8)
  api/           # P6 · FastAPI surface (Part F)
tests/           # pytest — unit + contract + (P8) one mocked end-to-end
```

## Setup & test

```bash
cd implementation/engine-backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Principles

Domain-neutral (one engine, many playbooks) · tool-agnostic (phases declare intents, never tool
names) · governed at the boundary (`govern()` allow/ask/deny, human-approved writes) · everything
behind a mockable interface so the whole system is testable with **no real credentials**. Real
MCP/A2A, Postgres, Mongo, and Redis are wired in only at P9 (after the user supplies them).
