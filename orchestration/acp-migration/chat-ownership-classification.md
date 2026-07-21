# ACP-06 chat ownership classification

Status: **authoritative ownership classification; deletion authorized 2026-07-20**. Every ownership
destination and deletion boundary below is frozen. The named worker-context gateway proofs and full
ACP-05 real Panels Computer Use gate are green; ACP-06 may now implement this one-way cutover.

The four classifications used below are the ACP-06 classes:

- **DELETE** — legacy Panels chat, raw Hermes, relay, neutral-envelope, transcript, or duplicate
  session machinery that has no owner after the ACP cutover.
- **RETAIN — PRODUCT WORKER-STEP SETTLEMENT ONLY** — a correctness record or transition needed
  to claim, recover, interrupt, and settle an automatic Employee step. It must move out of the
  `chat` vocabulary and cannot become a transcript, a worker prompt path, a browser transport, or
  a second Hermes client.
- **RETAIN — WORKER-CONTEXT/ACP PROMPT DELIVERY** — keyed product guidance prepared by
  `WorkerContextService`, delivered only by `AcpStepGateway`, and composed only by
  `ConversationComposition`. It is neither transcript nor Employee-step settlement state.
- **MOVE — CONVERSATION/FILES** — durable ACP conversation identity, ACP UI behavior, or managed
  file/image behavior whose owner is the ACP conversation or files system.

No item found in the requested source and test sweep is left unclassified. The live ACP composer
sends typed inline image content blocks and real Hermes image dogfood has passed; the final caller
scan found no ACP/product caller of the old managed chat upload/store/resolver. Those chat-specific
paths are therefore deleted rather than retained for backward compatibility.

## Binding boundary

`AGENTS.md` is decisive: `chat_messages` and `chat_turns` are Panels product-visible state, not
Hermes worker context. Writing either row does not deliver guidance to a worker. Worker guidance
must travel through the actual ACP/Hermes session. Therefore no transcript/history/activity row,
clarification mirror, or HTTP chat turn may survive under the settlement exception.

The surviving split is:

1. `/api/conversation` plus `src/planner/conversation/` owns browser conversation, replay,
   permissions, typed content, child lifetime, and durable ACP bindings.
2. `src/planner/runtime/employee_step_repository.py` owns automatic Employee-step
   admission/recovery and terminal settlement in `employee_step_runs`, exactly as frozen below. It
   stores no prompt, reply, activity stream, image reference, or transcript.
3. `files` retains generic and Ticket-file path/preview safety with live callers. ACP owns inline
   typed image attachment, replay, and rendering; there is no durable managed chat-file subsystem.

## Frozen non-chat Employee-step destination

The replacement is `src/planner/runtime/employee_step_repository.py`, containing the
`EmployeeStepRun` record and transaction-bound `SqliteEmployeeStepRepository`. It is the sole owner of
the `employee_step_runs` table. The exact columns are:

- `employee_step_id TEXT PRIMARY KEY`;
- `ticket_id TEXT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE`;
- `status TEXT NOT NULL` constrained to `running`, `complete`, `interrupted`, or `errored`;
- nullable `employee_session_id TEXT` and nullable bounded `error TEXT`;
- `started_at INTEGER NOT NULL`, `updated_at INTEGER NOT NULL`, and nullable
  `completed_at INTEGER`.

The table has one partial unique index on `ticket_id WHERE status = 'running'`. It has no origin,
mode, phase, activity, output, prompt, reply, clarification, image, or recovery-link column. The
repository exposes exact start, read-running, bind-session, replace-running-for-restart, first-wins
settle, has-running/Ticket-deletion guard, and stale-handoff settlement operations; operations joining a
Ticket or binding accept the caller's existing SQLite connection so the current immediate-transaction
proof is preserved.

The sole new canonical event is `employee_step_started` with payload
`{"employee_step_id":"<id>"}`. Initial start and restart replacement each append it. No finish event
is added: terminal Ticket writers already emit the product doorbells, while terminal run history is
the row. Paired-stage eligibility reads this event instead of `chat_turn_started`.

Exact live callers are `EmployeeStepRunner` (start/restart/bind/stop/terminal settlement),
`AcpStepGateway.guard_worker_permission_settlement` (one running row with the exact ACP session),
automatic Employee-step eligibility (running exclusion and the start marker),
`core/loops.py` (stale-handoff settlement), and Ticket transition/return/delete guards. Ticket
deletion relies on the declared cascade after rejecting a running row; it does not perform optional
chat cleanup.

The ACP-06 terminal migration creates this table and index, copies only
`chat_turns.origin='worker' AND mode='worker_step'`, maps `id/entity_id/session_key` to
`employee_step_id/ticket_id/employee_session_id`, and preserves status/error/timestamps. Matching
worker `chat_turn_started` events are rewritten in place to `employee_step_started` with the exact new
payload while preserving event id/time/order; all human and remaining generic chat events are
deleted. Because the approved session cutover resets every old session, every migrated `running` row
is settled `interrupted` at its existing `updated_at`, and every `agent_running_step` Ticket is
released to `empty` before startup. New post-cutover rows retain the existing restart-recovery
behavior: stale running rows whose Ticket is no longer running settle interrupted first, then running
Tickets recover only against their exact row/session.

## `src/planner/chat/`

The package itself does not survive. The worker-step semantics listed below are reimplemented
under a runtime/product owner; they are not reasons to retain a `chat` module.

