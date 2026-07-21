# ACP-05 worker-context delivery through the ACP step

## Why this ticket exists

Ticket edits still write `pending_worker_context`, but the production ACP worker path never reads it.
Only the legacy `SharedGateway` calls `WorkerContextService.prepare/acknowledge`, so deleting that
gateway would silently stop the documented “reread the changed ticket” guidance from reaching the
worker. A Panels chat row is not delivery; the prepared text must enter the real ACP prompt.

## Frozen contract

1. Existing `pending_worker_context` schema, `planner.worker_context` contracts/data/service, Ticket
   producers, keyed coalescing, monotonic revisions, and exact-revision delete behavior stay
   unchanged.
2. `AcpStepGateway` requires a `WorkerContextService`; there is no empty/default compatibility
   service. `ConversationComposition.build` injects `SqliteWorkerContextService` in production and
   test-option composition, using short-lived connections for the configured database.
3. `run_ticket_step` calls `prepare(entity_id, prompt_text)` exactly once on its synchronous caller
   thread, after the caller-thread guard and before `run_coroutine_threadsafe` schedules ACP work.
4. The returned `PreparedWorkerPrompt.model_text` is the exact text in the ACP `PromptRequest`.
   The original and prepared strings are not both submitted, and pending context is never copied to
   `chat_messages`, `chat_turns`, browser echoes, or any other transcript projection.
5. The exact prepared receipts remain pending through binding/session callback and broker admission.
   Acknowledge them only after `deliver_tracked_normal` returns the tracked handle—its contract proves
   the normal prompt was admitted and started. Use `asyncio.to_thread` so SQLite acknowledgement
   never blocks the conversation event loop. An empty receipt tuple performs no acknowledgement or
   SQLite transaction.
6. Session callback failure/timeout, missing or stale required binding, busy active turn, rejected
   broker admission, or any failure before tracked delivery returns performs no acknowledgement.
7. Acknowledgement failure is logged and swallowed after admission. It cannot cancel, resubmit, or
   duplicate the already-started ACP turn and cannot change its terminal `RunResult`. The SQLite
   service rolls back, leaving the exact context pending for at-least-once redelivery on a later step.
8. Existing session binding, tracked-turn collection, interruption, permission settlement, terminal
   mapping, and runner settlement behavior otherwise remain unchanged.

## Required proof

- `tests/unit/test_acp_step_gateway.py` proves prepare count/thread/order, exact prepared prompt,
  post-admission off-loop acknowledgement, no acknowledgement for every pre-admission failure, and
  acknowledgement failure with one admitted prompt, unchanged terminal result, and pending redelivery.
- `tests/unit/test_acp_conversation_composition.py` proves the real composition passes a
  `SqliteWorkerContextService` to the required gateway dependency.
- `tests/e2e/test_acp_conversation.py` adds one production-composition automatic-step test using the
  official SDK prompt audit: pending context appears once in the exact model prompt, disappears from
  SQLite only after admission, and never appears in a Panels chat row.
- Existing `tests/unit/test_worker_context.py` remains green without changing its schema/data/service
  contract.

## Allowed files and blocker

- `src/planner/runtime/acp_step_gateway.py`
- `src/planner/conversation/composition.py`
- `tests/unit/test_acp_step_gateway.py`
- `tests/unit/test_acp_conversation_composition.py`
- `tests/e2e/test_acp_conversation.py`
- this ticket's plan/report/review/evidence files

Source implementation waits for ACP-05 requested-cancel recovery because `composition.py` and the ACP
e2e suite overlap. Planning may land now. Do not edit worker-context schema/types/writers, chat,
`PROGRESS.md`, `decisions.md`, or unrelated files, and do not run canonical `./verify` in this ticket.
