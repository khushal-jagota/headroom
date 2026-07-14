# AD01 implementation plan — Worker type and stored Stage contracts

## Outcome and boundary

AD01 is one vocabulary-and-persistence replacement. It changes the Ticket contract from
`ticket_type`/`state` to `worker_type`/`stage`, including every stored, HTTP, CLI, event, frontend,
skill, and live-document surface. It preserves the two existing Worker types, every Stage id, every
field id and value, every scope rule, event ordering, worker-selection rule, and visible interaction.

This ticket does **not** deepen the workflow module. The package path `planner.ticket_types`, the
`WorkflowDefinition`/`Registry` structure, the `coding_bridge` seam, and the current definition-driven
tables remain until AD02. Where those internals consume or emit the renamed Ticket contract, make only
the mechanical edits needed for AD01; do not remove a forwarder, move a module, or change how a
workflow is resolved.

The stored `tickets.stage` value is authoritative. `_row_to_ticket` copies it directly into
`Ticket.stage`; it must not resolve a Worker type merely to return or compare that value. Boot-time
`audit_ticket_registry_integrity` and the existing write doors continue to validate the
`(worker_type, stage, ceiling)` relationship. Callers that actually interpret a Stage (gate, successor,
terminal, scope) continue to resolve the stored Worker type through the existing seam until AD02.

## Requirement correction — do not accommodate an impossible deletion assertion

The ticket currently asks both (a) to migrate the old SQLite columns and historical events and (b) to
prove the old storage vocabulary is absent from all live code. Those two requirements cannot both be
literal: a forward migration must name `ticket_type`, `state`, and `state_changed` to recognize and
copy an old database. The out-of-scope rule also explicitly defers renaming the
`src/planner/ticket_types/` package to AD02.

Correct the deletion requirement as follows rather than hiding the contradiction behind aliases:

- Old persisted/event tokens are permitted only inside the sealed legacy-recognition section of
  `src/planner/core/db.py` and migration input fixtures/assertions in `tests/unit/test_db.py`.
- The literal package/import path `planner.ticket_types` and historical decision IDs/prose are allowed
  until AD02. They are code-location/history references, not domain or wire vocabulary.
- No old token is allowed in the canonical DDL, dataclasses, ordinary SQL, HTTP/OpenAPI surface, CLI,
  current event constructors, frontend source, role-skill instructions, or live explanatory prose.
- No property, parameter, route, flag, query name, request-body fallback, serializer fallback, or DOM
  attribute aliases an old name to the new one.

Codex plan review should confirm this correction before the contract diff is locked. If the ticket is
not corrected, its migration acceptance is unsatisfiable.

## Exact naming contract

### Persisted and backend Ticket contract

| Old | New | Notes |
|---|---|---|
| `tickets.ticket_type` | `tickets.worker_type` | `TEXT NOT NULL`, no default; ids remain `coding` and `new_worker`. |
| `tickets.state` | `tickets.stage` | `TEXT NOT NULL DEFAULT 'needs_kickoff'`; stored Stage ids are unchanged. |
| `Ticket.ticket_type` | `Ticket.worker_type` | Required non-default dataclass field, placed before `stage`. |
| `Ticket.state` | `Ticket.stage` | Plain stored `str`; no conversion to a coding enum on read. |
| `CreateTicketBody.ticket_type` | `CreateTicketBody.worker_type` | Use `Required[str]` (or an equivalent total TypedDict split); no contract default. |
| `ReconcileTicketFromExternalWorkBody.state` | `.stage` | Reconciliation reads the Ticket's existing immutable Worker type. |
| `CreateTicketFromExternalWorkBody.state` | `.stage` | Add required `.worker_type` to the create contract. |
| `StateBody` / `to` | `StageBody` / `to_stage` | Direct human Stage jump body. |
| `BlockedBySummaryRow.state` | `.stage` | Blocker reads expose a Ticket Stage. |
| `EventKind.state_changed` | `EventKind.stage_changed` | Event id changes; event order does not. |
| stage-change payload `{from,to,cause}` | `{from_stage,to_stage,cause}` | `cause=direct_state_jump` becomes `direct_stage_jump`; all other cause values stay byte-identical. |
| `item_children_changed.reason = "state"` | `"stage"` | This reason describes a Ticket Stage change. |
| seeded `ticket_created.payload.state` | `.stage` | Other creation payload keys are unchanged. |
| `idx_tickets_state` | `idx_tickets_stage` | Same single-column purpose. |
| `idx_tickets_type_state` | `idx_tickets_worker_type_stage` | Columns exactly `(worker_type, stage)`. |