| File / symbol | Class | Evidence and destination |
|---|---|---|
| `chat/__init__.py`, `chat/logic/__init__.py` | **DELETE** | Package markers for the retired domain. |
| `chat/contracts.py::ChattableEntityKind` | **DELETE** | Includes `day` and `agent_chat_session`; ACP resolves only Ticket employees and the Chief. |
| `ChatTurnRequest`, `ChatStateMessage`, `ChatActivityObservation`, `ChatActivityEntry`, `ChatPendingClarification`, `ChatTurn`, `ChatTurnOutcome`, `ChatState` | **DELETE** | Shapes the duplicate DB transcript/turn/activity/clarification projection. No ACP caller consumes them. Do not move `output_text`, messages, activity, continuation, pause, or clarification fields into the worker-step record. |
| `HumanChatOutputDelta`, `HumanChatCompletion`, `HumanChatObservation` | **DELETE** | Old iterator transport from `GatewayAdapter.run_human_turn`; ACP notifications are authoritative. |
| `CommandCategory`, backend `CommandCatalog`, `GatewayStatus` | **DELETE** | Old gateway catalogue/status API. The composer’s temporary TypeScript catalogue adapter is separately classified as conversation UI. |
| `chat/logic/activity.py::normalize_gateway_activity` | **DELETE** | Translates raw Hermes events into duplicate Panels activity rows. `AcpStepGateway.run_ticket_step` discards `on_event`; ACP notifications already render activity. |
| `chat/api.py` and `_cached_catalog`, `chat_state`, `start_chat_turn`, `continue_chat_turn`, `start_chief_message`, `pause_chat_turn`, `answer_chat_clarification`, `chat_commands`, `gateway_status` | **DELETE** | Entire duplicate HTTP surface; current Chief, Ticket, and Board routes mount `AcpConversation`. |
| `chat/service.py::_resolve`, `resolve_chattable_entity`, `_validate_chattable_entity_for_state`, `state`, `catalog`, `_AdmittedHumanChatTurn`, `ChatTurnLifecycle`, `_resolve_turn_images`, `_resolve_turn_image`, `_visible_human_text`, `answer_pending_clarification`, `status` | **DELETE** | Human admission, HTTP image prompt assembly, continuation, pause, recovery, command dispatch, clarification, and gateway status all duplicate ACP. |
| `chat/service.py::observe_worker_gateway_event` | **DELETE** | Only turns raw Hermes events into chat activity/output/clarification. ACP step gateway explicitly `del on_event`, so this is already inert on the production path. |
| `start_worker_turn`, `attach_worker_session_key`, `finish_worker_turn`, `fail_worker_turn` | **RETAIN — PRODUCT WORKER-STEP SETTLEMENT ONLY** | Replace exactly with the start/bind/settle operations in `runtime/employee_step_repository.py`; `EmployeeStepRunner` is the caller. No transcript write or `ChatTurn` return type survives. |
| `chat/data.py::_txn`, row projectors, `_append_message`, `record_message`, `read_state`, `record_turn_activity`, `record_pending_clarification`, `read_pending_clarification_answer_target`, `mirror_accepted_clarification_answer`, `start_human_continuation_turn`, `bind_human_turn_session`, `record_agent_session_key`, `append_turn_output` | **DELETE** | These exist for the duplicate transcript, human HTTP turn, activity, clarification, or deleted Chief mirror. Chief binding CAS remains in the conversation repository and writes no replacement mirror. |
| `start_turn`, `start_turn_in_transaction`, `_settle_chat_turn_in_transaction`, `read_active_turn`, `read_running_turn_session_key`, `roll_running_turn_for_recovery`, `attach_session_key`, `settle_chat_turn`, `finish_turn`, `fail_turn` | **RETAIN — PRODUCT WORKER-STEP SETTLEMENT ONLY** | Replace exactly with the frozen `SqliteEmployeeStepRepository` operations. Restart settles the old running row and creates a new row carrying the same exact Employee session; no recovery link, visible role/text, output, activity, or generic origin/mode survives. |
| `write_agent_session_key_in_transaction` | **DELETE** | The approved cutover removes the Chief mirror. Chief session CAS writes only `conversation_session_bindings`; no replacement writer or `chat_session_created` event exists. The Chief employee ID moves to conversation contracts. |

## Schema, migrations, and canonical events

| Table / column / migration / event | Class | Exact caller evidence and migration requirement |
|---|---|---|
| `chat_messages`, `idx_chat_messages_entity` | **DELETE** | Only legacy state/history/image prompt and transcript mirror paths read/write it. Ticket deletion cleanup should stop deleting transcript rows because the table disappears. Do not migrate text. |
| `chat_turn_activity_entries`, `idx_chat_turn_activity_identity`, `idx_chat_turn_activity_order` | **DELETE** | Only legacy activity projection and its tests use it. ACP SDK notifications own live/replayed activity. |
| Generic `chat_turns` and `idx_chat_turns_entity`, `idx_chat_turns_one_running`, `idx_chat_turns_one_recovery` | **RETAIN — PRODUCT WORKER-STEP SETTLEMENT ONLY** | Replace exactly with `employee_step_runs` and its one partial running index. Live callers and migration are frozen in the destination section; every human/message/command row is deleted. |
| `chat_turns.id`, `entity_id`, running/terminal `status`, `session_key`, `error`, `started_at`, `updated_at`, `completed_at` | **RETAIN — PRODUCT WORKER-STEP SETTLEMENT ONLY** | Map exactly to `employee_step_id`, `ticket_id`, `status`, `employee_session_id`, `error`, `started_at`, `updated_at`, and `completed_at` in `employee_step_runs`. No additional column is permitted. |
| `origin`, `mode`, `phase`, `activity_label`, `output_role`, `output_text`, pending clarification columns | **DELETE** | Employee-step-only scope makes origin/mode unnecessary; phase/activity/output/clarification are transcript/UI state. None can be read or replayed by the settlement subsystem. |
| `recovery_of_turn_id` | **DELETE** | It serves human continuation only. Employee restart recovery needs no link: first-wins settlement closes the old row and the replacement is the sole running row for the Ticket. No renamed recovery column is allowed. |
| `_migrate_chat_turn_pending_clarification`, `_migrate_chat_turn_recovery_column`, chat indexes in `_create_indexes` | **DELETE** | Replace with one ACP-06 terminal migration into the dedicated worker-step table, then drop legacy tables/indexes. |
| `_cleanup_legacy_execution_route_records` chat-message redaction branch | **DELETE** | No transcript table remains. Retain the function's separately owned historical execution-route event cleanup; remove only its chat-message branch. |
| `days.chat_session_key` plus `Day.chat_session_key` in `days/contracts.py`, `days/data.py`, and `days/api.py` | **DELETE** | Day is not an ACP employee; the field exists only for retired human chat. Remove from schema, materialization/read/API response, and migration tests. |
| `agent_chat_sessions` and its `chat_session_key` | **DELETE** | There is no renamed or folded Chief mirror. The terminal cutover drops the table. After cutover the Chief is classified by its conversation-owned employee ID and its sole durable session owner is `conversation_session_bindings`. |
| `conversation_session_bindings` | **MOVE — CONVERSATION/FILES** (already correctly owned) | Canonical durable ACP binding for sessions created after the cutover. Ticket rows additionally match `tickets.employee_session_id`; Chief rows have no second product mirror. |
| `_migrate_conversation_session_bindings` | **MOVE — CONVERSATION/FILES** | Keep table creation/validation, but the ACP-06 terminal migration supersedes legacy backfill by deleting every pre-cutover binding. It never reads `agent_chat_sessions` after the cutover version. First later Ticket/Chief demand creates a fresh generation-1 ACP binding. |
| `_rewrite_ticket_employee_session_events` recognition of historical `chat_session_created` | **MOVE — CONVERSATION/FILES** migration-only | It is sealed historical input that rewrites Ticket session identity to `employee_session_changed`. Keep only as migration recognition until the oldest supported DB no longer requires it; it is not a live chat event. |
| `EventKind.chat_session_created` | **DELETE** | Live writers are legacy Day/Chief/human binding paths; ACP binding writes should use conversation binding state and Ticket `employee_session_changed`, not canonical resource invalidation for chat. |
| `chat_message_recorded`, `chat_turn_updated`, `chat_turn_finished` | **DELETE** | Only duplicate chat/resource projection consumes them. Worker correctness callers inspect the product row/Ticket state, not these events. |
| `chat_turn_started` | **RETAIN — PRODUCT WORKER-STEP SETTLEMENT ONLY** | Rewrite only worker-step instances to exact `employee_step_started {"employee_step_id":"<id>"}` for the paired-stage opening marker. Delete human instances and generic origin/mode payloads. |
| `pending_worker_context` | **RETAIN — WORKER-CONTEXT/ACP PROMPT DELIVERY** | Schema and exact-revision semantics remain under `planner.worker_context`. `AcpStepGateway.run_ticket_step` prepares once on its synchronous caller thread, submits only `PreparedWorkerPrompt.model_text`, and acknowledges exact receipts off-loop only after `deliver_tracked_normal` returns. `ConversationComposition` injects the required `SqliteWorkerContextService`; `EmployeeStepRunner` remains only the caller of the gateway and does not own preparation or acknowledgement. |

