# ACP-07 generic Ticket employee-backend selection — implementation plan

## Status and boundary

This ticket is ready to implement. The earlier contract mismatch is resolved: an editable pristine
Kickoff Ticket is exactly `stage == "needs_kickoff"`, `ticket_status in {"awaiting_approval",
"empty"}`, `employee_session_id is None`, and no durable
`conversation_session_bindings` row. That includes the ordinary fresh-Ticket shape without changing
the Ticket lifecycle.

This is the serial, backend-neutral selection slice. It adds no Codex or Claude definition. Its
production catalog initially contains Hermes, while focused tests register a second fake backend to
prove that order, defaults, selection, binding, browser chat, and automatic work are genuinely
generic. ACP-07's Codex-definition continuation and ACP-08's Claude-definition ticket extend the one
registration tuple after this slice lands.

Implementation must start from the integrated ACP-06 legacy-deletion head. If ACP-06 deletes a
current compatibility test or call site named below, do not recreate it; prune that deleted path from
the mechanical allowlist and keep its surviving destination proof. No compatibility adapter, missing
column fallback, legacy session migration path, Chief selector, or session-reset-on-backend-change is
part of this ticket.

## Settled implementation shape

### One catalog and one registration point

Add `src/planner/conversation/backend_catalog.py` as the sole backend-registration authority. It owns:

- an immutable `EmployeeBackendRegistration` with one stable non-empty `backend_key` and a runtime
  builder;
- the built registration value containing the exact `AgentBackendDefinition`, its
  `AcpEmployeeChildFactory`, and its executability probe;
- an ordered `EmployeeBackendCatalog` which rejects empty/duplicate keys, exposes
  `registered_backend_keys()` in declaration order, validates a requested key, and materializes every
  declared registration exactly once for one `ConversationComposition`;
- one production registration tuple, initially containing only the Hermes builder, and a factory
  returning a fresh catalog over that tuple.

The Hermes builder absorbs the Hermes definition/factory/executable-resolution work currently
performed inline in `ConversationComposition.build`. A materialized registration must return a
definition whose `backend_key` exactly equals its declared key; a missing, extra, or mismatched
definition fails composition. `AcpEmployeeRegistry` receives the catalog, not independently assembled
definition and factory maps. Adding Codex or Claude later means adding its builder and one ordered tuple
entry here; no Ticket, repository, manifest, or UI branch changes.

`src/planner/worker_types/configuration.py` becomes the one process composition seam for both
authorities. Add immutable `ConfiguredEmployeeRuntimeDefinitions` containing one
`EmployeeBackendCatalog` and the `WorkerTypeRegistry` constructed and validated against that exact
catalog. Build the production pair once. `configured_worker_type_registry()` delegates to the current
pair so Ticket data, discovery, and `EmployeeStepRunner` cannot observe a separately configured
registry. `create_app`, startup audit, manifest, Ticket ingress, seed, the binding repository, and
`ConversationComposition` all read the same current pair; none creates another catalog.

Replace the registry-only test install/restore seam with one atomic pair install/restore seam.
`ConversationTestOptions` retains capacity/identifier tuning and may carry only the complete paired
authority; the e2e app fixture installs that pair for the app lifespan and restores the prior pair in
one `finally`. Tests never coordinate a registry, app catalog, and ACP catalog separately.
Framework-free data tests may construct and pass a pair directly. There is no string-list allowlist in
configuration or the frontend.

The Chief backend remains an explicit composition input, pinned to the Hermes registration and
validated as registered. The SQLite repository receives that Chief key separately; it is not a
repository-wide Ticket default and is never inferred from catalog order.

### Worker type and Ticket selection

Add required `default_employee_backend: str` to `WorkerProfile`. Each shipped profile declares
`"hermes"`; every test definition declares a value too. `WorkerTypeRegistry` validates that the value
is trimmed/non-empty and registered in its injected catalog. The canonical probe catalog has stable
order `("hermes", "probe-backend")`, while the probe Worker type defaults to the second value, so a
test catches first-entry inference.