`ticket_status`, `TicketStatus`, and all five `ticket_status` values are unchanged. Generic framework
state (FastAPI `app.state`), chat state, Svelte resource state, sprint/item status, and visual-state
types are not Ticket Stages and must not be renamed.

### Coding-only enum and reference constants

`TicketState` is not a generic lifecycle type: it contains the coding workflow's concrete stages.
Rename it precisely and keep the values unchanged:

| Old | New |
|---|---|
| `TicketState` | `CodingStage` |
| `STATE_ORDER` | `CODING_STAGE_ORDER` |
| `WORKER_STATE_ORDER` | `CODING_EMPLOYEE_STAGE_ORDER` |
| `GATING_FIELD` | `CODING_GATING_FIELD_BY_STAGE` |
| `ADVANCE_TARGET` | `CODING_NEXT_STAGE_BY_STAGE` |

Do not introduce `TicketStage` as another enum: non-coding stages are intentionally string ids. The
generic contract is the stored `Ticket.stage: str`; `CodingStage` is only the typed coding vocabulary.

The internal `ticket_types` package may retain `WorkflowDefinition.type_id`, `Registry.type_ids()`, and
its package name for AD02, but no such name crosses a public error or manifest boundary. Public error
details use `worker_type`/`worker_types` and `stage`; manifest serialization maps the internal
definition id to the new outward key.

### HTTP and CLI

| Old | New |
|---|---|
| create JSON `type` | `worker_type` |
| create/external-work JSON `state` | `stage` |
| response/list/board/Review/sprint/blocker `ticket_type` | `worker_type` |
| response/list/board/Review/sprint/blocker `state` | `stage` |
| board column/card `state_label` | `stage_label` |
| `GET /api/ticket-types` + `{types:[{type_id:...}]}` | `GET /api/worker-types` + `{worker_types:[{worker_type:...}]}` |
| list query `state`, `ticket_type` | `stage`; delete the type-disambiguation query entirely |
| `POST /api/tickets/{id}/state` body `{to}` | `POST /api/tickets/{id}/stage` body `{to_stage}` |
| `panels ticket create --type` | `--worker-type` |
| `panels ticket list --type --state` | `--stage`; delete the type-disambiguation flag entirely |
| Chief create `--type --state` | `--worker-type --stage` |
| Chief reconcile `--state` | `--stage` |

Stage filtering compares the stored `tickets.stage` value directly. It does not resolve or require a
Worker type; an id not present in any row simply returns an empty list. The former list-only type
parameter did not filter by type and existed only to interpret Stage, so it is deleted rather than
renamed.

Old endpoints and flags are deleted, not redirected. OpenAPI and Click help are the authoritative
absence checks. The existing create error becomes “ticket create requires a known worker type” with
detail `{"worker_type": value, "worker_types": [...]}`. Stage validation messages and details say
Stage, not state. JSON output follows the new keys; plain CLI output keeps the same ids/order and only
updates explanatory words/help.

### Frontend

| Old | New |
|---|---|
| `TicketDetail.ticket_type` | `.worker_type` |
| `TicketDetail.state` | `.stage` |
| `BlockedByTicket.state` | `.stage` |
| `BoardResponse.columns[].state` | `.stage` |
| `TicketTypeManifest` / `TicketTypesResponse` | `WorkerTypeManifest` / `WorkerTypesResponse` |
| manifest entry `type_id` / envelope `types` | `worker_type` / `worker_types` |
| manifest resource key/path `ticket-types` | `worker-types` |
| `Lifecycle.typeId/typeLabel` | `workerType/workerTypeLabel` |
| `Lifecycle.stateOrder/gatedState` | `stageOrder/gatedStage` |
| Ticket-stage helper parameters/properties named `state`/`ticketState` | `stage`/`ticketStage` |
| `stateLabel` (Ticket helper) | `stageLabel` |
| `data-ticket-type` | `data-worker-type` |
| `data-ticket-state` | `data-ticket-stage` |
| Ticket screen `data-state` | `data-stage` |