Frontend resource completeness tests remove all four deleted chat event kinds. The exact
`employee_step_started` event invalidates the same Ticket aggregate resources needed by automatic
eligibility/state changes; ACP WebSocket envelopes are not canonical event-resource keys.

## Ticket session mirror and one-time binding cutover

| Symbol / state | Class | Frozen ownership and cutover |
|---|---|---|
| `tickets.employee_session_id` | **MOVE — CONVERSATION/FILES** (retain on Ticket) | Ticket product mirror of the exact ACP binding session. `SqliteConversationBindingRepository` writes it in the same immediate CAS transaction; the ACP worker-permission guard and worker-self ownership check read it. Rename comments/docs from Hermes identity to ACP Employee session. |
| `EmployeeSessionIdTransition`, `write_employee_session_id_in_transaction`, `claim_running_step_employee_session_id` | **MOVE — CONVERSATION/FILES** (retain) | Ticket-side CAS/mirror writers used by the ACP binding repository and Employee-step session callback. They remain the single door to a live non-null Ticket mirror. |
| `EventKind.employee_session_changed` | **MOVE — CONVERSATION/FILES** (retain) | Exact Ticket resource doorbell emitted by a successful post-cutover mirror change. Resource and migration tests remain. The schema cutover itself writes no synthetic session event; first later ACP demand emits the live event. |
| `read_ticket_by_employee_session_id` | **MOVE — CONVERSATION/FILES** (retain internal) | Keep only for duplicate-owner validation inside `/tickets/{ticket_id}/worker-self` and binding integrity tests. It is no longer an HTTP identity ingress. `read_tickets_by_employee_session_ids` is deleted with live-session lookup. |
| `/tickets/{ticket_id}/worker-self` | **MOVE — CONVERSATION/FILES** (retain Ticket API) | Sole worker-self route. It reads the `PLAN_TICKET_ID` identity supplied by the ACP child and rejects a duplicate-owned Ticket mirror. |

The owner-approved ACP-06 cutover is one global, versioned reset, not compatibility recovery. In the
same terminal migration that removes legacy chat, while the service is stopped:

1. migrate historical worker correctness rows/events to `employee_step_runs`/
   `employee_step_started`;
2. settle every migrated running step `interrupted` and release every corresponding
   `agent_running_step` Ticket to `empty`;
3. set every `tickets.employee_session_id` to `NULL`;
4. delete every pre-cutover `conversation_session_bindings` row; and
5. drop `agent_chat_sessions`.

No legacy Chief or Ticket session is preserved, backfilled, resumed, or reminted on load. The first
post-cutover human or automatic demand creates a fresh ACP session and generation-1 binding; Ticket
CAS also writes its mirror, while Chief has no mirror outside `conversation_session_bindings`.
Reopening the already-migrated schema never repeats the reset.

## Worker self-identity cutover

| Path / symbol | Class | Disposition |
|---|---|---|
| `GET /api/tickets/by-live-session/{live_session_id}` and `GatewayAdapter.stored_session_keys_for_live_session_id` | **DELETE** | Legacy adapter/live-process lookup. ACP children do not need process-session reverse lookup. |
| `GET /api/tickets/by-employee-session/{employee_session_id}` | **DELETE** | Legacy worker identity ingress. The underlying one-owner read remains internal only as classified above. |
| `cli/main.py::worker_my_ticket` reads of `HERMES_UI_SESSION_ID` and `HERMES_SESSION_KEY` | **DELETE** | Remove both fallback branches and messages. `panels worker my-ticket` requires non-empty `PLAN_TICKET_ID` and calls only `/api/tickets/{ticket_id}/worker-self`. |
| `conversation/sdk_child.py` Ticket environment assignment | **MOVE — CONVERSATION/FILES** (retain in place) | ACP child construction scrubs ambient `PLAN_*`, sets exact `PLAN_ACTOR`, and sets `PLAN_TICKET_ID` only for Ticket employees. Hermes live/stored session environment is not an identity input. |

## Runtime and Ticket callers that force the settlement exception

