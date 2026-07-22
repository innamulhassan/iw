"""Investigation Workbench engine — governed, typed, phased incident investigation.

Layering (see docs/DESIGN.md):
  domain/      DATA LAYER  — pure types + the closed registry (zero I/O)
  graph/ hypothesis/ journal/  APP LAYER  — the three projections of the PhaseResult stream
  runtime/     APP LAYER  — the phase orchestrator (thin deterministic loop)
  capability/  APP LAYER  — governed, mockable capability adapters
"""

__version__ = "0.1.0"
