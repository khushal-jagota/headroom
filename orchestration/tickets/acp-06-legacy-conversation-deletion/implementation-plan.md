# ACP-06 legacy conversation deletion — implementation plan

## Start condition

Do not execute this plan until the ACP-05 gate in `contract.md` is recorded green and the
classification addendum is accepted. At start, re-read the current ACP-05 corrections so this
deletion does not remove or reintroduce a settled behavior.

The change is a one-way cutover. Do not add shims, feature flags, old-session import, transcript
fallback, Gemini, or a second browser/backend protocol.

## Parallel shape

After the gate, two writers may proceed because their product files are disjoint:

- **Runtime/schema lane:** Python migration, Employee-step repository/runtime, server/routes/config,
  identity/files, Python tests.
- **Frontend lane:** Svelte/TypeScript rehome/deletion, CSS pruning, browser tests, package script.

Do not let both lanes edit `web/dist`, `PROGRESS.md`, `decisions.md`, or this ticket directory.
Integrate runtime first, frontend second, then assign one documentation pass against the settled
tree. Build `web/dist` once after both source lanes are integrated. The orchestrator performs one
focused review of the integrated ACP-06 diff; do not multiply review rounds without a concrete
finding.

## Phase 1 — freeze the replacement and migration tests

Before deleting an owner, add the tests that prove its retained responsibility has moved:

1. Add `tests/unit/test_employee_step_repository.py` for the exact eight-field row, one-running
   index, start event, session bind, restart replacement, first-wins settlement, stale-handoff
   interruption, and running/deletion guard.
2. Extend `tests/unit/test_db.py` with one v24 fixture containing:
   - human chat/message/activity/clarification rows;
   - complete, errored, and running worker-step rows;
   - matching and nonmatching chat events;
   - Day and Chief legacy sessions;
   - Ticket session mirrors and ACP bindings; and
   - at least one `agent_running_step` Ticket without a usable running row.
   Assert the exact v25 result and an idempotent second `create_schema`. Add a forced-failure case
   that fails after at least one reset/drop has executed but before `PRAGMA user_version=25`; assert
   rollback preserves the original legacy tables/rows/events, Ticket session/status values, binding
   rows, and incoming `user_version`, with no `employee_step_runs` table or index left behind.
3. Rewrite `tests/unit/test_chat_ingress_contract.py` as a source/router/schema absence contract.
   Require `/api/conversation` and `/tickets/{ticket_id}/worker-self`; reject every route/import/
   table/config/build token listed in `contract.md`.
4. Add a structural worker-boundary assertion that `EmployeeStepRun` has exactly the frozen fields
   and cannot be projected as history or conversation content.

These tests may initially fail. Do not weaken them to preserve an old caller.

## Phase 2 — rehome live primitives before deleting packages

1. Create `src/planner/conversation/hermes_backend_configuration.py` from the live, non-smoke part
   of `minds/config.py`. Update `conversation/composition.py`, ACP composition/core-loop tests, and
   `test_initiative_planning_worker_type.py` imports. Move the live path/home/skill-provisioning tests
   from `test_minds.py` into new
   `tests/unit/test_conversation_hermes_backend_configuration.py`. This conversation-owned filename
   is intentionally outside the deleted `test_hermes_backend_*.py` glob.
2. Move `CHIEF_OF_STAFF_ENTITY_ID` into `conversation/contracts.py` and update the ACP binding,
   trusted-ingress, e2e fixture, and retained tests. Delete all old-only imports rather than redirect
   them.
3. Rewrite `runtime/step_gateway.py` with the frozen ACP-only result/status/busy shapes. Update
   `runtime/acp_step_gateway.py`, `runtime/employee_step_runner.py`, and their tests. Remove the
   `on_event` argument and rename session fields to `employee_session_id`.
4. Remove worker text collection from `conversation/hub.py` and `AcpStepGateway`. Rewrite the
   requested-cancel/e2e checks to assert typed browser boundaries and terminal status rather than an
   internal text accumulator.

Do not delete `src/planner/minds/**` until all retained imports and tests have moved.

## Phase 3 — install `employee_step_runs` and cut runtime callers over

1. Implement `runtime/employee_step_repository.py` to the frozen record and transaction-bound API.
   Keep all SQL in that owner.
2. Replace `chat_data`/`chat_service` use in `EmployeeStepRunner`:
   - initial and revision paths create one running Employee-step row before ACP submission;
   - the session callback binds the exact row after the Ticket mirror claim succeeds;
   - restart recovery validates and replaces the exact running row/session;
   - busy, lost claim, complete, interrupted, errored, shutdown, and late completion each settle at
     most once and preserve existing Ticket-status outcomes; and
   - no prompt/reply/activity text is written to product state.