| Caller | Class | Evidence / destination tests |
|---|---|---|
| `runtime/employee_step_runner.py` chat imports and lifecycle calls | **RETAIN — PRODUCT WORKER-STEP SETTLEMENT ONLY** | Starts an `employee_step_runs` row before submission, binds the session before prompt submission, finds the exact running row for shutdown, replaces it on restart, and wins one terminal settlement through `SqliteEmployeeStepRepository`. Delete `observe_worker_gateway_event` and all prompt/reply history assertions. Preserve the named admission, stop/deadline, takeover/late-completion, session-rotation, recovery-mismatch, and spawn-crash proofs against the new row. |
| `runtime/acp_step_gateway.py::guard_worker_permission_settlement` | **RETAIN — PRODUCT WORKER-STEP SETTLEMENT ONLY** | Its immediate transaction proves binding + Ticket + active worker epoch by querying a running worker chat row. Point it at the new worker-step table. Preserve `test_worker_permission_guard_proves_binding_ticket_turn_and_active_epoch` and ACP stale-permission e2e. |
| `runtime/automatic_employee_step_eligibility.py` running-row query | **RETAIN — PRODUCT WORKER-STEP SETTLEMENT ONLY** | A running Employee step blocks another automatic step. Rename `test_only_a_running_chat_turn_blocks_automatic_employee_step` accordingly. |
| Same file’s `chat_turn_started` event query | **RETAIN — PRODUCT WORKER-STEP SETTLEMENT ONLY** | Paired ownership opens once per effective stage. Migrate to explicit Employee-step-start event and preserve the paired-stage marker tests. |
| `core/loops.py::_settle_stale_worker_turns_after_ticket_handoff` | **RETAIN — PRODUCT WORKER-STEP SETTLEMENT ONLY** | Startup finds running worker records whose Ticket no longer says `agent_running_step` and interrupts them. Rehome query/settlement; remove output-role logic. Preserve the corresponding `tests/unit/test_core_loops.py` stale-turn cases. |
| `tickets/data.py` stage resolution, return-for-revision, and delete guards | **RETAIN — PRODUCT WORKER-STEP SETTLEMENT ONLY** | Lines currently querying `chat_turns` prevent canonical Ticket transitions or deletion during an active Employee step. Query the new table. Preserve return-for-revision and active-worker deletion invariants. |
| `tickets/data.py` delete cleanup of `chat_messages` | **DELETE** | Transcript table disappears; preserve full-footprint deletion without transcript assertions. |
| `tickets/data.py` delete cleanup of worker `chat_turns` | **RETAIN — PRODUCT WORKER-STEP SETTLEMENT ONLY** | Reject deletion when the repository reports a running step; after Ticket deletion, `ON DELETE CASCADE` removes all terminal `employee_step_runs`. No optional cleanup branch remains. |
| `runtime/step_gateway.py::{StepGateway, StepGatewayStatus}` | **RETAIN — PRODUCT WORKER-STEP SETTLEMENT ONLY** | Correct runtime port. Rewrite comments to ACP-only, define runtime-owned result/status/busy shapes, remove legacy `SharedGateway`/`PoolStepGateway` claims. |
| `minds/contracts.py::RunStatus`, `RunResult` | **RETAIN — PRODUCT WORKER-STEP SETTLEMENT ONLY** | Move to `runtime/step_gateway.py` (or a runtime contract module). `EmployeeStepRunner` and `AcpStepGateway` need only terminal status, exact Employee session, and error; legacy text/usage/session-key vocabulary does not survive. |
| `minds/contracts.py::OnEvent` | **DELETE** | ACP step gateway ignores raw events and settlement must not observe transcript. Remove callback from the runtime port and runner. |
| `minds/shared_gateway.py::SharedGatewayBusy` | **RETAIN — PRODUCT WORKER-STEP SETTLEMENT ONLY** | Only the generic busy outcome remains useful to runner/ACP gateway. Move/rename to runtime `StepGatewayBusy`; preserve ACP busy mapping test under the new name. |
| `conversation/hub.py::_WorkerCollector`, `_worker_collectors`, `_completed_worker_text`, `install_worker_collector`, `finish_worker_collector`, `take_worker_text` | **DELETE** | This in-memory assistant-text accumulator exists only to populate the legacy `RunResult.text` consumed by Panels worker Chat settlement. ACP typed ingress already reaches the browser, and `employee_step_runs` cannot store transcript. Remove collector-only unit/e2e assertions; keep the requested-cancel/queued-successor proofs against typed browser boundaries and terminal settlement. |
| `worker_context/contracts.py`, `data.py`, `service.py`; `tickets/worker_context.py` | **RETAIN — WORKER-CONTEXT/ACP PROMPT DELIVERY** | Keep schema, service, coalescing, producers, and Ticket deletion unchanged. `ConversationComposition` injects `SqliteWorkerContextService` into required `AcpStepGateway`; the gateway—not the runner—owns prepare/model-text/admission/acknowledge ordering. It never writes or reads Panels chat rows. |

`SharedGateway` deletion is specifically blocked until the settled ACP-05 worker-context suite passes:

- `test_worker_context_prepares_once_on_caller_thread_and_acknowledges_after_tracked_start`;
- the parameterized missing/stale-binding, session-callback, busy, and rejected-admission proof that
  every pre-admission failure retains receipts;
- `test_worker_context_ack_failure_keeps_context_pending_without_cancelling_or_duplicating_turn`;
- `test_conversation_composition_injects_sqlite_worker_context_into_step_gateway`; and
- `test_automatic_worker_delivers_pending_context_through_official_sdk_once`, proving the exact
  prepared prompt reaches the real automatic ACP step and no Panels chat row carries it.

Passing unit tests alone does not authorize deletion; the overall ACP-05 Computer Use pass remains
the outer gate stated at the top of this artifact.

## Durable ACP binding and configuration ownership

| Symbol / file | Class | Evidence |
|---|---|---|
| `conversation/sqlite_binding_repository.py` chat imports, `CHIEF_OF_STAFF_ENTITY_ID`, and Chief mirror calls | **MOVE — CONVERSATION/FILES** | This is live ACP code. Define the Chief employee ID in conversation contracts and remove all `planner.chat` imports. Ticket CAS continues to update/validate the Ticket mirror; Chief CAS writes/validates only `conversation_session_bindings`. Rewrite restart proof to start from a post-cutover ACP binding, not `agent_chat_sessions`. |
| `minds/config.py::resolve_hermes_python`, `hermes_src_root`, `resolve_planner_home`, `repo_root`, `provision_planner_home_skills`, `DEFAULT_*`, `PLANNER_SKILL_NAMES`, `ENV_*` | **MOVE — CONVERSATION/FILES** | `ConversationComposition` imports these for the real ACP backend. Move to conversation/backend configuration without importing legacy gateway code. |
| `minds/config.py::boot_smoke_check` and its `GatewayChild`/spawn constants | **DELETE** | Legacy raw-child smoke only; ACP composition and conformance tests are authoritative. |
| `core/config.py::relay_backend_enabled`, `PLAN_RELAY_BACKEND_ENABLED`; `config.yaml::relay_backend_enabled` | **DELETE** | Selects the legacy pool/relay/neutral stack. ACP composition is the sole production path. |
| `core/config.py::gateway_adapter`, `PLAN_GATEWAY_ADAPTER`; `config.yaml::gateway_adapter` | **DELETE** | Selects the old human/history adapter registry. ACP test options and backend definition are the surviving seams. Update unrelated tests that only set the env var as fixture boilerplate. |
| `core/config.py::run_startup_recovery_in_test_mode`, `PLAN_RUN_STARTUP_RECOVERY_IN_TEST_MODE` | **DELETE** | This switch invokes only retired human `ChatTurnLifecycle` recovery in test mode. ACP test composition and Employee-step recovery have their own explicit owners. Delete the config field, parser, fixture toggle, and its legacy-only tests. |
| `core/config.py::{hermes_bin,hermes_profile,worker_skill}` and their `PLAN_HERMES_BIN`, `PLAN_HERMES_PROFILE`, `PLAN_WORKER_SKILL`/`config.yaml` keys | **DELETE** | These legacy role-gateway fields have no live production reader. Keep `PLAN_HERMES_PYTHON` and `PLAN_HERMES_HOME` in the rehomed ACP Hermes configuration; do not preserve unused aliases. |
| `core/adapters/base.py::{HumanSessionKeyBinder, GatewayAdapter}`; `fakes.py::{CANNED_CATALOG, EchoGatewayAdapter, OfflineGatewayAdapter}`; `real.py::RealGatewayAdapter`; `registry.py::{Adapters, _resolve_auto, _select_gateway, build_adapters}` | **DELETE** | All methods serve old human chat, history, commands, clarification, status, or interrupt. After chat/history removal there is no adapter payload. Remove adapter construction from `server_lifecycle/application.py` and simplify `create_app` inputs/state. |

## Legacy raw Hermes, session, relay, and neutral stack