Keep `FieldStageVisualState` and chat/resource state names: those are visual/runtime state, not the
Ticket's stored Stage. Keep markup structure, CSS values, labels derived from the manifest, request
timing, and interactions unchanged.

## Migration design

### Call order and canonical shape

1. Bump `SCHEMA_VERSION` from 17 to 18.
2. Change the canonical `tickets` DDL to `worker_type` + `stage`. Remove ticket Stage indexes from the
   main DDL block because that block also runs against pre-migration tables; create them only in
   `_create_indexes` after the migration.
3. Before touching `tickets`, seed/resolve the project catalog needed to map a legacy Ticket `project`
   name. Keep the existing non-Ticket project migrations for `sprint_items` and `ideas`, but remove the
   Ticket branch from `_migrate_project_columns`; it must not call `_rebuild_tickets_with_project_id`.
4. Replace the entire chain of Ticket-table mutations with one
   `_migrate_tickets_to_v18_contract` call. `create_schema` must no longer call
   `_migrate_tickets_status_column`, `_migrate_ticket_user_note_column`,
   `_migrate_ticket_lifecycle`, `_migrate_ticket_implementer_column`,
   `_migrate_ticket_kickoff_columns`, `_migrate_ticket_type_column`, or
   `_rebuild_tickets_with_project_id`. Delete the obsolete rebuilding envelopes; retain only small pure
   row/JSON transformation helpers where the consolidated migration reuses their proven behavior.
5. The consolidated migration recognizes every old Ticket shape itself and performs project/status,
   lifecycle, kickoff, implementer, Worker-type, and Stage normalization only after acquiring its one
   FK-off + `BEGIN IMMEDIATE`/savepoint envelope. There is no pre-lock Ticket snapshot, row copy, ALTER,
   or scratch table.
6. Migrate affected historical events in the same transaction as the table rebuild. A target Ticket
   table with old events still runs the event portion, which makes crash/partial recovery idempotent.
7. `_create_indexes` creates `idx_tickets_alias`, `idx_tickets_stage`, and
   `idx_tickets_worker_type_stage`; it must not recreate either old index.
8. Set `PRAGMA user_version=18` only after every migration and index succeeds.

### Recognized input matrix

Use `PRAGMA table_info(tickets)` for column presence/NOT NULL and `sqlite_master.sql` for the legacy
shape markers and remaining canonical-shape checks. The single migration must accept all shapes the
current `db.py` recognizes without first rebuilding them elsewhere:

1. Pre-project/pre-lifecycle tables containing `project`, optional legacy `status`, `user_note`, old
   lifecycle ids (`in_progress`/`needs_review`), and `state`.
2. Post-project, pre-lifecycle `state` tables containing the old lifecycle ids/checks.
3. Post-lifecycle, pre-kickoff `state` tables with five field slots and `user_note`.
4. Current kickoff tables with `kickoff_note`/`kickoff_proposal`, six pre-normalized worker field slots,
   and `state`.
5. Post-kickoff, pre-type tables: `state`, no type column. Copy Stage and backfill `worker_type='coding'`.
6. Current v17 tables: `ticket_type` + `state`. Copy both values exactly, including `new_worker`.
7. One-column partial migrations: `worker_type` + `state`, or `ticket_type` + `stage`. Select the
   existing new name in preference to no value; rebuild the other column.
8. Canonical v18: `worker_type` + `stage`, both NOT NULL, no old columns/checks/default, correct table
   constraints. Table rebuild is skipped, but old-event detection still runs.

Reject after acquiring the migration envelope but before creating/copying the scratch table when
neither source exists for Stage, when a typed row has neither source for Worker type, or when both old
and new forms of the same column coexist.
Dual columns are ambiguous data, not a partial migration to guess through. A nullable target column,
retired enumerating CHECK, ceiling default, or otherwise incomplete target shape triggers a rebuild;
NULL copied values fail and roll back rather than receiving a hidden default.

For each legacy row, derive the canonical values in the same order as the removed migration chain:

1. Resolve `ticket_status`: preserve `ticket_status` when present; otherwise map legacy `status`
   (`agent_working`/`agent_running_step`, `awaiting_approval`, `user_takeover`, `errored`, else `empty`),
   or use `empty` when neither exists.
2. Resolve project membership: preserve `project_id` when present; otherwise map trimmed legacy
   `project` through the already-seeded project catalog, forcing `NULL` when `sprint_item_id` is set.