Add required `employee_backend: str` to `Ticket` and the `tickets` row mapper. Ticket creation resolves
the Worker type, then chooses the explicit override when present or
`worker_profile.default_employee_backend` when absent, and validates that exact key against the
catalog before opening a write transaction. Every insert writes the selected value explicitly. There
is no SQL default and no row-read fallback.

The public Worker-type resource becomes:

```json
{
  "employee_backends": ["hermes"],
  "worker_types": [
    {"worker_type": "coding", "default_employee_backend": "hermes"}
  ]
}
```

The shown objects are abbreviated; all existing manifest fields remain unchanged.

### Canonical pristine-Kickoff writer

Add one `tickets_data.write_employee_backend(...)` writer. It receives the catalog explicitly and owns
one `BEGIN IMMEDIATE` transaction:

1. validate the candidate as a registered non-empty key;
2. read `employee_backend`, `stage`, `ticket_status`, and `employee_session_id` for the Ticket;
3. return immediately, without touching `updated_at` or events, when the stored value already equals
   the candidate (including a harmless retry after the Ticket has frozen);
4. for a real change, require `needs_kickoff`, status `awaiting_approval` or `empty`, no mirrored
   employee session, and no binding row for the Ticket;
5. update only `employee_backend` and `updated_at`;
6. append exactly one existing `ticket_updated` event with
   `{"field":"employee_backend","from":old,"to":new}`; then commit and return the reloaded Ticket.

Do not route this through the general multi-field PATCH writer: its lifetime guard and binding race
are a distinct state transition. Expose `PUT /api/tickets/{ticket_id}/employee-backend` with the exact
body `{"employee_backend": "..."}` and direct-write authorization. `panels ticket set <id>
employee-backend --value <key>` uses this endpoint; `--clear` is rejected because the field is
required. The writer does not wake automatic discovery: a permitted Ticket is still at unaccepted
Kickoff, and a frozen Ticket cannot change.

Both this writer and binding CAS take SQLite's immediate writer lock. The deterministic race proof
uses two connections and latches. If selection commits first, a stale old-backend CAS fails against
the new stored selection. If CAS commits first, it atomically writes the binding/session mirror and
the selection writer rejects. No ordering may leave the Ticket selection, binding backend, and
session mirror inconsistent.

### Creation, reads, and events

Add optional `employee_backend` ingress to all live creation paths:

- `POST /api/tickets` and `panels ticket create --employee-backend`;
- `POST /api/chief/tickets/from-external-work` and
  `panels chief create-ticket-from-external-work --employee-backend`;
- `python -m planner.seed --employee-backend` and `seed_from_source(...,
  employee_backend=...)`;
- `tickets_actions.create_ticket`, `tickets_data.create_ticket`,
  `tickets_actions.create_ticket_from_external_work`, and
  `tickets_data.create_ticket_from_external_work`.

`CreateTicketBody` and `CreateTicketFromExternalWorkBody` carry that optional key. Both creation
marshallers preserve absent versus explicit input, and the strict external-create allowlist admits the
key. The external reconcile body/allowlist does not: changing an existing Ticket uses only the
canonical pristine-Kickoff writer.

The ordinary and external-work `ticket_created` payloads gain the exact selected
`employee_backend`; existing payload keys and event order remain. Unknown overrides fail before an
insert/event/wake. The seed importer resolves its one selected Worker definition and backend before
`BEGIN IMMEDIATE`, then writes the same explicit value to every imported Ticket. It does not add the
backend to `ParsedTicket`, infer it from Markdown, or default it to Hermes.

Return the exact value from `ticket_json`, Ticket list/day/sprint loose-ticket responses, Board card
rows, and Sprint item Ticket rows. Add `employee_backend: <key>` to Ticket copy text. Extend the
startup Ticket integrity audit to reject null/blank/unregistered stored selections against the
application catalog. Do not add a derived `employee_backend_editable` field: the server writer remains
authoritative, while the normal UI predicate follows the already-returned Ticket state/session fields.