Every file and public/internal symbol in the following rows is **DELETE**. Their caller chain ends
at the old server composition, `/api/relay`, `/api/relay/neutral`, legacy chat/history adapters,
or tests. ACP has its own SDK child, registry, hub, broker, permission broker, turn strategy,
binding repository, and step bridge.

| Files | Symbols covered / evidence |
|---|---|
| `minds/gateway.py` | `GatewayError`, `GatewayRpcError`, `ChildProcess`, `PopenChild`, `spawn_popen`, `_Pending`, `GatewayRequestHandle`, `ChildSessionEventIngress`, `GatewayChild`, plus raw JSON-RPC constants/types. |
| `minds/sessions/service.py`, `minds/sessions/__init__.py` | `LiveSessionDormant`, `_ConsequenceState`, `_SessionState`, `SubmissionConsequence`, `AcceptedSubmission`, `PendingSubmission`, `LiveSessionOperation`, `LiveSession`, `LiveSessionManager`, and re-exports. |
| `minds/shared_gateway.py` except the moved busy concept | `EntityRoutingGateway`, `SharedGateway`, raw human/worker/history/catalog/clarification/session logic and constants. |
| `minds/contracts.py` legacy shapes | `SubmitDisposition`, `SubmissionReceipt`, `InterruptReceipt`, `HermesObservation`, `TransportUnknown`. |
| `minds/employee_child_registry.py` | `ChildReaderError`, `ChildRegistryClosing`, `ChildReader`, `ChildFrameSubscriber`, `EmployeeChildRecord`, `_InitSlot`, `EmployeeChildRegistry`. ACP owns `AcpEmployeeRegistry`. |
| `minds/runner.py`, `minds/fake.py`, `minds/smoke.py`, `minds/__init__.py` | `run_step`, fake raw child/gateway, standalone raw smoke functions/classes, and all legacy exports. Move the separately classified backend configuration first, then delete the package completely; do not recreate a compatibility package. |
| `hermes_backend/composition.py` | `compose_relay_backend_if_enabled`, `_read_chief_session_key`, `_read_ticket_employee_session_id`. ACP composition and binding repository replace it. |
| `employee_child_pool.py`, `employee_child_relay.py` | `TurnSubmission`, `EmployeeChildPool`, `PendingForward`, `DownstreamConnection`, `ChildBinding`, `EmployeeChildRelay`. |
| `raw_frame_transport.py`, `relay_tee.py`, `transcript_mirror_tee.py` | `RawFrameTransportError`, `_PoolSessionResponder`, `_ShutdownSentinel`, `RawFrameChildTransport`, `RelayFrameDirection`, `RelayTeeObserver`, `_SettledTurnRecord`, `_TurnAccumulator`, `TranscriptMirrorTee`, helpers. The tee’s `record_message` path is expressly forbidden by the worker boundary. |
| `hermes_frame_translation.py` | `NativeRequestPlan`, native/neutral translation functions, `RpcKind`, error mapping, and helpers. ACP schema is the only wire vocabulary. |
| `neutral_vocabulary.py` | Every enum, event/request dataclass, union, and `to_wire`/`from_wire` helper: `ToolPhase`, `TurnFailureReason`, `NeutralEventKind`, `NeutralRequestKind`, history/text/thinking/tool/question/approval/terminal/title/reset/catalog/status/compact/passthrough events and attach/send/answer/respond/interrupt/compact/catalog/new-conversation requests. |
| `neutral_downstream_session.py`, `neutral_relay_config.py` | `NeutralDownstreamSession`, envelope/history helpers, and all neutral queue/preview constants. |
| `relay_route.py`, `relay_neutral_route.py` | `relay_downstream_websocket`, `OverflowSignallingQueue`, `relay_neutral_downstream_websocket`. Delete WS routes `/api/relay` and `/api/relay/neutral`. |
| `pool_step_gateway.py` | `PoolStepGateway`, `_is_busy`, `_shape_event`. `AcpStepGateway` is the sole step gateway. |
| `scripted_relay_child.py` | Scripted store/session/process child and helpers. Replace only with existing ACP scripted-agent fixtures. |
| `hermes_backend/__init__.py` | Package marker after all contents are removed. |

## Server composition and routes

| File / route / state | Class | Evidence |
|---|---|---|
| `core/server.py::_build_role_gateways`, `_is_pool_owned_entity`, `_assert_single_employee_owner`, `_shutdown_gateway_with_deadline`, `_shutdown_relay_pool_with_deadline`, `_start_gateway_if_available`, `_recover_running_human_chat_turns` | **DELETE** | Legacy SharedGateway/pool/relay or human chat startup ownership. Worker context does not move here or to the runner: settled ACP-05 makes `ConversationComposition` inject required `SqliteWorkerContextService` into `AcpStepGateway`. |
| Lifespan legacy gateway/pool/scripted-relay branches and teardown | **DELETE** | Production already builds `ConversationComposition`; retain that branch and ACP-first shutdown order only. |
| `app.state.chat_turn_lifecycle`, `shared_gateway`, role gateways, child pool/relay, relay tee, chat catalogue cache, and adapter state | **DELETE** | No surviving caller after legacy routes and runtime fallbacks are removed. |
| Chat router inclusion | **DELETE** | Removes all `/api/chat/*` and `/api/messages/chief` endpoints. |
| WS `/api/relay`, WS `/api/relay/neutral` | **DELETE** | Duplicate raw/neutral transports. |
| WS `/api/conversation` and `app.state.conversation` | **MOVE — CONVERSATION/FILES** (retain in place) | Sole browser conversation transport/composition. |
| `/api/meta::relay_chief_enabled` | **DELETE** | Obsolete feature-selection response; active routes mount ACP unconditionally. Keep unrelated meta fields. |
| `tickets/api.py` GET `/api/tickets/{ticket_id}/employee-session-history`; `tickets/employee_session_history.py::read_employee_session_history`; `EmployeeSessionHistoryMessage`, `EmployeeSessionHistory` | **DELETE** | No current web caller. It resumes/reads a second legacy client. ACP typed load/replay is authoritative. |

## Images and files

The live `AcpComposer` reads browser `File` bytes and sends ordered ACP `image` content blocks
inline. Real Hermes image dogfood has passed. A final production-only caller search shows the old
managed chat-image graph closes entirely inside deleted code:
`ChatPanel`/`ChiefNeutralPane` -> `uploadChatImage` -> POST `/api/chat/{id}/images` ->
`store_chat_image`; and legacy chat/neutral rendering -> `resolve_chat_file` ->
GET `/files/chats/{id}/{path}`. There is no ACP/product caller. Delete it. Generic managed-path
validation remains because Ticket files call it.

