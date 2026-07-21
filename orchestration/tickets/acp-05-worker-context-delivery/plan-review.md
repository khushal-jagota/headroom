# ACP-05 worker-context delivery plan review

## Verdict

`NOT READY`

One concrete proof gap remains. No source or tests were changed or run.

## Finding

### P1 — The caller guard is not included in the frozen prepare ordering

The contract requires
`_require_caller_thread() -> prepare(...) -> run_coroutine_threadsafe(...)`, so an owner-loop call or
a call after the loop stops must fail before the SQLite context service is touched. The plan requires
`prepare` before scheduling and tests that it precedes stream resolution, callback, collector, and
prompt, but it never says that the existing guard remains first or asserts `guard -> prepare`.
An implementation could therefore move `prepare` above the guard while satisfying every planned
assertion.

Required correction:

- In Phase 2, state that `run_ticket_step` retains `_require_caller_thread()` as its first operation
  and calls `prepare` immediately after that guard succeeds.
- In the focused ordering test, record/assert `guard -> prepare -> async stream resolution -> callback
  -> collector -> broker prompt`; alternatively assert invalid owner-thread/stopped-loop calls perform
  zero prepares. Either proof is sufficient.

## Other reviewed boundaries

- The prepared `model_text` is the sole ACP text block; no transcript copy is planned.
- `deliver_tracked_normal` currently returns only after allocating the tracked epoch, creating the
  prompt task, and publishing the broker's started state, so the planned post-return acknowledgement
  is the correct admission boundary and matches the old gateway's accepted-submission meaning.
- Missing/stale binding, callback exception/timeout, busy admission, and generic broker rejection all
  retain receipts through the planned zero-ack matrix.
- Acknowledgement runs off-loop, and its failure is logged/swallowed while the same tracked handle is
  awaited; the next explicit run proves at-least-once redelivery without retrying the admitted turn.
- Production/test composition injects the real SQLite service, while the automatic-step official-SDK
  audit checks the composed prompt, exact-row removal, and absence from Panels chat. The runner prompt,
  binding, settlement, and automatic-work behavior otherwise remain untouched.

No second finding was raised in this bounded pass.

## Orchestrator disposition

Accepted and corrected before source dispatch. Phase 2 now freezes
`_require_caller_thread -> prepare -> run_coroutine_threadsafe`, and the first focused test proves the
guard precedes preparation plus zero prepares for invalid owner-loop/stopped-loop calls. Because the
review confirmed every other boundary sound and the correction is the exact requested assertion, no
second broad plan-review round is warranted. The ticket is `READY` once the overlapping requested-
cancel implementation review settles.