3. Point the ACP permission guard at one exact running Employee-step row inside its existing immediate
   transaction.
4. Replace the running row and `chat_turn_started` reads in automatic eligibility with
   `employee_step_runs` and `employee_step_started`.
5. Replace stale worker cleanup in `core/loops.py` with repository settlement and remove output-role
   logic.
6. Replace the active-run checks in `tickets/data.py` for direct Stage resolution, return for
   revision, external-work reconciliation, and permanent deletion. Let Ticket deletion cascade
   terminal Employee-step rows and continue deleting pending worker context explicitly.

Update these behavior suites in this phase:

- `tests/unit/test_employee_step_runner.py`
- `tests/unit/test_acp_step_gateway.py`
- `tests/unit/test_automatic_employee_step_eligibility.py`
- `tests/unit/test_automatic_employee_step_discovery_loop.py`
- `tests/unit/test_core_loops.py`
- `tests/unit/test_return_for_revision.py`
- `tests/unit/test_ticket_delete.py`
- `tests/unit/test_chief_external_work.py`
- affected Ticket stage/ownership tests that seed a running chat row

The old prompt/reply-visible-in-chat assertion becomes a boundary proof that the exact prompt went
through ACP while the correctness row contains no transcript.

## Phase 4 — implement the versioned reset and remove legacy schema/events

1. Capture the incoming `user_version` before canonical DDL or migration helpers run, then bump
   `SCHEMA_VERSION` to 25. For incoming versions below 25, suppress the canonical pre-transaction
   `employee_step_runs` table/index statements and skip `_migrate_conversation_session_bindings`
   entirely; that legacy helper must neither backfill data that this cutover resets nor run on a v25
   reopen against the dropped `agent_chat_sessions` table. Remove chat/agent-session DDL and Day
   `chat_session_key` from fresh schema.
2. Run only the required sealed historical Ticket normalizers, then enter one guarded
   `BEGIN IMMEDIATE` transaction. That single transaction creates the Employee-step table/index,
   converts worker rows/events, resets Ticket status/session mirrors and all ACP bindings, drops the
   legacy Day column/tables/indexes, runs its integrity checks, sets `PRAGMA user_version=25`, and
   commits. No v25 DDL, reset, destructive drop, or durable version assignment happens outside it;
   any failure rolls the complete cutover back.
3. Remove `_migrate_chat_turn_recovery_column`, `_migrate_chat_turn_pending_clarification`, chat
   indexes, and the chat-message branch of `_cleanup_legacy_execution_route_records`.
4. Remove live chat event members from `core/contracts.py`; add only `employee_step_started`.
   Retain sealed historical event recognition inside the old Ticket migration without exposing a
   live writer.
5. Remove Day chat field materialization/API output from `days/contracts.py`, `days/data.py`, and
   `days/api.py` and correct `test_day_api.py`/migration expectations.
6. Rewrite resource completeness expectations so the new Employee-step start event invalidates its
   Ticket aggregates and no deleted chat event/resource remains.

Do not combine this with ACP-07's `employee_backend` column. Record for ACP-07 that its planned v25
migration is now v26.

## Phase 5 — delete server transports, adapters, and identity fallbacks

In `core/server.py`:

1. Remove Chat imports/router/lifecycle, role-gateway builders, pool/relay composition, raw shutdown,
   human restart recovery, adapter state, command cache, relay websocket routes, and
   `relay_chief_enabled` meta output.
2. Keep one production `ConversationComposition` and its ACP-first shutdown order. Test mode creates
   it only through `ConversationTestOptions`; ordinary product-route tests need no fake gateway.
3. Change `create_app` to `(config, clock, conn_factory, *, conversation_test_options=None)` and
   update every surviving call site mechanically.

Then:

- remove `build_adapters` from `server_lifecycle/application.py`;
- delete `src/planner/core/adapters/**`, `src/planner/chat/**`, `src/planner/hermes_backend/**`, and
  finally `src/planner/minds/**`;
- delete the five legacy/test config families frozen in `contract.md` from `core/config.py`,
  `config.yaml`, config tests, e2e environment setup, and fixture boilerplate;
- remove `/tickets/by-live-session`, `/tickets/by-employee-session`, and employee-session-history
  from `tickets/api.py`; delete `tickets/employee_session_history.py` and its contracts;
- simplify `cli/main.py::worker_my_ticket` to require nonblank `PLAN_TICKET_ID` and call only
  `/tickets/{ticket_id}/worker-self`; and