3. Apply the exact existing lifecycle conversion to the source Stage/ceiling/fields: `in_progress` ->
   `needs_implementation`; `needs_review` -> `needs_closeout` only for settled/empty control status and
   otherwise `needs_implementation`; preserve the current pending-Result proposal reconstruction and
   corrupt-proposal rejections.
4. Apply the exact existing kickoff conversion: turn `user_note` or `kickoff_note` plus an optional
   compound `kickoff_proposal` into the leading `kickoff` slot; preserve the settled-versus-pending
   rules and force `awaiting_approval` for `needs_kickoff`. Already post-kickoff fields are copied
   byte-for-byte and are not decoded/re-encoded.
5. Preserve `implementer` when present, otherwise use `NULL`; preserve every other canonical column.
6. Resolve `worker_type` from `worker_type`, else `ticket_type`, else `coding`; resolve `stage` from
   `stage`, else the lifecycle-converted `state`.

The ordering above preserves the current production migration decisions while moving them inside one
lock. It does not change a legacy value merely to satisfy a new name.

### Transaction and copy algorithm

- Read FK state before beginning. If foreign keys are on and the connection is already in a
  transaction, raise the same explicit autocommit requirement as the existing safe rebuilds.
- Turn `PRAGMA foreign_keys=OFF` before `BEGIN IMMEDIATE`. If a caller already holds a transaction with
  FKs off, use a savepoint.
- Under the held write lock: inspect the source schema, snapshot rows, run the status/project/lifecycle/
  kickoff/implementer transformations above, create `tickets_new` with the exact canonical DDL, and
  copy every column explicitly by name. There must be no read of Ticket rows before the lock. Preserve
  `fields` text byte-for-byte only for shapes whose kickoff fields are already canonical; legacy
  lifecycle/kickoff shapes intentionally receive the same JSON conversion as today.
- Still in that transaction, rewrite only affected event rows:
  - `state_changed` -> `stage_changed`, `from` -> `from_stage`, `to` -> `to_stage`, and only the cause
    value `direct_state_jump` -> `direct_stage_jump`;
  - `ticket_created` payload key `state` -> `stage` when present;
  - `item_children_changed` payload `reason:"state"` -> `reason:"stage"`.
  Require affected payloads to decode to JSON objects and reject conflicting old+new keys. Preserve
  unaffected keys and rows. Do not rewrite unrelated payload bytes.
- Drop the old table, rename `tickets_new`, and run `PRAGMA foreign_key_check` before commit. This
  explicitly covers `day_tickets`, sprint membership, chat-owned references, and every other existing
  FK to Tickets.
- On any copy, JSON, swap, or FK-check failure: roll back the savepoint/transaction, release it, drop a
  surviving scratch table only after rollback, restore FK mode, and re-raise. The original schema,
  rows, indexes, and events must remain unchanged.
- Re-enable foreign keys in `finally`. A second `create_schema` sees the complete v18 shape and no old
  events, performs no row/event writes, and produces identical schema SQL and rows.

This is one rebuild rather than a chain of rebuilds or `ALTER TABLE RENAME COLUMN`: it handles every
recognized historical shape and both one-column partial shapes, proves the target constraints, removes
stale indexes/checks/defaults, eliminates the existing kickoff migration's pre-lock snapshot window,
and gives all Ticket row transformations plus event vocabulary one rollback envelope.

## Serial implementation steps

1. **Write RED contract/static tests.** Pin the new Python/TypeScript shapes, missing Worker-type
   rejection, new route/flags/manifest, old-surface absence, direct Stage reads, and exact new event
   contract. Do not mass-update existing green tests first.
2. **Lock and implement the contract diff.** Change only the six ticket-listed contract files plus the
   canonical DDL section. Compile/type-check to let the removed names enumerate consumers; do not add
   aliases to make the tree temporarily green.
3. **Implement the consolidated v18 migration.** Remove/bypass every earlier Ticket-table mutation in
   `create_schema`; add the all-shape parser, in-lock legacy transforms, explicit canonical copy, event
   rewrite, indexes, idempotence, and rollback. Keep non-Ticket migrations otherwise unchanged. Run
   only the focused migration tests during implementation.
4. **Rewire backend persistence and serializers.** Make `_row_to_ticket` a direct value read; update
   SQL, actions, HTTP, events, blocker/sprint/board/Review reads, seed import, and employee prompts.
   Preserve existing workflow-resolution calls where interpretation is required.
