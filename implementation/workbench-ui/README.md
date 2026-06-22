# Workbench UI — the operator console

React (Vite + TypeScript) implementation of the console in `../../design/v2/` (F1; mockups
`diagrams/ui-console*.png`). Three panes — **Incidents** (left) · **Triage** chat + inline gate
(center) · **Phases & Steps ⇄ Graph** (right) — over a **focused investigation graph** that conveys
hundreds of nodes (cause path + impacted in full, a minimap for density, the healthy rest collapsed
to a count).

Runs against a **mock API client** (`src/model.ts`) serving INC-4821 fixtures, so the console works
with **no backend**. The real client (P9) hits the FastAPI surface (`../engine-backend`) behind the
same `ApiClient` interface.

## Develop & test

```bash
cd implementation/workbench-ui
npm install
npm run dev        # local dev server
npm test           # vitest component tests
npm run build      # typecheck + production build
```

## Layout

```
src/
  model.ts                 # TS shapes + INC-4821 fixtures + MockApiClient (ApiClient)
  App.tsx                  # wires the MockApiClient into the Workbench
  components/
    Workbench.tsx          # the 3-pane shell
    IncidentsPane.tsx      # subject + operator-linked related/similar (no auto-merge)
    TriagePane.tsx         # chat + the inline approval gate
    RightPane.tsx          # Phases & Steps ⇄ Graph tabs
    GraphView.tsx          # the focused graph (cause path · impacted · minimap · collapsed)
  Workbench.test.tsx       # component tests (vitest + Testing Library)
```