| Symbol / route | Class | Evidence / destination |
|---|---|---|
| `files/chat_images.py::store_chat_image`, `sniff_image_extension`, structural PNG/JPEG/GIF/WebP validators | **DELETE** | The only production caller is the deleted chat upload route. ACP performs typed inline image attachment; no backward-compatible store is earned. |
| `files/logic/paths.py::chat_files_root`, `prepare_chat_entity_files_directory`, `resolve_chat_file`; `files/contracts.py::ChatFile` | **DELETE** | All production callers are the deleted upload/file route, legacy `chat/service.py`, or legacy neutral downstream session. Keep the shared private `_resolve_managed_file`, safe-ID/path checks, and Ticket-file helpers because Ticket files still call them. |
| GET `/files/chats/{entity_id}/{file_path}` | **DELETE** | Only legacy chat/neutral preview links target it. ACP typed replay/browser rendering replaces it. |
| POST `/api/chat/{entity_id}/images` and `files/api.py` import of `chat_service` | **DELETE** | Duplicate chat ingress and Day entity validation; ACP inline typed image content replaces it. |
| `web/lib/api.ts::uploadChatImage`, `ChatImageUploadResponse` | **DELETE** | Not used by active ACP composer. |
| `web/lib/chatImages.js::{isImageFile, createPendingChatImages, removePendingChatImage, pendingChatImageFiles, clearSentPendingChatImages, revokePendingChatImages}` | **MOVE — CONVERSATION/FILES** | Active `ChatComposer`/`AcpComposer` use browser image intake and lifecycle. Rename to conversation images. |
| `web/lib/filePreview.ts` `chat-file` target, `chatFileTarget`, `chatFileHref`, chat branch in `targetFromHref`/`previewHashHref`; `FilePreviewRoute` chat branch | **DELETE** | Only old `/files/chats` links use these branches. Generic external previews and Ticket-file preview/path safety remain. |
| Generic/external/Ticket branches of `web/lib/filePreview.ts` and `web/lib/acp/filePreview.ts::acpFilePreviewTarget` | **MOVE — CONVERSATION/FILES** (retain in place) | Live ACP `ToolCallCard` and `TranscriptView` call this adapter for typed replay previews; removing the chat-file branch does not remove this live generic/Ticket behavior. |

Replacement tests belong to ACP: prove ordered typed image content blocks reach the official SDK,
typed replay renders them after refresh, the browser renders the attachment, and no `/api/chat`
upload/turn request occurs. Keep existing Ticket-file traversal/symlink/nosniff/preview tests; delete
chat-store validation tests with the uncalled store.

## Web UI, resources, and types

| File / symbol / resource | Class | Evidence |
|---|---|---|
| `components/ChatPanel.svelte` | **DELETE** | Duplicate DB-backed transcript/API UI; no production route imports it. |
| `components/ChiefNeutralPane.svelte` | **DELETE** | Duplicate neutral WS pane; no production route imports it. |
| `lib/neutralPane.ts` including `EVENT_KIND`, `REQUEST_KIND`, all neutral snapshot/request types, `createNeutralPaneClient`, reconnect/history/failure/catalog helpers | **DELETE** | Browser client for `/api/relay/neutral`; ACP browser controller replaces it. |
| `components/acp/AcpConversation*`, `AcpComposer.svelte`; mounts in `ChiefOfStaffRoute`, `TicketRoute`, `BoardRoute` | **MOVE — CONVERSATION/FILES** (already correctly owned) | These are the one production conversation surface. |
| `components/ChatComposer.svelte` | **MOVE — CONVERSATION/FILES** | `AcpComposer` actively wraps it. Move/rename under ACP conversation components; remove legacy pause/clarification/skill-only branches not exercised by ACP, retain text/image/command composition used by ACP. |
| `lib/types.ts` `GatewayStatus`, all `Chat*` state/activity/turn/outcome/pending clarification/request/upload types | **DELETE** | Only legacy API/resource/UI consumes them. |
| `lib/types.ts::CommandCatalog` | **MOVE — CONVERSATION/FILES** | Temporary UI adapter used by `ChatComposer`/`AcpComposer`; move local to conversation UI or replace with the ACP command representation. It is not the deleted backend gateway catalog. |
| `lib/api.ts::startChatTurn`, `continueChatTurn`, `answerChatClarification`, `pauseChatTurn` | **DELETE** | Duplicate HTTP turn/clarification/pause API. Keep generic `fetchJson`/`fetchText`. |
| `lib/resourceCatalogue.ts` resources `panelsChat`, `chatGatewayStatus`, `chatCommands`; identities `chat:*`, `chat-status:*`, `chat-commands`; paths `/api/chat/{id}/state`, `/status`, `/commands` | **DELETE** | Only ChatPanel and legacy tests open them. ACP is session WS state, not canonical resource cache state. |
| `TICKET_CHAT_KINDS`, `PANELS_CHAT_KINDS`, `EventFacts.ticketChatKind`, `panelsChatKind`, chat invalidation branches, `parameterizedDefinition` chat prefixes, `ticketEffectIdentities(...includeChat)` | **DELETE** | Removes deleted chat event/resource coupling. Keep targeted canonical product invalidation. |
| `lib/capabilities.ts::{RelayChiefCapability, relayChief, resolveRelayChiefFromMeta, markRelayChiefMetaError, retryRelayChiefMeta}` and `App.svelte` capability fetch branch | **DELETE** | Active routes ignore it and mount ACP. `App.svelte` still fetches meta for debounce/heartbeat; remove only relay capability logic. |
| Current `web/dist` chunks containing `/api/chat`, `/api/relay`, `chat-file`, and neutral/legacy pane code | **DELETE** | FastAPI serves this build, so source deletion is incomplete until Vite rebuild replaces the generated chunks and the absence test scans the served artifact too. |
| `assets/app.css` mixed chat selectors | **MOVE — CONVERSATION/FILES** plus **DELETE** | Retain the shared ACP-used shell/transcript/composer selectors: `chief-chat-*`, `chat-panel`, `chat-head`, `chat-dot*`, `chat-lbl`, `chat-thread*`, `chat-jump`, `chat-u`, `chat-a`, `chat-box*`, `chat-menu*`, `chat-image*`, `chat-drop-label`, `chat-ta`, `chat-foot`, `chat-slash`, `chat-send`, `chat-rail`, and `.chat-thread .file-preview-*`, including their scrollbar rules. Delete `chat-outcome*`, `chat-continue*`, `chat-pending*`, `chat-activity*`, `chat-dots`/`chat-bounce`, `chat-off*`, `chat-empty*`, `chat-sys`, and `chat-clarification*`; those are used only by deleted ChatPanel/neutral/clarification activity. Keep all `acp-*`, generic file-preview, Chief layout, Ticket layout, and unrelated selectors. A post-delete selector-to-live-caller search is required. |
| `web/package.json` `test` script | **MOVE — CONVERSATION/FILES** (rewrite) | Remove `neutral-pane.test.mjs` and `ticket-neutral-pane.test.mjs`; keep `chat-images.test.mjs` only after rewriting it to ACP pending-browser-image helpers with all upload/managed-chat cases removed. Add the surviving ACP browser state, components, conformance, contracts, and production-mount suites to the script so `npm test` exercises the sole UI path. Keep resource/cache/WebSocket/file-preview/lifecycle/markdown suites. |

