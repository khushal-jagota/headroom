# ACP-06 legacy conversation deletion

Status: **COMPLETE — implementation review READY and live schema-v25 cutover passed 2026-07-20**.

This ticket executes the one-way ACP cutover defined by
`orchestration/acp-migration/chat-ownership-classification.md`. It removes the old Panels Chat,
raw Hermes, relay, neutral-envelope, adapter, and duplicate transcript/session systems. It keeps
only the ACP conversation, automatic Employee-step correctness, pending worker-context delivery,
Ticket file safety, and the Ticket ACP session mirror.

## Hard gate

No product, test, schema, generated build, or live database deletion may begin until the
orchestrator records that:

1. ACP-05 real Computer Use against `http://127.0.0.1:8767` passed its full Hermes matrix, including
   corrected Stop, Send Now, compaction, permission, restart, Ticket conversation, and automatic
   Employee-step behavior; and
2. the five named worker-context delivery proofs in the ownership classification are green.

Planning and review may finish before that gate. Passing focused unit tests alone is not deletion
authorization. The cutover has no compatibility mode, no legacy-session preservation, no Gemini
work, and no write in `~/.hermes/hermes-agent`.

Gate record (2026-07-20): the five worker-context delivery proofs passed in the settled 49-test
gateway/composition/official-SDK set. Real Panels then passed the remaining ACP-05 matrix and its final
compaction gate: visible start, durable Chief generation 12 -> 13, content-free completion after hard
reload and full server restart, and a later exact-response prompt in the same replacement session. The
accepted ownership-closure addendum is present in the authoritative classification. Deletion is now
authorized exactly to this contract and its reviewed plan.

## End-state invariants

1. `/api/conversation` is the only browser conversation route. `ConversationComposition`,
   `ConversationHub`, `ConversationTurnBroker`, `ConversationPermissionBroker`,
   `AcpEmployeeRegistry`, and `SqliteConversationBindingRepository` are the only conversation and
   child/session composition path.
2. Chief and Ticket routes mount the existing restrained Panels ACP pane. This ticket does not
   redesign it, adopt ACP UI styling, or change public ACP wire contracts.
3. Automatic work remains eligibility -> Ticket claim -> prompt construction -> `AcpStepGateway`
   -> the same selected employee/session -> proposal/Ticket settlement.
4. `employee_step_runs` is product correctness state only. It stores no prompt, reply, transcript,
   usage, activity, image, tool, clarification, or browser state.
5. `pending_worker_context` remains owned by `planner.worker_context`; only `AcpStepGateway` prepares
   its exact model text and acknowledges exact revisions after ACP admission.
6. `conversation_session_bindings` is the durable ACP binding owner. A Ticket additionally mirrors
   its exact session in `tickets.employee_session_id`; the Chief has no second mirror.
7. There is no `src/planner/chat`, `src/planner/minds`, `src/planner/hermes_backend`,
   `src/planner/core/adapters`, legacy browser pane/client/resource, or legacy config switch after
   the ticket.

## Frozen Employee-step replacement

Create `src/planner/runtime/employee_step_repository.py` with:

- immutable `EmployeeStepRun` using the exact fields `employee_step_id`, `ticket_id`, `status`,
  `employee_session_id`, `error`, `started_at`, `updated_at`, and `completed_at`;
- `EmployeeStepStatus = Literal["running", "complete", "interrupted", "errored"]`; and
- transaction-bound `SqliteEmployeeStepRepository` operations for start, read-running,
  bind-session, replace-running-for-restart, first-wins terminal settlement, running-existence
  guards, and stale-handoff interruption.

The repository is the sole SQL owner of `employee_step_runs`. Operations used inside a Ticket,
binding, permission, or migration transaction accept that caller's existing SQLite connection; no
second connection may weaken the current immediate-transaction proof.

The table is exactly:

```sql
CREATE TABLE employee_step_runs (
  employee_step_id    TEXT PRIMARY KEY,
  ticket_id           TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
  status              TEXT NOT NULL
                      CHECK (status IN ('running','complete','interrupted','errored')),
  employee_session_id TEXT,
  error               TEXT,
  started_at          INTEGER NOT NULL,
  updated_at          INTEGER NOT NULL,
  completed_at        INTEGER
);
CREATE UNIQUE INDEX idx_employee_step_runs_one_running
  ON employee_step_runs(ticket_id) WHERE status = 'running';
```

No other column or index is permitted. The repository stores the runtime's existing error string;
this ticket adds neither a second diagnostic payload nor a SQL length check.

Initial start and restart replacement append the sole new canonical event
`employee_step_started` with exact payload `{"employee_step_id":"<id>"}`. Restart interruption
settles the old row first and creates one replacement carrying the same exact Employee session.
Terminal settlement is first-wins. No finish event is added.