- retain internal one-owner Ticket mirror validation and the ACP child `PLAN_TICKET_ID` environment;
- rewrite only `core/testmode.py`'s `/test/run-step` description to name the composed ACP
  `EmployeeStepRunner` and scripted ACP backend, deleting relay/`PoolStepGateway` wording; and
- once both callers are gone, remove only `LEGACY_EXECUTION_ROUTE_VALUES` and
  `redact_generated_execution_route_segment` from `core/legacy_execution_route.py`; retain
  `LEGACY_EXECUTION_ROUTE_FIELDS` for historical Ticket-field cleanup.

Surviving tests needing only the app-factory signature update are exactly the nondeleted files from
this current caller set:

- `tests/e2e/test_acp_conversation.py`, `tests/support/acp_e2e_server.py`;
- `tests/unit/test_acp_conversation_composition.py`, `test_authctx_routes.py`,
  `test_automatic_employee_step_discovery_loop.py`,
  `test_automatic_employee_step_eligibility_actions.py`, `test_chief_external_work.py`,
  `test_core_loops.py`, `test_day_api.py`, `test_go_no_go_gate.py`, `test_projects.py`,
  `test_return_for_revision.py`, `test_review_ticket_decisions_type_driven.py`,
  `test_server_events.py`, `test_server_static_paths.py`, `test_ticket_delete.py`,
  `test_ticket_edit_api.py`, `test_ticket_files.py`, `test_trusted_ingress.py`,
  `test_type_driven_ingress.py`, `test_value_edit_api.py`, `test_worker_cli_identity.py`,
  `test_worker_my_ticket.py`, `test_worker_type_manifest_endpoint.py`, and
  `test_worker_type_stage_contracts.py`.

Behavioral rewrites in that set are limited to deleted chat/relay/config cases. In particular:

- delete the Chat route authorization case from `test_authctx_routes.py`;
- replace Chief HTTP/chat-file trusted-ingress cases with existing unsafe product HTTP and
  `/api/conversation` websocket coverage in `test_trusted_ingress.py`;
- replace running-chat fixtures in external-work/Ticket tests with a running Employee-step row; and
- keep unrelated route, identity, Ticket, file, event, and ingress assertions unchanged.

Rewrite `test_worker_my_ticket.py`, `test_worker_cli_identity.py`, and `test_cli_entrypoints.py` to
cover only `PLAN_TICKET_ID` -> worker-self, missing identity, specialist skill response, missing
Ticket, and duplicate Ticket-mirror ownership rejection.

## Phase 6 — delete managed Chat files and retain ACP inline images

1. Delete `files/chat_images.py`, `ChatFile`, chat directory/resolver helpers, GET `/files/chats`,
   and POST `/api/chat/{entity}/images`.
2. Keep shared managed path validation and Ticket file routes unchanged.
3. Delete old upload/store tests. Rewrite ACP image tests to prove ordered inline ACP content reaches
   the official SDK, survives typed replay/refresh, and renders without any HTTP upload.
4. Remove the `chat-file` target and preview-route branch while preserving external/Ticket/ACP
   generic preview behavior and security tests.

The affected surviving files are:

- `src/planner/files/api.py`, `src/planner/files/contracts.py`,
  `src/planner/files/logic/paths.py`;
- `web/src/lib/filePreview.ts`, `web/src/lib/acp/filePreview.ts`, and
  `web/src/routes/FilePreviewRoute.svelte` only where the deleted target is referenced; and
- `tests/unit/test_ticket_files.py`, `tests/e2e/test_ticket_file_previews.py`, and
  `web/tests/file-preview.test.mjs` only to remove Chat branches, not weaken Ticket security.

## Phase 7 — frontend-only deletion and rehome

The frontend lane may execute in parallel with Phases 2-6 after the hard gate.

1. Move and trim `ChatComposer.svelte` and `chatImages.js` to the exact ACP destinations in the
   contract; update `AcpComposer.svelte`. Accept ACP available commands directly and delete the
   `CommandCatalog` adapter from `lib/types.ts`.
2. Delete `ChatPanel.svelte`, `ChiefNeutralPane.svelte`, and `lib/neutralPane.ts`.
3. Delete legacy chat API helpers/types from `lib/api.ts` and `lib/types.ts`.
4. Remove `panelsChat`, `chatGatewayStatus`, `chatCommands`, their identities, and chat event
   invalidation from `resourceCatalogue.ts`. Keep canonical product cache behavior.
5. Remove relay capability code from `lib/capabilities.ts` and its branch in `App.svelte`; continue
   loading unrelated meta timings.