Reuse `EventKind.ticket_updated`; do not add an event kind. The existing entity-prefix catalogue rule
already invalidates `ticket:<id>`, `board`, and `sprint:current` for this event and does not invalidate
Review. Add the exact employee-backend event case to the catalogue proof so a future mapping change
cannot make the selector stale.

### Schema v26 migration and parser ordering

Bump `SCHEMA_VERSION` from 25 to 26. Fresh DDL adds
`employee_backend TEXT NOT NULL` next to `worker_type`, with no `DEFAULT` and no enumerating `CHECK`.
Every live insert must therefore supply it.

Do not rewrite the sealed v20/v22 historical migrations or ACP-06's terminal v25 cutover. Add a
final v26 Ticket-table rebuild after those normalizers and before binding cleanup, indexes, and
`PRAGMA user_version=26`:

- `_tickets_table_is_v26` checks the column exists, is `NOT NULL`, and has no SQL default;
- `_V26_TICKETS_TABLE_SQL` is the current canonical Ticket table plus the new column;
- while holding one immediate migration transaction and before destructive DDL, inspect every
  existing binding; any `backend_key != "hermes"` aborts without replacing the original Ticket table
  or advancing `user_version`;
- copy every historical Ticket column byte-for-byte and write literal `"hermes"` only into the new
  column;
- preserve the current foreign-key-off/rebuild/foreign-key-check discipline, drop the scratch table on
  failure, restore foreign keys, recreate indexes, and make reopen idempotent.

The one literal migration assignment is not a live fallback: pre-feature work was Hermes. A database
already at v26 may legitimately contain later Codex/Claude selections and must not be rewritten on
reopen. ACP-06 has already reset pre-cutover bindings and mirrors; the v26 precheck guarantees that
the selector migration never hides a contradictory post-cutover binding.

### Binding and runtime ownership

Inject the catalog and explicit Chief backend into `SqliteConversationBindingRepository`; remove
`default_backend_key`.

- For a Ticket without a binding, `resolve_employee` reads the non-null stored
  `tickets.employee_backend`, validates it as registered, and returns it.
- For a Ticket with a binding, both the binding backend and stored selection must be registered and
  exactly equal; otherwise both binding and employee resolution fail closed.
- The Chief continues to use the separately injected Hermes key and its binding must match it.
- CAS validates the candidate key as registered under the same immediate transaction. For a Ticket it
  must equal the stored selection; a replacement must also preserve the existing binding backend.
  A stale-CAS winner is returned only after the winner has passed those same checks.
- `is_backend_available(key)` means `catalog.is_registered(key)`, never `key == "hermes"`.

`ConversationComposition` materializes every catalog entry and gives the catalog to the repository
and registry. The step gateway's existing zero-argument aggregate status probe returns true when at
least one materialized registration's executability probe passes; Ticket selection and CAS still use
registration, not process availability. Add
`tickets.employee_backend` to the worker-permission settlement snapshot query so a binding whose
backend no longer equals the Ticket selection cannot settle a worker permission.

Do not add an Automatic Employee backend field. Browser attach/prompt already resolves the
`ConversationEmployee` through `ConversationHub.repository`; `AcpStepGateway` uses that same hub and
repository before `EmployeeStepRunner` delivers its tracked prompt. Preserve that path and prove it.
No discovery, eligibility, prompt, proposal, status, or settlement behavior changes.

### Restrained Ticket control and deferred first attach

Extend `TicketDetail` with required `employee_backend`, `WorkerTypeManifest` with required
`default_employee_backend`, and `WorkerTypesResponse` with ordered `employee_backends: string[]`.
`TicketRoute.svelte` derives options only by mapping the served keys through existing `labelize`; it
contains no Hermes/Codex/Claude conditional or copied list.

Place the control in the existing `ticket-facts` line beside the Worker-type pill:

- while the returned Ticket is at `needs_kickoff`, has status `awaiting_approval` or `empty`, and has no
  `employee_session_id`, render the existing `EnumPill` with `keyLabel="worker"`, the stored value
  preselected, and only served catalog options;
- save a changed value through the dedicated endpoint and existing `ticketChanged` mutation effect;
- otherwise render the same value in the existing `Pill` with `keyLabel="worker"` and no select.