`EmployeeStepRunner`, `AcpStepGateway.guard_worker_permission_settlement`, automatic eligibility,
paired-stage event lookup, startup stale-handoff cleanup, and Ticket transition/return/delete guards
must use this repository. Ticket deletion rejects one running record, then the declared foreign-key
cascade removes terminal history. None may query generic chat state.

## Runtime port cleanup

Move the useful legacy result concepts into `src/planner/runtime/step_gateway.py` and name them for
their actual owner:

- `EmployeeStepRunStatus = Literal["complete", "interrupted", "errored"]`;
- `EmployeeStepRunResult(status, employee_session_id, error)`; and
- `EmployeeStepGatewayBusy(employee_session_id)`.

Remove `OnEvent`, `text`, `usage`, `session_key`, and raw Hermes types from the runtime port.
`EmployeeStepRunner` never receives or stores ACP transcript text; the browser already receives the
typed ACP stream. Remove the Hub's `_WorkerCollector`, `_worker_collectors`,
`_completed_worker_text`, and install/finish/take methods with their collector-only tests.

`AcpStepGateway` still returns the exact terminal status/session/error and still owns pending
worker-context prepare/admit/acknowledge. Its permission guard proves exactly one running
`employee_step_runs` row with the same Ticket and ACP session while `BEGIN IMMEDIATE` is held.

## Schema-v25 one-time cutover

ACP-06 owns schema version 25. ACP-07's existing selector plan must therefore retarget its Ticket
backend-column migration to version 26 before implementation.

`create_schema` captures the incoming `user_version` before it executes canonical DDL or migration
helpers. On `user_version < 25`, it suppresses pre-transaction creation of `employee_step_runs` and
its index and does not call the legacy `_migrate_conversation_session_bindings`
backfill/validation path. After only the required sealed historical Ticket normalizers have run,
one terminal `BEGIN IMMEDIATE` transaction owns the complete v25 cutover and durable version marker:

1. Ensure `conversation_session_bindings.compaction_boundaries_json` exists with the canonical non-null
   empty-array default before reading or deleting binding rows. A direct pre-column v24 -> v25 path adds
   and backfills it inside this transaction; an ACP-05-amended dogfood v24 path validates and preserves
   the existing column and values. Fresh v25 DDL includes the column. This is the terminal-migration
   counterpart of ACP-05's no-version-bump v24 amendment.
2. Create `employee_step_runs` and its one partial index.
3. Copy only `chat_turns.origin='worker' AND mode='worker_step'`. Map
   `id/entity_id/session_key` to `employee_step_id/ticket_id/employee_session_id`; preserve terminal
   status, error, and timestamps. A copied running row becomes `interrupted` with `updated_at` and
   `completed_at` equal to its existing `updated_at`.
4. Rewrite only matching worker-step `chat_turn_started` events in place to
   `employee_step_started`, preserving event id, entity, time, and ordering and replacing the payload
   with the exact new payload. Delete human/generic chat events and all
   `chat_session_created`, `chat_message_recorded`, `chat_turn_updated`, and
   `chat_turn_finished` events.
5. Release every `agent_running_step` Ticket to `empty` without a synthetic event or timestamp
   change, then set every `tickets.employee_session_id` to `NULL`.
6. Delete every existing `conversation_session_bindings` row only after the column exists and any
   pre-existing provenance has passed validation.
7. Remove `days.chat_session_key`; drop `chat_turn_activity_entries`, `chat_messages`, `chat_turns`,
   and `agent_chat_sessions` and all their indexes.
8. Set `PRAGMA user_version=25` and commit. Any failure before commit rolls back the column add/backfill,
   new table/index,
   conversions, resets, drops, and version marker together.

Fresh schema contains only the new table and no legacy chat/agent-session table or Day chat column.
Reopening version 25 never repeats the reset and never calls a helper that queries the dropped
`agent_chat_sessions` table. The first later Ticket or Chief demand creates a fresh generation-1 ACP
binding; Ticket CAS writes its mirror, while Chief writes only the binding row. No old Chief/Ticket
session is backfilled, resumed, or reminted on load.

The sealed `_rewrite_ticket_employee_session_events` recognition of historical
`chat_session_created` remains only where an older Ticket migration still needs it. It is not a live
event kind or writer.

## Exact keep / rehome / delete allowlist

### Keep in place

- `src/planner/conversation/**` and `/api/conversation`, except the explicit legacy imports and
  worker-text collector removal above.
- `src/planner/worker_context/**`, `src/planner/tickets/worker_context.py`, and
  `pending_worker_context` unchanged.
- `tickets.employee_session_id`, `EmployeeSessionIdTransition`,
  `write_employee_session_id_in_transaction`, `claim_running_step_employee_session_id`,
  `employee_session_changed`, internal one-owner session reads, and
  `/tickets/{ticket_id}/worker-self`.