6. Prune only the legacy-only stylesheet selector groups enumerated in the ownership classification.
   Keep all ACP, Chief/Ticket layout, composer, image, transcript, and generic file-preview selectors.
   Run a selector-to-live-caller search before deleting each group.
7. Rewrite `web/tests/chat-images.test.mjs` as `web/tests/acp-images.test.mjs`; delete neutral tests;
   update resource/file-preview tests and `web/package.json` so `npm test` includes all five ACP
   suites, resources/cache/websocket/lifecycle/markdown/file preview, and ACP images.

No new cards, colors, layout regions, explanatory copy, or visual tokens are authorized.

## Phase 8 — mixed end-to-end tests and legacy suite deletion

1. Delete the whole legacy test files listed in `contract.md` only after destination proofs pass.
2. In `tests/e2e/test_new_worker_public_flow.py`, use production ACP composition and the real
   Employee runner. Preserve Understanding, one post-cutover session shared by human and automatic
   demand, proposal parking, and browser progression.
3. In `tests/e2e/test_flows_a.py`, move live scroll/latest, typed refresh replay, and ACP command-menu
   behavior to ACP tests, then delete ChatPanel/history/offline-adapter assertions.
4. In `tests/unit/test_request_identity.py`, keep request actor/direct-write coverage and delete only
   legacy role-gateway wiring; retain ACP child environment proof.
5. In `tests/unit/test_server_shutdown_process.py`, use the official ACP scripted subprocess and
   assert exact bound Employee-step interruption, no stored partial output, clean deadline exit, and
   no running row.
6. In `tests/unit/test_pool_ticket_adoption.py`, move only durable duplicate-owner/CAS assertions to
   the ACP binding repository suite, then delete the file.
7. Remove legacy environment setup from `tests/e2e/conftest.py`; keep the general server fixture and
   ACP-specific test support.

## Phase 9 — docs, build, and focused evidence

After runtime and frontend source settle:

1. Rewrite the seven live docs named in `contract.md`, then regenerate/update `docs/systems.html`
   from the same settled map.
2. Apply the accepted classification addendum to `AGENTS.md`, `CLAUDE.md`, and `DESIGN.md`.
3. Run caller-closure searches over `src`, `tests`, `web/src`, `web/tests`, `config.yaml`, the live
   docs, and root architecture/design docs. Exclude `PROGRESS.md`, `decisions.md`, and
   `orchestration/**` because they contain history.
4. Run the focused Python repository/migration/runtime/binding/server/identity/file/ACP suites,
   Ruff on changed Python, and strict Mypy on changed source.
5. Run `npm --prefix web test`, `npm --prefix web run check`, and one
   `npm --prefix web run build`. Confirm the newly served `web/dist` contains no legacy token.
6. Run the smallest route/static smoke needed to prove the production app starts with one ACP
   composition and no adapter argument.

Write `implementation-report.md`, `focused-checks.txt`, and one integrated implementation review.
Address or explicitly refute each concrete finding. Do not run `./verify`; ACP-10 owns the single
canonical final run after Codex and Claude are also settled.

## Exact product-file mutation allowlist

Beyond deletions and ticket evidence files, ACP-06 source work is limited to:

- `config.yaml`
- `src/planner/conversation/{__init__,contracts,composition,hub,sqlite_binding_repository}.py`
- `src/planner/conversation/hermes_backend_configuration.py` (new)
- `src/planner/core/{config,contracts,db,legacy_execution_route,loops,server,testmode}.py`
- `src/planner/server_lifecycle/application.py`
- `src/planner/runtime/{acp_step_gateway,automatic_employee_step_eligibility,employee_step_runner,step_gateway}.py`
- `src/planner/runtime/employee_step_repository.py` (new)
- `src/planner/tickets/{api,contracts,data}.py`, `src/planner/cli/main.py`
- `src/planner/days/{api,contracts,data}.py`
- `src/planner/files/{api,contracts}.py`, `src/planner/files/logic/paths.py`
- `web/package.json`, `web/package-lock.json`, generated `web/dist/**`
- `web/src/App.svelte`
- `web/src/components/acp/{AcpComposer,ConversationComposer}.svelte`
- `web/src/lib/{api,capabilities,filePreview,resourceCatalogue,types}.ts`
- `web/src/lib/acp/filePreview.ts`
- `web/src/lib/acp/pendingConversationImages.js` (new)
- `web/src/routes/FilePreviewRoute.svelte`
- `assets/app.css`
- the exact test/doc files named in this plan and contract.

If a new product file outside this allowlist appears necessary, stop implementation and return the
caller evidence to the orchestrator. Do not fit around an unclassified owner.
