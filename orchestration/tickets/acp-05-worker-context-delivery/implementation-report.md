# ACP-05 worker-context delivery implementation report

Status: **READY**

## Implemented

- `AcpStepGateway` now requires a `WorkerContextService` and keeps the frozen synchronous order:
  caller-thread guard, one `prepare`, then coroutine scheduling.
- The complete `PreparedWorkerPrompt` crosses into the async step. Its `model_text` is the sole ACP
  text block; the original prompt is not submitted separately.
- Exact receipts remain pending through binding resolution, the session callback, collector setup,
  and broker admission. After `deliver_tracked_normal` returns, non-empty receipts are acknowledged
  through `asyncio.to_thread` before the same tracked completion is awaited.
- Ordinary acknowledgement failure is logged and swallowed. It does not cancel, retry, resubmit, or
  alter the admitted turn, leaving the prepared revision available to a later explicit step.
- `ConversationComposition` constructs one `SqliteWorkerContextService` with short-lived
  `connect(db_path, busy_timeout_ms)` connections and injects it in production and test-option modes.

## Proof added

- The named gateway ordering test proves caller thread, exact prepared text, admission-before-ack,
  off-loop acknowledgement, and zero preparation for owner-loop/stopped-loop calls.
- A six-case pre-admission matrix proves zero acknowledgement for missing/stale binding, callback
  exception/timeout, busy admission, and generic broker rejection.
- The named acknowledgement-failure test proves a normal result, one delivery per explicit call, no
  cancel/resubmit, error logging, and the same prepared revision on the next explicit call.
- The named composition test proves the required SQLite service is installed.
- The named official-SDK automatic-step test proves one prepared ACP text block reaches the real
  child, the pending row is acknowledged, no context text enters `chat_messages`, and the waiting
  step can be interrupted and drained.

## Verification

The exact focused output is recorded in `focused-checks.txt`. Ruff passed, strict Mypy passed for the
two changed source files, and all 49 tests in the gateway, composition, worker-context, and ACP e2e
files passed. Canonical `./verify` was intentionally not run for this ticket.

## Focused review disposition

One bounded review round completed. The spec axis passed with no missing behavior, scope creep, or
incorrect implementation. The standards axis requested exact internal names; the implementation now
uses `worker_context_service`, `prepared_worker_prompt`, and `_FakeWorkerContextService`. Its optional
scenario-record suggestion for the six-case parameterized test was declined because it would add
structure without changing the proof. The final focused gate above passed after the naming correction.