- `src/planner/runtime/automatic_employee_step_discovery_loop.py`, eligibility wake, Ticket
  proposals/status settlement, and existing planning-date/ownership logic.
- generic and Ticket-file path safety/routes, ACP typed inline images, ACP generic/Ticket preview,
  `MarkdownBlock`, existing tokens/layout, and all `web/src/vendor/acp-components-core/**`.
- existing ACP conformance, registry, hub, broker, permission, reverse-service, composition,
  binding, websocket, browser, and production-mount tests except collector-only assertions.

### Rehome before deleting the old owner

- Move the non-smoke contents of `src/planner/minds/config.py` to
  `src/planner/conversation/hermes_backend_configuration.py`. Preserve Hermes Python/home
  resolution, source-root calculation, skill provisioning, constants, and tests. Delete
  `boot_smoke_check` and raw-child spawn constants.
- Move `CHIEF_OF_STAFF_ENTITY_ID` to `src/planner/conversation/contracts.py`.
- Move/trim `RunStatus`, `RunResult`, and `SharedGatewayBusy` to the runtime shapes frozen above;
  delete all other `minds/contracts.py` types.
- Move `web/src/components/ChatComposer.svelte` to
  `web/src/components/acp/ConversationComposer.svelte` and trim it to ACP-used text, typed-command,
  picker/paste/drop image, draft, submit, and error behavior. Delete pause, clarification, legacy
  skill-category, and legacy mode branches.
- Move `web/src/lib/chatImages.js` to
  `web/src/lib/acp/pendingConversationImages.js`, renaming symbols from Chat to Conversation.
  Keep only browser `File` intake/lifecycle; there is no upload/store path.
- Replace `web/src/lib/types.ts::CommandCatalog` with a conversation-local command shape sourced
  from ACP `available_commands_update`; do not create a second backend command catalog.

### Delete whole production owners

- `src/planner/chat/**`
- `src/planner/hermes_backend/**`
- `src/planner/minds/**` after the preceding rehomes
- `src/planner/core/adapters/**`
- `src/planner/tickets/employee_session_history.py`
- `src/planner/files/chat_images.py`
- `web/src/components/ChatPanel.svelte`
- `web/src/components/ChiefNeutralPane.svelte`
- `web/src/lib/neutralPane.ts`

Delete the corresponding server/app state, routers, websocket routes, caches, relay tee/pool
teardown, human recovery, old identity routes, employee-session-history route, managed chat-file
routes, chat image upload, API helpers/types/resources/events, relay capability branch, and
chat-file preview branch from the surviving mixed files named in the implementation plan.

Delete config keys and environment parsing for `relay_backend_enabled` /
`PLAN_RELAY_BACKEND_ENABLED`, `gateway_adapter` / `PLAN_GATEWAY_ADAPTER`, the human-chat-only
`run_startup_recovery_in_test_mode` / `PLAN_RUN_STARTUP_RECOVERY_IN_TEST_MODE`, and the unused legacy
`hermes_bin`, `hermes_profile`, and `worker_skill` keys and their `PLAN_*` forms. Keep
`PLAN_HERMES_PYTHON` and `PLAN_HERMES_HOME` in the rehomed ACP Hermes configuration.

Two surviving mixed core files have exact closure edits:

- rewrite only the `/test/run-step` description in `src/planner/core/testmode.py` so it describes
  the composed ACP `EmployeeStepRunner` and scripted ACP backend, with no relay or
  `PoolStepGateway` vocabulary; and
- after the chat-message cleanup branch and employee-session-history caller are removed, delete
  only `LEGACY_EXECUTION_ROUTE_VALUES` and `redact_generated_execution_route_segment` from
  `src/planner/core/legacy_execution_route.py`. Keep `LEGACY_EXECUTION_ROUTE_FIELDS`, which remains
  the historical Ticket-field cleanup recognizer.

### Delete whole legacy test files

- `tests/unit/test_chat_activity.py`, `test_chat_clarification.py`, `test_chat_commands.py`,
  `test_chat_seed.py`, `test_human_chat_turn.py`, `test_employee_session_history.py`,
  `test_employee_child_registry.py`, `test_minds_sessions.py`, `test_pool_step_gateway.py`, and
  `test_raw_frame_request_reply.py`.
- every `tests/unit/test_hermes_backend_*.py` file (the underscore after `backend` is load-bearing;
  keep `test_hermes_acp_backend.py` and `test_hermes_acp_turn_strategy.py`).
- `tests/unit/test_minds.py` only after its live Hermes configuration/skill-provisioning proofs move
  to `test_conversation_hermes_backend_configuration.py`; raw transport, human-message, command,
  and smoke cases are deleted.