5. **Replace public ingress.** Switch body/query/route names and Click flags atomically; delete old
   definitions. Test both shipped Worker types at creation and through one ordinary Stage advance.
6. **Replace manifest/frontend consumption.** Change the manifest endpoint/resource/shape, then update
   routes, component props, rendered data attributes, selectors, and CSS without changing structure or
   styling.
7. **Update skills and live docs.** Rename `docs/ticket-types.md` to `docs/worker-types.md`, repair all
   current links, and use Worker type/Stage in instructions. Literal deferred code paths may remain
   backticked and explicitly described as implementation names pending AD02.
8. **Run focused tests and static deletion assertions.** Search output must be classified against the
   narrow exceptions above. Address each unexpected occurrence; do not broaden the allowlist.
9. **Independent diff review, fixes, then one canonical `./verify`.** The orchestrator owns the final
   run and memory updates. Do not run a second `./verify` merely to quote it.

## Focused RED tests

### New test module

Add `tests/unit/test_worker_type_stage_contracts.py` before implementation with these failures:

- `Ticket` requires `worker_type` and `stage`, has no `ticket_type`/`state` field or default, and
  `CodingStage` has the exact old enum values in the same order.
- `_row_to_ticket`/`read_ticket` returns raw stored `worker_type` and `stage` without calling the
  registry; boot audit and write-door tests separately retain integrity validation.
- `POST /api/tickets` rejects missing/unknown `worker_type`, rejects the old `type` body by virtue of
  the missing required field, and creates both `coding` and `new_worker` with the same initial values
  as v17.
- Worker type remains unchanged when PATCH receives ordinary mutable fields; OpenAPI contains no
  writable Worker-type field after creation.
- OpenAPI exposes `stage`/`worker_type`, `/api/worker-types`, and `/tickets/{ticket_id}/stage`; it has no
  `/api/ticket-types` or Ticket `/state` route and no old Ticket query/body/response property.
- The direct Stage route accepts `to_stage`; list filtering uses `stage` + `worker_type`; both retain
  existing reserved-bookend semantics.
- manifest JSON is exactly `{worker_types:[...]}` with per-entry `worker_type`, unchanged ordering and
  unchanged Stage/field/profile values.
- Stage transitions emit `stage_changed` in the existing position with exact
  `{from_stage,to_stage,cause}` payload and `direct_stage_jump` cause where applicable.
- Click help exposes only `--worker-type`/`--stage`; invoking old flags fails as an unknown option.
- targeted source scanners enforce the corrected deletion boundary: canonical DDL/contracts/HTTP/CLI/
  UI/skills/live docs contain no old outward tokens, while only the migration section, migration
  fixtures, `planner.ticket_types` path, and historical decisions are exempt.

### Migration tests in `tests/unit/test_db.py`

Replace the v17 type-migration section with v18 tests that assert:

- fresh schema: required/no-default `worker_type`, required `stage` with the unchanged kickoff
  default, no enumerating Stage/ceiling check, exact new indexes/columns, no old columns/indexes;
- each pre-lifecycle fixture migrates directly through the single v18 envelope: legacy project/status,
  `in_progress`/`needs_review`, Result proposal reconstruction, and old ceiling rules produce the exact
  values asserted by the existing migration tests;
- each post-lifecycle/pre-kickoff and kickoff fixture migrates directly through that same envelope:
  `user_note`, `kickoff_note`, compound kickoff proposal, settled/pending kickoff values, control
  status, and the six canonical field slots match the current expected outputs;
- post-kickoff pre-type input backfills `coding`, renames `state` to `stage`, preserves every other
  column and fields JSON bytes, and retains day/link/sprint FKs;
- v17 input preserves each row's actual Worker type (`coding` and `new_worker`) and Stage value;
- a connection-trace/static assertion proves `create_schema` calls no retired Ticket rebuild/ALTER
  helper and performs no `SELECT * FROM tickets` before the consolidated `BEGIN IMMEDIATE`/savepoint;
- project migration still upgrades `sprint_items` and `ideas`, while legacy Ticket `project` mapping is
  performed inside the consolidated Ticket transaction;
- both one-column partial shapes complete correctly; a dual-column ambiguous shape fails unchanged;
- incomplete target constraints rebuild rather than being mistaken for complete;
- relevant historical events are rewritten exactly, unrelated event payload bytes are unchanged, and
  a target table plus old events completes on reopen;