Current guard evidence: `web/tests/acp-production-mount.test.mjs` asserts active wrappers/routes do
not mount `ChatPanel`, `ChiefNeutralPane`, relay capability, `/api/chat`, or `/api/relay`;
`web/tests/acp-contracts.test.mjs` rejects legacy API/neutral dependencies from ACP contracts.

## Clarification and permission ownership

| Path | Class | Evidence |
|---|---|---|
| Hermes `clarify.request` normalization, pending clarification DB columns/state, POST clarification-answer, gateway `respond_to_clarification`, transcript mirror | **DELETE** | A second request/answer protocol and DB mirror. |
| `conversation/permission_broker.py` and ACP permission UI/envelopes | **MOVE — CONVERSATION/FILES** (retain in place) | One live ACP callback/selection path, guarded by exact employee/turn epoch. Preserve `test_worker_guard_marks_exact_pending_inside_immediate_transaction`, `test_real_sdk_permission_callback_is_admitted_by_turn_epoch_and_cancelled_once`, `test_stale_worker_permission_cannot_settle`, and ACP browser permission-option tests. |

## Test disposition

### Delete with the retired subsystem

- Entire legacy chat suites: `tests/unit/test_chat_activity.py`, `test_chat_clarification.py`,
  `test_chat_commands.py`, `test_chat_seed.py`, `test_human_chat_turn.py`,
  `test_employee_session_history.py`, and `tests/e2e/test_live_chat_state.py`.
- Rewrite `tests/unit/test_chat_ingress_contract.py` as the ACP-06 route/import absence boundary. It
  must reject `/api/chat`, `/api/relay`, `/tickets/by-live-session`,
  `/tickets/by-employee-session`, and employee-session history while requiring
  `/api/conversation` and `/tickets/{ticket_id}/worker-self`.
- Delete neutral/relay UI suites: `tests/e2e/test_chief_neutral_pane.py`,
  `test_ticket_neutral_pane.py`, `web/tests/neutral-pane.test.mjs`, and
  `web/tests/ticket-neutral-pane.test.mjs`.
- Delete raw/legacy transport suites: `tests/unit/test_minds.py`, `test_minds_sessions.py`,
  `test_employee_child_registry.py`, `test_pool_step_gateway.py`,
  `test_raw_frame_request_reply.py`, every
  `tests/unit/test_hermes_backend_*.py`, and their relay/scripted-child fixture branches. Before
  deleting `test_minds.py`, transplant its worker-context admission/retention cases to the ACP
  runner/gateway suite and its live Hermes path/home/skill-provisioning cases to a conversation-owned
  Hermes configuration suite. Human-message, slash-command, raw-child smoke, and transport cases are
  deleted with Chat.
- Delete old ChatPanel/managed-chat-file image suites: `tests/e2e/test_chat_images.py`,
  `tests/unit/test_chat_images.py`, and the legacy upload/file-preview branches of
  `web/tests/chat-images.test.mjs`. Keep Ticket-file security/preview suites for the shared generic
  path logic still exercised by Ticket files.
- Delete the raw pool/relay cases in `tests/unit/test_pool_ticket_adoption.py`. Move only its durable
  duplicate-owner/CAS assertions into `test_acp_binding_repository.py`; delete pool adoption,
  eager-adoption, raw resume/create, and `bind_pool_employee_session_id` coverage with their owners.

### Rewrite in surviving mixed suites

- `tests/unit/test_employee_step_runner.py`: keep settlement/recovery/stop/takeover/session tests;
  replace `test_worker_step_prompt_and_reply_are_visible_in_chat_history` with a boundary test that
  proves the worker product record contains no transcript and the prompt went through ACP.
- `tests/unit/test_worker_context.py`: retain keyed coalescing, exact-revision acknowledgement,
  Ticket producer, and Ticket deletion tests without moving gateway behavior here. The settled
  ACP-05 gateway/composition/e2e tests listed above own prompt preparation, pre-admission retention,
  acknowledgement failure, injection, and real automatic delivery.
- `tests/unit/test_acp_step_gateway.py`: point permission guard at the new worker-step repository;
  rename the busy test from `SharedGatewayBusy` to the runtime busy concept.
- `tests/unit/test_automatic_employee_step_eligibility.py`,
  `test_automatic_employee_step_discovery_loop.py`, and `test_core_loops.py`: rename chat-row/event
  fixtures to worker-step records/events and preserve behavior.
- `tests/unit/test_return_for_revision.py`, `test_ticket_delete.py`, Ticket stage/ownership tests,
  and mixed e2e flows: keep active-worker guards and delete transcript/table assertions.
- `tests/unit/test_db.py`: assert exact worker-row/event migration, deletion of human
  chat/activity/message data, interruption of migrated running rows, release of running Tickets,
  null Ticket mirrors, deletion of every old binding and `agent_chat_sessions`, removal of Day chat,
  and idempotent reopen without a second reset.
- `tests/unit/test_acp_binding_repository.py`: delete Chief mirror/backfill fixtures. Prove a
  post-cutover Chief first CAS writes only `conversation_session_bindings`; Ticket first CAS writes
  the Ticket mirror atomically; duplicate-session rejection and fresh post-cutover restart remain.
- `tests/e2e/conftest.py`, server/config tests, and unrelated route tests: remove
  `PLAN_GATEWAY_ADAPTER` and `PLAN_RELAY_BACKEND_ENABLED` boilerplate. Preserve `/api/meta` tests
  without `relay_chief_enabled`.
- `web/tests/resource-catalogue.test.mjs` and `tests/e2e/test_resource_catalogue.py`: remove chat
  resources/event kinds and keep completeness for canonical product events.
- `web/tests/chat-images.test.mjs`: move only pending browser image helper coverage alongside ACP;
  delete upload and managed chat-file assertions. `file-preview.test.mjs` keeps external/Ticket-file
  cases and deletes `chat-file` cases.
- `tests/e2e/test_new_worker_public_flow.py`: replace `SharedGateway` and `/api/chat` helpers with the
  production ACP composition and real `EmployeeStepRunner`. Preserve the New Worker Understanding
  stage, same post-cutover ACP binding across human/automatic demand, proposal parking, and browser
  progression; transcript assertions use the ACP WebSocket/UI only.
- `tests/e2e/test_flows_a.py`: delete the ChatPanel DB-history/offline-adapter cases. Move the still
  live scroll-follow/jump, refresh replay, and ACP command-menu behaviors to
  `test_acp_conversation.py`/ACP browser component tests; remove `/api/chat`, employee-session-history,
  system-line, ChatPanel pending/activity, and adapter-offline assertions. Rewrite the revision case
  to assert no Panels chat table exists and the automatic prompt uses ACP.
- `tests/unit/test_request_identity.py`: retain actor classification/direct-write/header tests and
  delete only `test_role_gateway_wiring_sets_distinct_actors_and_preserves_environment`. ACP child
  environment coverage in `test_acp_employee_child.py` proves Ticket `PLAN_TICKET_ID` and Chief/no
  Ticket identity.