- `tests/unit/test_pool_ticket_adoption.py` after its duplicate-owner/CAS proofs move to
  `test_acp_binding_repository.py`.
- `tests/e2e/test_chat_images.py`, `test_chief_neutral_pane.py`,
  `test_ticket_neutral_pane.py`, and `test_live_chat_state.py`.
- `web/tests/neutral-pane.test.mjs` and `web/tests/ticket-neutral-pane.test.mjs`.

## Required route and caller closure

The final source and built artifact contain none of these live surfaces:

- `/api/chat`, `/api/messages/chief`, `/api/relay`, `/tickets/by-live-session`,
  `/tickets/by-employee-session`, employee-session-history, or `/files/chats`;
- `planner.chat`, `planner.minds`, `planner.hermes_backend`, `planner.core.adapters`, `ChatPanel`,
  `ChiefNeutralPane`, `neutralPane`, `SharedGateway`, `PoolStepGateway`, raw-frame/neutral/relay
  vocabulary, `chat_messages`, `chat_turns`, `chat_turn_activity_entries`, or
  `agent_chat_sessions`; and
- legacy Chat resources/events/config keys in Python source, Svelte/TypeScript source,
  `config.yaml`, `web/package.json`, or the newly built `web/dist`.

Historical discussion in `PROGRESS.md`, `decisions.md`, and `orchestration/**` is not rewritten and is
excluded from the absence search. Migration-only recognition of sealed historical event input is the
one documented source exception.

## Acceptance

1. Migration tests prove exact worker-history conversion, running interruption, Ticket release,
   global session/binding reset, old table removal, foreign-key integrity, and idempotent reopen. A
   forced failure after destructive v25 work begins but before the version marker proves rollback
   restores the incoming schema/data/version and leaves no `employee_step_runs` table or index.
   The matrix includes direct pre-column v24 -> v25 and ACP-05-amended dogfood v24 -> v25: both finish
   with the provenance column present on the emptied binding table, the amended path preserves its values
   until the specified row deletion, and rollback restores each incoming column/data shape exactly.
2. `test_employee_step_repository.py` and rewritten runner/eligibility/loops/Ticket tests prove
   admission, exact session binding, restart replacement, first-wins settlement, stale cleanup,
   paired-stage marker, active-run guards, cascade deletion, and no transcript fields.
3. ACP binding/permission tests prove Chief binding-only ownership, Ticket mirror CAS, exact running
   worker epoch, duplicate-session rejection, and fresh generation-1 first demand.
4. Route/import absence tests require `/api/conversation` and worker-self while rejecting every old
   route/import/table/config surface. The served `web/dist` is included.
5. ACP image tests prove ordered typed image blocks reach the SDK and replay/render after refresh;
   browser tests prove no chat upload or managed-chat-file request.
6. Rewritten shutdown and New Worker public-flow tests use production ACP composition and a real
   `EmployeeStepRunner` record, not relay/scripted raw children.
7. Existing ACP Python/e2e/browser suites, Ticket-file security, worker-context, resource
   completeness, Ruff, strict Mypy, Svelte check, frontend tests, and Vite build pass.

This ticket records focused evidence only. The one canonical full `./verify` remains ACP-10's final
frozen-tree gate.

## Documentation

In the same implementation, rewrite `docs/chat.md`, `docs/employee-runtime.md`,
`docs/frontend.md`, `docs/systems.md`, `docs/systems.html`, `docs/tickets-and-gates.md`, and the
`docs/README.md` map entry to the sole ACP end state described by the ownership classification.
History is not retained as a current alternative.

The caller sweep also found live architecture text omitted from the classification table. Subject
to the classification addendum noted below, update `AGENTS.md`, `CLAUDE.md`, and `DESIGN.md` so they
name ACP conversation/session delivery and the ACP pane rather than deleted chat tables or
`ChatPanel`.

## Classification addendum required before implementation

The authoritative classification says no use is unclassified, but the current tree exposes these
four omissions:

1. Hub `_WorkerCollector`/worker-text accumulation exists only to return legacy transcript text from
   `AcpStepGateway`; it has no owner once `RunResult.text` is removed.
2. `run_startup_recovery_in_test_mode`, `hermes_bin`, `hermes_profile`, and `worker_skill` are
   legacy/unused config fields not named by the config table.
3. `test_minds.py` contains live Hermes path/home/skill-provisioning tests that must move before the
   file is deleted, not only the worker-context cases named by the classification.
4. `AGENTS.md`, `CLAUDE.md`, and `DESIGN.md` describe `chat_messages`/`chat_turns` or `ChatPanel` as
   current architecture.

The orchestrator must accept these as a narrow classification correction (or amend the ownership
artifact) before source implementation. They do not change product behavior or broaden ACP-06; they
close deletion ghosts already present in the classified owners.