- corrupt/conflicting affected event JSON rolls back the table and events together;
- failed FK check leaves the original schema/rows/indexes/events intact, drops `tickets_new`, and
  restores FK mode;
- repeated `create_schema` produces identical schema SQL, Ticket rows, event rows, index definitions,
  FK result, and `user_version=18`.

### Existing focused suites to update, not weaken

- `tests/unit/test_worker_type_manifest_endpoint.py` (rename from
  `test_ticket_type_manifest_endpoint.py`): new route/envelope/key, same definition ordering/content.
- `tests/unit/test_worker_type_persistence.py` (rename from
  `test_ticket_type_persistence.py`): Worker-type creation/write/audit doors and direct Stage read.
- `tests/unit/test_type_driven_ingress.py`: new body/query/direct-Stage contract, same cross-workflow
  validation behavior.
- `tests/unit/test_ticket_lifecycle.py`, `test_generic_field_storage.py`,
  `test_engine_parameterization.py`, and `tests/typing/tt01_overload_cases.py`: `CodingStage` precision
  and unchanged return narrowing/transition behavior.
- `tests/unit/test_board_view.py`, `test_sprint_views_type_driven.py`,
  `test_queues_approval_type_driven.py`, `test_links.py`: new outward read keys, same rows/order/status.
- `tests/e2e/test_cli_verbs.py`, `test_chief_external_work_cli.py`, `test_flows_a.py`, and
  `test_flows_b.py`: new flags/JSON/DOM attributes and unchanged end-to-end outcomes.
- `web/tests/lifecycle.test.mjs`: new manifest and Ticket keys with identical derived behavior.
- `web/tests/event-mapping.test.mjs`: replace the backend kind with `stage_changed`; completeness and
  invalidation expectations remain unchanged.

## Bounded implementation allowlist

The implementation agent may edit only the paths below. Renames count as delete+add within this list.
Anything discovered outside it is a blocker for orchestrator re-scoping, not permission to expand.

### Contracts and migration

- `src/planner/tickets/contracts.py`
- `src/planner/core/contracts.py`
- `src/planner/ticket_types/contracts.py`
- `web/src/lib/types.ts`
- `web/src/lib/lifecycle.ts`
- `src/planner/core/db.py`

### Backend/CLI implementation

- `src/planner/cli/main.py`
- `src/planner/core/links.py`
- `src/planner/core/server.py`
- `src/planner/days/data.py`
- `src/planner/runtime/employee_step_runner.py`
- `src/planner/runtime/readiness.py`
- `src/planner/runtime/ticket_readiness_loop.py`
- `src/planner/seed/contracts.py`
- `src/planner/seed/importer.py`
- `src/planner/seed/logic/workspace.py`
- `src/planner/sprints/data.py`
- `src/planner/sprints/logic/status.py`
- `src/planner/sprints/logic/__init__.py`
- `src/planner/sprints/logic/blockers.py` (delete; unused retired helper)
- `src/planner/sprints/views.py`
- `src/planner/tickets/actions.py`
- `src/planner/tickets/api.py`
- `src/planner/tickets/data.py`
- `src/planner/tickets/views.py`
- `src/planner/tickets/logic/__init__.py`
- `src/planner/tickets/logic/admission.py`
- `src/planner/tickets/logic/coding_bridge.py`
- `src/planner/tickets/logic/decisions.py`
- `src/planner/tickets/logic/external_work.py`
- `src/planner/tickets/logic/fields_codec.py`
- `src/planner/tickets/logic/machine.py`
- `src/planner/tickets/logic/resolution.py`
- `src/planner/tickets/logic/ticket_type_guard.py`
- `src/planner/ticket_types/__init__.py`
- `src/planner/ticket_types/coding.py`
- `src/planner/ticket_types/new_worker.py`
- `src/planner/ticket_types/logic/__init__.py`
- `src/planner/ticket_types/logic/manifest.py`
- `src/planner/ticket_types/logic/validation.py`
- `src/planner/ticket_types/logic/views.py`
- `src/planner/ticket_types/registry.py`

The `ticket_types` files are allowed only for contract consumption, `CodingStage`, manifest/error
output, and parameter-name fallout. Package/module restructuring and removal of forwarders/defaults is
forbidden in AD01.

