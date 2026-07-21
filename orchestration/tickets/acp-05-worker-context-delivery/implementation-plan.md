# ACP-05 worker-context delivery implementation plan

## Design

Keep context preparation at the synchronous gateway boundary and receipt settlement at the exact
asynchronous admission boundary:

`caller -> prepare once -> session callback -> deliver_tracked_normal -> acknowledge in to_thread
-> await the same tracked completion`.

This preserves the current callback-before-prompt guarantee, makes `model_text` the only backend
prompt, and gives acknowledgement a precise meaning: the prepared revision reached an admitted ACP
turn. A failed acknowledgement is an at-least-once bookkeeping failure, not a model-turn failure.

## Phase 1 — freeze the gateway behavior with focused tests

Extend the `_gateway` fixture in `tests/unit/test_acp_step_gateway.py` with a required recording
`WorkerContextService` fake and broker admission markers.

1. Add `test_worker_context_prepares_once_on_caller_thread_and_acknowledges_after_tracked_start`.
   Assert `_require_caller_thread` succeeds before `prepare`, then `prepare` precedes async stream
   resolution, session callback, collector, and broker prompt; assert invalid owner-loop/stopped-loop
   calls perform zero prepares. Assert the broker receives exactly
   `PreparedWorkerPrompt.model_text`; assert acknowledgement sees the exact receipts only after
   delivery returns and runs outside the event-loop thread.
2. Add a parameterized pre-admission test covering missing/mismatched required binding, session
   callback exception/timeout, busy active prompt, and generic rejected admission. Preparation occurs
   once, no prompt starts where applicable, and acknowledgement count stays zero.
3. Add `test_worker_context_ack_failure_keeps_context_pending_without_cancelling_or_duplicating_turn`.
   Make acknowledgement raise, complete the tracked prompt once, and assert the normal `RunResult`,
   one broker delivery, no cancel/resubmit, and the same prepared revision on the next explicit run.

## Phase 2 — implement the required gateway dependency

In `src/planner/runtime/acp_step_gateway.py`:

1. Add required constructor field `worker_context: WorkerContextService`.
2. In synchronous `run_ticket_step`, retain `_require_caller_thread()` as the first operation and call
   `prepare` exactly once immediately after that guard succeeds, before creating/scheduling the
   coroutine. Pass the complete `PreparedWorkerPrompt` into `_run_ticket_step`; build the sole text
   content block from `prepared.model_text`.
3. Immediately after `await broker.deliver_tracked_normal(...)` returns, call
   `await asyncio.to_thread(worker_context.acknowledge, entity_id, prepared.receipts)`. Skip the call
   for an empty receipt tuple.
4. Catch and log ordinary acknowledgement exceptions, then continue awaiting that same tracked
   handle. Do not retry, cancel, create another client message ID, or alter terminal mapping.

## Phase 3 — compose the SQLite owner

In `src/planner/conversation/composition.py`, construct one `SqliteWorkerContextService` whose
connection factory uses `connect(db_path, busy_timeout_ms)`, and pass it to `AcpStepGateway` in every
composition mode. Add
`test_conversation_composition_injects_sqlite_worker_context_into_step_gateway` in
`tests/unit/test_acp_conversation_composition.py`; keep direct gateway tests on explicit fakes.

## Phase 4 — prove the production automatic step

Add `test_automatic_worker_delivers_pending_context_through_official_sdk_once` beside the existing
worker-first e2e. Seed an eligible Ticket plus one pending exact revision, dispatch through
`POST /api/test/run-step/{id}`, and use the existing official-SDK UDP prompt audit to assert one text
block containing the original step prompt plus one pending-context envelope. Poll until the exact
pending row is acknowledged, assert no `chat_messages` text contains that context, interrupt the
waiting scripted turn, and drain the runner.

After requested-cancel recovery lands, run only the focused gateway, composition, worker-context,
and ACP e2e tests plus Ruff/Mypy required by the parent ACP workflow. This planning task itself runs
no tests and makes no source change.
