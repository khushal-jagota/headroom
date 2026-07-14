# AD01 contract lock

The orchestrator generated this skeleton after the corrected plan passed independent review. The
implementation must consume these names exactly. It may complete the migration and update consumers,
but it must not add aliases, defaults, alternate wire keys, or different shapes.

## Python Ticket contract

- `CodingStage` is the coding-only enum.
- `CODING_STAGE_ORDER`, `CODING_EMPLOYEE_STAGE_ORDER`, `CODING_GATING_FIELD_BY_STAGE`, and
  `CODING_NEXT_STAGE_BY_STAGE` are the coding-only reference tables.
- `CreateTicketBody.worker_type` is required even though the remaining creation fields are optional.
- `ReconcileTicketFromExternalWorkBody.stage` is required.
- `CreateTicketFromExternalWorkBody.worker_type` and `.stage` are required.
- `StageBody.to_stage` is the direct Stage-jump body.
- `Ticket.worker_type` and `Ticket.stage` are required stored fields immediately after `title`.
  `Ticket` has no Worker-type default and no `ticket_type` or `state` property.

## Shared and Worker-type contracts

- `EventKind.stage_changed` has value `stage_changed` and payload
  `{from_stage, to_stage, cause}`.
- `BlockedBySummaryRow.stage` exposes the blocking Ticket's stored Stage.
- Internal `WorkflowDefinition.type_id` and the `planner.ticket_types` package remain for AD02.
- The serialized `ManifestDict` exposes `worker_type`; it does not expose `type_id`.
- `TransitionHook.old_stage` and `.new_stage` use Stage language. This is a vocabulary correction only;
  the hook structure and behavior are unchanged.

## Frontend contracts

- `WorkerTypeManifest.worker_type` and `WorkerTypesResponse.worker_types` are the served manifest.
- `Lifecycle.workerType`, `.workerTypeLabel`, `.stageOrder`, and `.gatedStage` replace the rejected
  names; existing gate, advance, ceiling, field, and Stage-label behavior is unchanged.
- `TicketDetail.worker_type`, `TicketDetail.stage`, `BlockedByTicket.stage`, and
  `BoardResponse.columns[].stage` mirror the server.
- `ticketStage` is the visual-input property for a Ticket's Stage. Generic Svelte, resource, Chat, and
  visual-state names remain untouched.

## SQLite contract

- Schema version is 18.
- Canonical columns are `worker_type TEXT NOT NULL` with no default and
  `stage TEXT NOT NULL DEFAULT 'needs_kickoff'`.
- Canonical indexes are `idx_tickets_stage(stage)` and
  `idx_tickets_worker_type_stage(worker_type, stage)`.
- The reviewed plan's one lock-held terminal migration is binding. Historical old tokens may appear
  only inside its sealed recognition/transform section and migration fixtures.

## Implementation rule

The implementation agent may fill migration logic in `src/planner/core/db.py` and update consumers of
the five contract/type files. It must not change the declarations above or the canonical DDL/index
shape. Any discovered need for a different shape returns to the orchestrator as a blocker.