`days/data.py` is allowed only for the one live comment that says a removed day association leaves the
Ticket itself untouched. The unused `sprints.logic.blockers` helper and its package export are deleted:
they have no callers, duplicate `core.links.blocker_summary`, and retain rejected Ticket-State names.

### Frontend/assets

- `assets/app.css`
- `web/dist/index.html` (generated production entrypoint only)
- `web/dist/assets/index-*.js` (generated production bundle add/delete only)
- `web/src/components/ApprovalBlock.svelte`
- `web/src/components/ScopePairPicker.svelte`
- `web/src/components/TicketStageSection.svelte`
- `web/src/lib/manifest.svelte.ts`
- `web/src/lib/ui.ts`
- `web/src/routes/BoardRoute.svelte`
- `web/src/routes/ReviewRoute.svelte`
- `web/src/routes/SprintRoute.svelte`
- `web/src/routes/TicketRoute.svelte`

FastAPI serves the checked-in `web/dist` tree, so source-only frontend replacement is not a shipped
replacement. Regenerate these two output classes with `npm --prefix web run build` after the reviewed
source changes; do not hand-edit generated files. The generated bundle must not retain the deleted
Worker-type/Stage wire or DOM vocabulary.

### Live skills and docs

- `skills/panels-chief-of-staff/SKILL.md`
- `skills/panels-rollover/SKILL.md`
- `skills/panels-sprint-planning/SKILL.md`
- `skills/panels-worker/SKILL.md`
- `skills/panels-worker-coding/SKILL.md`
- `skills/panels-worker-new-worker/SKILL.md`
- `docs/README.md`
- `docs/cli.md`
- `docs/employee-runtime.md`
- `docs/frontend.md`
- `docs/systems.md`
- `docs/systems.html`
- `docs/tickets-and-gates.md`
- `docs/ticket-types.md` (delete/rename source)
- `docs/worker-types.md` (new rename target)

Do not rewrite `decisions.md`, `PROGRESS.md`, retired orchestration records, or `CONTEXT.md` in the
implementation ticket. The orchestrator owns current-cycle memory; historical decision wording is an
explicit scan exception.

### Tests/support

- `tests/support/probe.py`
- `tests/typing/tt01_overload_cases.py`
- `tests/unit/test_worker_type_stage_contracts.py` (new)
- `tests/unit/test_board_view.py`
- `tests/unit/test_authctx_routes.py`
- `tests/unit/test_chat_commands.py`
- `tests/unit/test_chat_images.py`
- `tests/unit/test_chat_seed.py`
- `tests/unit/test_chief_external_work.py`
- `tests/unit/test_cli_entrypoints.py`
- `tests/unit/test_copy_text_type_driven.py`
- `tests/unit/test_core_loops.py`
- `tests/unit/test_day_api.py`
- `tests/unit/test_days.py`
- `tests/unit/test_db.py`
- `tests/unit/test_employee_step_runner.py`
- `tests/unit/test_engine_parameterization.py`
- `tests/unit/test_external_work_generic.py`
- `tests/unit/test_generic_field_storage.py`
- `tests/unit/test_go_no_go_gate.py`
- `tests/unit/test_links.py`
- `tests/unit/test_new_worker_type.py`
- `tests/unit/test_probe_type.py`
- `tests/unit/test_projects.py`
- `tests/unit/test_queues_approval_type_driven.py`
- `tests/unit/test_readiness_actions.py`
- `tests/unit/test_return_for_revision.py`
- `tests/unit/test_seed.py`
- `tests/unit/test_sprint_item_status_buckets.py`
- `tests/unit/test_sprint_views_type_driven.py`
- `tests/unit/test_sprints.py`
- `tests/unit/test_ticket_delete.py`
- `tests/unit/test_ticket_edit_api.py`
- `tests/unit/test_ticket_lifecycle.py`
- `tests/unit/test_ticket_readiness_loop.py`
- `tests/unit/test_ticket_type_manifest_endpoint.py` (delete/rename source)
- `tests/unit/test_worker_type_manifest_endpoint.py` (new rename target)
- `tests/unit/test_ticket_type_persistence.py` (delete/rename source)
- `tests/unit/test_worker_type_persistence.py` (new rename target)
- `tests/unit/test_ticket_type_registry.py`
- `tests/unit/test_tickets_engine.py`
- `tests/unit/test_trusted_ingress.py`
- `tests/unit/test_type_driven_ingress.py`
- `tests/unit/test_value_edit_api.py`
- `tests/unit/test_value_edit_logic.py`
- `tests/unit/test_worker_context.py`
- `tests/unit/test_worker_my_ticket.py`
- `tests/e2e/test_blockers_frontend.py`
- `tests/e2e/test_board_stage_indicators.py`
- `tests/e2e/test_chat_images.py`
- `tests/e2e/test_chief_external_work_cli.py`
- `tests/e2e/test_chief_of_staff.py`
- `tests/e2e/test_cli_verbs.py`
- `tests/e2e/test_flows_a.py`
- `tests/e2e/test_flows_b.py`
- `tests/e2e/test_live_chat_state.py`
- `tests/e2e/test_ticket_file_previews.py`
- `tests/e2e/test_ticket_hard_delete_e2e.py`
- `web/tests/event-mapping.test.mjs`
- `web/tests/lifecycle.test.mjs`