Add stable data attributes for runtime assertions, not styling. Make no change to `EnumPill`, shared
tokens/CSS, route layout, cards, transcript rendering, or other pages. A hidden contradictory binding
can still lose a race after render; the server rejects it and remains canonical.

The current pane attaches on mount, so the Ticket route must explicitly defer only its pristine
Kickoff conversation:

- derive the same pristine predicate used for the selector and pass `deferInitialAttach` through
  `TicketRoute.svelte` → `AcpConversation.svelte` → `AcpConversationPane.svelte`;
- while deferred, subscribe and render the existing rail/composer but do not call `controller.attach()`;
  when Kickoff ceases to be pristine, the pane reactively calls it once;
- add `deferInitialAttach` to the controller options. Its first `prompt` demand stores one exact pending
  content/choice/client-message tuple, calls the existing idempotent `attach`, and sends that tuple
  exactly once after the first admitted contiguous `ready`. Reconnect retains it; dispose clears it;
  a second submit before ready rejects locally rather than being lost;
- change only the existing `AcpComposer` callback result so `ChatComposer` retains text when that local
  rejection occurs. Chief, Board, already-started Tickets, automatic demand, and every already-attached
  controller keep eager behavior.

This is demand deferral, not an unbound websocket mode and not a second conversation state machine.
The server remains the first-binding CAS authority. A first prompt freezes the then-stored key; a
successful selector save before that prompt changes which registered child the ordinary attach creates.

## Exact implementation files

### Product source

- `src/planner/conversation/backend_catalog.py` (new)
- `src/planner/conversation/__init__.py`
- `src/planner/conversation/composition.py`
- `src/planner/conversation/employee_registry.py`
- `src/planner/conversation/sqlite_binding_repository.py`
- `src/planner/runtime/acp_step_gateway.py`
- `src/planner/core/db.py`
- `src/planner/core/server.py`
- `src/planner/worker_types/contracts.py`
- `src/planner/worker_types/registry.py`
- `src/planner/worker_types/configuration.py`
- `src/planner/worker_types/coding.py`
- `src/planner/worker_types/exploration.py`
- `src/planner/worker_types/initiative_planning.py`
- `src/planner/worker_types/new_worker.py`
- `src/planner/tickets/contracts.py`
- `src/planner/tickets/data.py`
- `src/planner/tickets/actions.py`
- `src/planner/tickets/api.py`
- `src/planner/tickets/views.py`
- `src/planner/sprints/views.py`
- `src/planner/cli/main.py`
- `src/planner/seed/importer.py`
- `src/planner/seed/__main__.py`
- `web/src/lib/types.ts`
- `web/src/lib/lifecycle.ts`
- `web/src/lib/acp/conversationController.ts`
- `web/src/components/AcpConversation.svelte`
- `web/src/components/acp/AcpConversationPane.svelte`
- `web/src/components/acp/AcpComposer.svelte`
- `web/src/routes/TicketRoute.svelte`

`web/src/lib/resourceCatalogue.ts`, `src/planner/core/contracts.py`, discovery/runner logic, and ACP
wire/broker/transcript rendering are read-only references for this ticket; the existing event kind and
routing rule are sufficient.

### Focused tests and support

- `tests/support/probe.py`
- `tests/support/acp_e2e_server.py`
- `tests/unit/test_worker_type_registry.py`
- `tests/unit/test_worker_type_manifest_endpoint.py`
- `tests/unit/test_engine_parameterization.py`
- `tests/unit/test_external_work_generic.py`
- `tests/unit/test_probe_type.py`
- `tests/unit/test_new_worker_type.py`
- `tests/unit/test_db.py`
- `tests/unit/test_tickets_engine.py`
- `tests/unit/test_ticket_edit_api.py`
- `tests/unit/test_chief_external_work.py`
- `tests/unit/test_seed.py`
- `tests/unit/test_copy_text_type_driven.py`
- `tests/unit/test_board_view.py`
- `tests/unit/test_sprint_views_type_driven.py`
- `tests/unit/test_acp_binding_repository.py`
- `tests/unit/test_acp_employee_registry.py`
- `tests/unit/test_acp_conversation_composition.py`
- `tests/unit/test_acp_step_gateway.py`
- `tests/unit/test_frontend_event_mapping.py`
- `web/tests/lifecycle.test.mjs`
- `web/tests/resource-catalogue.test.mjs`
- `web/tests/conversation-controller.test.mjs`
- `web/tests/acp-components.test.mjs`
- `tests/e2e/test_cli_verbs.py`
- `tests/e2e/test_chief_external_work_cli.py`
- `tests/e2e/test_acp_conversation.py`