- `tests/unit/test_worker_my_ticket.py`, `test_worker_cli_identity.py`, and
  `test_cli_entrypoints.py`: delete by-live-session/by-employee-session and Hermes-env fallback cases;
  retain/rewrite only `PLAN_TICKET_ID` → worker-self, missing-`PLAN_TICKET_ID` rejection, specialist
  skill response, missing Ticket, and duplicate Ticket-mirror ownership rejection.
- `tests/unit/test_server_shutdown_process.py`: replace the raw gateway interpreter with the existing
  official ACP scripted subprocess/composition. Assert process shutdown interrupts one
  `employee_step_runs` row with the exact binding/Ticket session, stores no partial output, exits
  cleanly at the shared deadline, and leaves no running worker record.

### Existing ACP destination coverage to retain

- `tests/unit/test_acp_conversation_composition.py`, `test_acp_conversation_websocket.py`,
  `test_acp_employee_child.py`, `test_acp_employee_registry.py`, `test_conversation_hub.py`,
  `test_conversation_turn_broker.py`, `test_conversation_permission_broker.py`, and
  `test_acp_step_gateway.py`.
- `tests/unit/test_acp_binding_repository.py`, especially post-cutover Ticket mirror CAS, Chief
  binding-only ownership, employee resolution, and duplicate-session rejection.
- `tests/e2e/test_acp_conversation.py`, especially durable restart binding, official fork across
  refresh/child death/restart, browser/worker one-session sharing, mid-turn attach, stale worker
  permission rejection, and predecessor teardown before a queued successor. The proof must observe
  typed browser/terminal boundaries, not the deleted worker-text collector.
- `web/tests/acp-browser-components.test.mjs`, `acp-browser-state.test.mjs`,
  `acp-browser-conformance.test.mjs`, `acp-contracts.test.mjs`, and
  `acp-production-mount.test.mjs`.

### Required ACP-06 boundary additions

1. A repository absence test asserting there is no `src/planner/chat`, `src/planner/hermes_backend`,
   raw `minds` transport/session/shared gateway, `/api/chat`, `/api/relay`, legacy pane, neutral
   vocabulary, ChatPanel resource, or legacy config flag.
2. A worker-boundary test proving the retained product record has no prompt/reply/activity/image or
   clarification fields and cannot be read as conversation history.
3. A migration test with one human chat row, terminal and running worker-step rows, Day/Chief legacy
   session data, Ticket mirrors, and ACP bindings: only exact terminal worker correctness history
   survives; running records interrupt, running Tickets release, all old mirrors/bindings reset,
   `agent_chat_sessions` drops, and first later Ticket/Chief demand creates fresh generation-1 ACP
   bindings in the frozen owners.
4. ACP image tests proving ordered typed image content blocks reach the official SDK, typed replay
   and browser rendering survive refresh, and no `/api/chat` upload/turn endpoint is called.

## Live documentation disposition

| Document | Required ACP-06 disposition |
|---|---|
| `docs/chat.md` | Rewrite in place as the sole ACP conversation boundary. It must state that Panels chat rows do not deliver worker context and that no `/api/chat` surface remains. Keep its existing `docs/README.md` map entry. |
| `docs/employee-runtime.md` | Document `EmployeeStepRunner` + `AcpStepGateway`, the exact `employee_step_runs` correctness record, `pending_worker_context` prepare/admit/acknowledge ownership, restart/permission settlement, and absence of transcript fields. |
| `docs/frontend.md` | Remove ChatPanel/neutral/resource-cache chat ownership and describe `/api/conversation`, the ACP reducer/components, inline typed images, retained shared composer/layout CSS, and the updated `npm test` suite. |
| `docs/systems.md` | Replace chat/relay/raw-child nodes and edges with conversation, binding repository, worker-context gateway, and Employee-step repository ownership; include the one-time binding reset. |
| `docs/systems.html` | Regenerate/update from the corrected systems map so no retired chat/relay route, table, or session owner remains in the rendered document. |
| `docs/tickets-and-gates.md` | Document `tickets.employee_session_id` as the Ticket ACP mirror, `PLAN_TICKET_ID` worker-self identity, `employee_step_started` paired-stage marker, active-run transition/delete guards, and the clean first-demand session after cutover. |
| `AGENTS.md`, `CLAUDE.md` | Rewrite the current worker-context boundary so it names ACP session/prompt delivery and `employee_step_runs`; remove statements that present deleted `chat_messages`/`chat_turns` as live product state. Preserve the rule that a product/event row is never model delivery. |
| `DESIGN.md` | Replace the current `ChatPanel`/legacy command-catalog implementation pointer with the restrained ACP pane/composer and typed command/update surface. Preserve the existing Panels visual direction; this is documentation cleanup, not a redesign. |

`docs/README.md` retains the `docs/chat.md` map entry but updates its description to ACP conversation.
History stays in git; none of these documents may preserve the legacy path as a current alternative.

## Accepted ownership-closure corrections

The ACP-06 ticket-planning caller sweep found four narrow omissions in the earlier table. They are
accepted into this authoritative classification:

1. the Hub worker-text collector is **DELETE**, because only legacy worker Chat settlement consumes
   it and the retained Employee-step record has no transcript field;
2. the human-recovery test flag and unused legacy `hermes_bin`/`hermes_profile`/`worker_skill`
   configuration families are **DELETE**, while ACP's Hermes Python/home configuration remains;
3. live Hermes path/home/skill-provisioning tests inside `test_minds.py` are **MOVE —
   CONVERSATION/FILES** to the rehomed ACP Hermes configuration suite before the raw test file is
   deleted; and
4. stale current-state text in `AGENTS.md`, `CLAUDE.md`, and `DESIGN.md` is **MOVE —
   CONVERSATION/FILES** documentation cleanup in the same cutover.

These corrections close already-present legacy ownership; they add no product behavior,
compatibility path, backend, or UI work. With them, the classification remains READY AS A
CONDITIONAL CLASSIFICATION and the ACP-05 Computer Use gate remains unchanged.

## Caller-closure searches used

The inventory was closed with repository-wide searches for imports and literal ownership tokens,
including `planner.chat`, `planner.minds`, `planner.hermes_backend`, `chat_turns`, `chat_messages`,
`chat_turn_activity_entries`, `chat_session_key`, `agent_chat_sessions`, `pending_worker_context`,
every `chat_*` event,
`/api/chat`, `/api/relay`, `ChatPanel`, `ChiefNeutralPane`, `neutralPane`, `panelsChat`,
`chatGatewayStatus`, and `chatCommands`. The non-legacy production hits are all accounted for in
the runtime, core-loop, Ticket, conversation-binding, configuration-helper, composer/image, and
file-preview rows above. The final live-document search also includes `AGENTS.md`, `CLAUDE.md`, and
`DESIGN.md`; `PROGRESS.md`, `decisions.md`, and `orchestration/**` remain historical evidence rather
than current-system cleanup targets.
