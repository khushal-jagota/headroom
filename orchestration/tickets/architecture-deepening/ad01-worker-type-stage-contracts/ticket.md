# AD01 — Worker type and stored Stage contracts

## Objective

Make the persisted and public Ticket contract use the resolved domain language:

- `worker_type` is required when a Ticket is created and stored immutably.
- `stage` is the Ticket's directly stored lifecycle position.
- `ticket_status` remains the distinct control status.
- No old wire key, CLI spelling, route, event kind, or compatibility alias remains.

This ticket changes vocabulary and persistence only. It must preserve every lifecycle, worker-selection,
scope, proposal, Review, board, sprint, Chat, and dispatch behavior. AD02 owns the internal Worker-type
module redesign and removal of parallel lifecycle authority.

## Binding decisions

- `CONTEXT.md`: Worker type and Stage.
- `D-worker-type-language`.
- `D-worker-type-immutable`.
- `D-stored-stage-is-authoritative`.
- `PRINCIPLES.md`: descriptive names, one canonical writer, backend is canonical.

## Contract files

The implementation agent must not change these shapes. The orchestrator will lock their exact diff after
the delegated plan and independent review:

- `src/planner/tickets/contracts.py`
- `src/planner/core/contracts.py`
- `src/planner/ticket_types/contracts.py` (internal package rename is deferred to AD02)
- `web/src/lib/types.ts`
- `web/src/lib/lifecycle.ts`
- SQLite schema/migrations in `src/planner/core/db.py`

The plan must enumerate every public/storage rename, including:

- Ticket dataclass and create/reconcile bodies.
- SQLite columns, indexes, migration detection, rebuild/copy SQL, and existing row preservation.
- HTTP request/response fields and the Worker-type manifest route/shape.
- CLI flags and JSON output.
- frontend types, lifecycle selection, routes, and rendered data attributes.
- event kind/payload terminology and migration of historical event rows if required.
- agent role skills and live docs that instruct callers.

## Planning task

Produce `plan.md` only. Do not edit code or contracts.

The plan must:

1. Inventory every affected file and classify it as contract, migration, implementation, test, skill, or doc.
2. Give the exact old → new naming map. Resolve coding-only `TicketState` precisely; do not leave a generic
   lifecycle type with the rejected `State` name.
3. Design a forward migration from every schema shape currently recognized by `db.py`, preserving values,
   indexes, foreign keys, and migration idempotence. Spot-check the migration parser and table-rebuild order.
4. Keep current-main behavior byte-for-byte where names are not the subject of the ticket.
5. Identify a bounded implementation file allowlist for the later implementation agent.
6. Specify deletion/static assertions proving old names and compatibility aliases are absent from live code,
   wire contracts, UI, skills, and docs while allowing historical decision identifiers where necessary.
7. Name focused tests that must fail before implementation and pass afterward.

## Acceptance

- Ticket creation rejects a missing or unknown `worker_type`; there is no `coding` default at any creation
  boundary or on the Ticket contract.
- Existing databases migrate `ticket_type` → `worker_type` and lifecycle `state` → `stage` without changing
  stored values or losing related data.
- Every Ticket read returns its stored `stage` directly and its stored `worker_type`.
- Public HTTP, CLI, frontend, event, and manifest contracts use Worker type / Stage naming only.
- `ticket_status` and its values are unchanged.
- Both shipped Worker types (`coding`, `new_worker`) behave exactly as before.
- Migration tests cover legacy, current, partially migrated, repeated-open, and rollback/failure paths.
- Static searches and typing tests prevent old public/storage vocabulary from returning.
- The full canonical `./verify` passes once after implementation and review fixes.

## Out of scope

- Renaming or restructuring `src/planner/ticket_types/` internally.
- Removing lifecycle tables, Registry forwarders, `coding_bridge`, or implicit coding workflow defaults.
- Changing Stage ids, field ids, Worker profiles, lifecycle order, scope rules, or Ticket control status.
- Automatic Employee-step eligibility, Review, Chat, Markdown, or resource-catalogue architecture.