The no-default DDL requires explicit `employee_backend` in raw-SQL Ticket fixtures. The mechanical
fixture allowlist is:

- `tests/unit/test_conversation_hub.py`
- `tests/unit/test_days.py`
- `tests/unit/test_hermes_backend_ticket_composition.py`
- `tests/unit/test_hermes_backend_ticket_step_composition.py`
- `tests/unit/test_links.py`
- `tests/unit/test_sprints.py`
- `tests/unit/test_worker_type_persistence.py`
- `tests/unit/test_worker_type_stage_contracts.py`

ACP-06 may delete the two `test_hermes_backend_*` compatibility paths above before implementation;
if so, they leave the allowlist rather than being restored. Raw fixture edits add only the explicit
column/value and adjust exact row/event assertions; they do not widen those tests.

### Live documentation and ticket evidence

- `docs/worker-types.md`
- `docs/tickets-and-gates.md`
- `docs/employee-runtime.md`
- `docs/frontend.md`
- `docs/cli.md`
- `orchestration/tickets/acp-07-worker-backend-selection/implementation-report.md`
- `orchestration/tickets/acp-07-worker-backend-selection/focused-checks.txt`
- `orchestration/tickets/acp-07-worker-backend-selection/implementation-review.md`
- `PROGRESS.md`
- `decisions.md`

Do not edit generated `web/dist`, package/dependency files, shared styles/tokens, or any Codex/Claude
definition file in this slice.

## Red/green test sequence

### 1. Catalog and Worker-type contract

Write these first:

- `test_employee_backend_catalog_rejects_empty_duplicate_and_runtime_key_mismatch` in
  `test_acp_conversation_composition.py`;
- `test_worker_profiles_require_non_empty_registered_default_employee_backend` and
  `test_probe_default_employee_backend_is_registered_second_catalog_entry` in
  `test_worker_type_registry.py`;
- `test_worker_type_manifest_serves_exact_defaults_and_ordered_employee_backend_catalog` in
  `test_worker_type_manifest_endpoint.py`.

Make them green with the catalog, required WorkerProfile field, registry validation, all explicit
definition values, and manifest plumbing. The shipped order is initially `['hermes']`; the probe
response preserves its injected two-key order and reports `probe-backend` as the probe default.
The probe installs one `ConfiguredEmployeeRuntimeDefinitions` pair atomically; assert its manifest,
Ticket writers, binding repository, and configured registry all hold those exact paired objects, then
restore the previous pair in one operation.

### 2. Schema and creation boundaries

Add to `test_db.py`:

- `test_fresh_v26_ticket_employee_backend_is_not_null_and_has_no_default` (an omitted/null insert
  fails);
- `test_v25_to_v26_assigns_exact_hermes_and_preserves_ticket_bytes`;
- `test_v26_migration_rejects_non_hermes_existing_binding_without_replacing_ticket_table`;
- `test_v26_reopen_preserves_non_hermes_selection_and_is_idempotent`.

Add focused creation cases proving default/override/unknown-before-mutation for ordinary data/action
and HTTP paths in `test_tickets_engine.py` and `test_ticket_edit_api.py`; external-work equivalents in
`test_chief_external_work.py`; seed default/override/unknown rollback and argument forwarding in
`test_seed.py`; and CLI flag/body forwarding in the two CLI e2e files. Assert exact stored rows,
returned values, `ticket_created` payloads, wake behavior, and zero rows/events for rejection.