Chat tests in the allowlist may change only their Ticket-creation flags/fixtures; `/api/chat/{id}/state`
and Chat state assertions must remain untouched.

## Risk controls and review checklist

- **Single-lock migration:** spot-check `create_schema` itself, not only the helper. Every recognized
  Ticket shape must reach `_migrate_tickets_to_v18_contract` without an earlier Ticket ALTER, snapshot,
  scratch copy, drop, or rename. The old kickoff/type/lifecycle rebuild envelopes and the Ticket branch
  of project migration must be unreachable or deleted.
- **Migration parser/order:** prove the consolidated row transformer reproduces the existing status,
  project, lifecycle, pending-Result, kickoff, implementer, type, and ceiling outcomes for every old
  fixture. Fresh v18 DDL makes the table portion a no-op, while old events can still complete. The DDL
  must not attempt a new-column index before an old table is migrated.
- **Column-shift/data loss:** explicit named inserts plus full before/after row dictionaries and
  byte-equal fields JSON for already canonical shapes; exact transformed JSON for legacy lifecycle/
  kickoff shapes; never `INSERT ... SELECT *`.
- **FK/table-swap safety:** lock before snapshot, FK toggle outside transaction, foreign-key check
  before commit, rollback proof with day-ticket references.
- **Half-migrated events:** table and affected event rows share a transaction; target-table/old-event
  reopen is tested.
- **Name overreach:** do not rename chat/resource/framework state or sprint-item status. Do not begin
  AD02 by deleting `coding_bridge`, defaults, or registry forwarders.
- **Hidden compatibility:** inspect OpenAPI, Click help, TypeScript types, DOM selectors, and source
  scanners; a fallback accepting `type`, `ticket_type`, `state`, old routes, or old flags is a failure.
- **Worker-type immutability:** no PATCH field or writer is added. Creation is the only choice point.
- **Direct Stage read:** no registry call in `_row_to_ticket`; interpretation call sites still resolve
  Worker type when they ask a workflow question. The no-definition `fields_from_json(raw)` path decodes
  the stored field-key map as stored so `_row_to_ticket` does not need a definition; definition-supplied
  decoding remains the audit/interpretation validator for declared fields.
- **Behavior parity:** compare event order, Stage ids, Worker-profile skill, scope transitions, proposal
  settlement, board ordering, Review contents, sprint derivation, employee prompt delivery, and UI
  structure for both `coding` and `new_worker`.

## Residual inventory uncertainty for Codex review

The inventory was generated from all concrete old identifiers/keys/routes plus the known Ticket SQL
read paths. Generic `state` is also heavily used for FastAPI app state, Chat state, resource state, and
sprint/item status, so a global textual replacement would be wrong. Codex should specifically inspect
the implementation diff for any Ticket-shaped `state` that escaped because it is carried inside an
`AnyRecord` (especially Review/Sprint/board JSON), and for any CSS/e2e selector coupled to the Ticket
screen's old `data-state`. If such a file lies outside the allowlist, stop and amend the ticket before
editing it. Do not treat this uncertainty as authorization for a repository-wide replacement.

## Done condition

AD01 is ready to integrate only when the focused tests pass, the independent diff review reports no
unresolved contract/migration violations, old outward names are absent under the corrected narrow
exceptions, and the orchestrator's single post-review `./verify` is clean. The implementation commit
must contain AD01 only; no AD02 deepening or later-program work is bundled.
