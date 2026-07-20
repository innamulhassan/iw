"""Remediation adapter — the WRITE-effect capability the human-gated REMEDIATE phase serves.

The read adapters (prometheus, appd, …) fold a tool's raw JSON into graph ops; a remediation
is the opposite — an ACTION (roll back a release, revert a change, scale a pool). It carries
`Effect.WRITE`, so the CapabilityLayer's gate (writes only under an approved gate) and the
interactive session's write-gate both key off it: the planner emits an `apply_remediation`
call in REMEDIATE, the session suspends and shows the operator the proposed action, and only an
`approve` decision lets the layer serve it (`allow_write=True`).

`normalize()` optionally records that the remediation was applied as an event on the target
(when the transport echoes an `applied` block), else folds to zero ops — the reversible action
itself lives outside the graph; the graph only witnesses its recorded effect. Kept out of
`default_adapters()` and wired only by the scenario registry, so the read-only golden path is
untouched (DESIGN-INPUT-v1.md §E.2: "`ocp__restart` **write**->gate")."""
from __future__ import annotations

from ...domain.enums import Binding, Effect, Source
from ...domain.operations import AddEvent, Operation


class RemediationAdapter:
    provider = "remediation"
    intents = frozenset({"apply_remediation"})
    effect = Effect.WRITE
    binding = Binding.A2A   # remediation delegation — the reserved write-side binding (§C)

    def normalize(self, raw: dict) -> list[Operation]:
        applied = raw.get("applied")
        if not applied:
            return []
        at = applied.get("at")
        entity = applied.get("entity")
        etype = applied.get("type")
        if not (at and entity and etype):
            return []
        return [AddEvent(entity=entity, type=etype, occurred_at=at, observed_at=at,
                         payload={"remediation": applied.get("action")}, source=Source.SERVICENOW)]