Update Board, Sprint-light-row, copy-text, and lifecycle/manifest tests to assert the exact key crosses
every read projection. Add startup-audit cases for blank/unregistered storage; do not turn them into
fallback tests.

### 3. Canonical edit and binding race

Add a concentrated matrix in `test_ticket_edit_api.py` (or a new class in that exact file):

- both pristine statuses permit a different registered key;
- same-value input leaves row bytes, `updated_at`, and event count unchanged, including after freeze;
- every other status, every other stage, a non-null session mirror, and a binding row reject a real
  change;
- empty/unknown candidates reject without mutation;
- the endpoint is direct-only and returns the updated Ticket;
- two latch-controlled connections exercise writer-first and binding-first serializations and assert
  there is never a stored/bound backend mismatch.

Extend `test_acp_binding_repository.py` for stored-selection resolution, unregistered stored data,
first CAS equality, existing-binding mismatch, replacement backend preservation, stale-winner
validation, explicit Chief default, and registration-based `is_backend_available`. Extend
`test_acp_employee_registry.py` and `test_acp_conversation_composition.py` to prove the registry gets
all and only catalog registrations in stable order. Extend `test_acp_step_gateway.py` for permission
settlement failure on stored-selection/binding mismatch.

### 4. Resource and real Ticket-route behavior

Add a resource-catalogue case for a `ticket_updated` event whose field is `employee_backend`; assert
only the existing `ticket:<id>`, Board, and current-Sprint dependency set. The Python completeness
wrapper remains green without a new `EventKind`.

In `test_acp_conversation.py`, use the real Vite Ticket route and a two-entry scripted catalog:

- `test_ticket_route_worker_selector_is_preselected_catalog_only_and_persists` checks the existing
  fresh `awaiting_approval` Ticket and Kickoff proposal, facts line, `worker` label, served options/order,
  selected stored value, one dedicated PUT, and the refetched value without layout/card additions. It
  asserts route observation and selector save leave both binding and session mirror absent;
- `test_ticket_route_worker_selector_freezes_after_binding_or_kickoff_advance` proves the selector is
  replaced by a non-editable pill after either freeze boundary. First prompt creates exactly one binding
  using the saved fake key; Kickoff advance independently enables exactly one ordinary attach;
- `test_fake_non_hermes_human_and_automatic_step_share_backend_and_session` selects the second fake
  backend, advances Kickoff, sends and settles one human Ticket prompt, triggers the real
  `EmployeeStepRunner`, and asserts both prompts hit that registration and one durable ACP session,
  while the Ticket mirror and binding row carry the same session/backend. The manifest default, stored
  override, binding, human prompt, and automatic prompt all come from the single installed pair.

Use the existing scripted official-SDK child and audit hooks; do not create a second gateway or bypass
`ConversationHub` for either prompt.

Add controller/component cases proving deferred mount opens no transport, first prompt attaches and is
delivered once after ready, a second pre-ready submit is retained by the composer, reconnect does not
duplicate the pending prompt, dispose drops it, and changing the prop after Kickoff calls idempotent
attach. Existing eager attach cases remain byte-for-byte behavioral controls.

## Focused commands

Run after the implementation is complete, not between mechanical edits:

```bash
.venv/bin/ruff check \
  src/planner/conversation/backend_catalog.py \
  src/planner/conversation/composition.py \
  src/planner/conversation/employee_registry.py \
  src/planner/conversation/sqlite_binding_repository.py \
  src/planner/runtime/acp_step_gateway.py \
  src/planner/core/db.py src/planner/core/server.py \
  src/planner/worker_types src/planner/tickets \
  src/planner/sprints/views.py src/planner/cli/main.py src/planner/seed \
  tests/support/probe.py tests/support/acp_e2e_server.py \
  tests/unit/test_worker_type_registry.py \
  tests/unit/test_worker_type_manifest_endpoint.py \
  tests/unit/test_db.py tests/unit/test_tickets_engine.py \
  tests/unit/test_ticket_edit_api.py tests/unit/test_chief_external_work.py \
  tests/unit/test_seed.py tests/unit/test_copy_text_type_driven.py \
  tests/unit/test_board_view.py tests/unit/test_sprint_views_type_driven.py \
  tests/unit/test_acp_binding_repository.py tests/unit/test_acp_employee_registry.py \
  tests/unit/test_acp_conversation_composition.py tests/unit/test_acp_step_gateway.py \
  tests/unit/test_frontend_event_mapping.py \
  tests/e2e/test_cli_verbs.py tests/e2e/test_chief_external_work_cli.py \
  tests/e2e/test_acp_conversation.py

.venv/bin/mypy --strict \
  src/planner/conversation/backend_catalog.py \
  src/planner/conversation/composition.py \
  src/planner/conversation/employee_registry.py \
  src/planner/conversation/sqlite_binding_repository.py \
  src/planner/runtime/acp_step_gateway.py \
  src/planner/core/db.py src/planner/core/server.py \
  src/planner/worker_types src/planner/tickets \
  src/planner/sprints/views.py src/planner/cli/main.py src/planner/seed

.venv/bin/python -m pytest \
  tests/unit/test_worker_type_registry.py \
  tests/unit/test_worker_type_manifest_endpoint.py \
  tests/unit/test_engine_parameterization.py \
  tests/unit/test_external_work_generic.py \
  tests/unit/test_probe_type.py tests/unit/test_new_worker_type.py \
  tests/unit/test_db.py tests/unit/test_tickets_engine.py \
  tests/unit/test_ticket_edit_api.py tests/unit/test_chief_external_work.py \
  tests/unit/test_seed.py tests/unit/test_copy_text_type_driven.py \
  tests/unit/test_board_view.py tests/unit/test_sprint_views_type_driven.py \
  tests/unit/test_acp_binding_repository.py tests/unit/test_acp_employee_registry.py \
  tests/unit/test_acp_conversation_composition.py tests/unit/test_acp_step_gateway.py \
  tests/unit/test_frontend_event_mapping.py

node web/tests/lifecycle.test.mjs
node web/tests/resource-catalogue.test.mjs
node web/tests/conversation-controller.test.mjs
node web/tests/acp-components.test.mjs
npm --prefix web run check

.venv/bin/python -m pytest \
  tests/e2e/test_cli_verbs.py \
  tests/e2e/test_chief_external_work_cli.py \
  tests/e2e/test_acp_conversation.py
```

Build only to a temporary output directory if a browser test needs Vite production assets; do not
modify tracked `web/dist`. Do not run `./verify`: ACP-10 owns the one final canonical gate.

## Integration and review order

1. Land ACP-06 and independently confirm its ownership classification/deletions. Rebase this plan's
   implementation branch and remove only file entries ACP-06 actually deleted; do not bring legacy
   chat/relay code back to satisfy old fixtures.
2. Implement this generic catalog/schema/selection slice serially. Run the focused checks above, one
   independent plan-aware diff review, and one narrow correction pass only if that review finds a
   concrete defect. Spot-check the v26 parser/rebuild and the writer-versus-binding CAS race directly.
3. Freeze the catalog API and single tuple entry point. The Codex and Claude definition authors may
   then work in parallel only in their distinct definition/strategy/conformance files.
4. Integrate backend registrations serially at the shared tuple: Codex first, producing stable order
   Hermes/Codex; Claude second, producing Hermes/Codex/Claude. Each backend ticket repeats the real
   human Ticket plus Automatic Employee-step proof through Computer Use. Neither may change the
   generic Ticket schema, selector, repository rules, or invent a backend-specific UI branch.
5. ACP-10 performs the final cross-backend audit, computer-use matrix, docs check, and sole
   canonical `./verify`.

## Explicit non-goals

No model selector, Worker-type change, workflow/status/eligibility change, Chief selector, backend
migration action, binding deletion, history transfer, session remint, Gemini entry, ACP wire update,
transcript redesign, shared CSS/token change, settings page, card, or backend-specific UI copy is
allowed. Existing sessions may be cleared only by the separately approved one-time cutover; this
ticket never clears them as a side effect of selection.
