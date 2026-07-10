# t_hs03 implementation report

## Implemented

- `SharedGateway.run_ticket_step` now registers employee work through the shared live-session
  consequence seam and consumes only that submission's owned observations.
- A queued employee attempt ignores the preceding execution's terminal and begins projecting worker
  Chat activity only when its own Hermes lifecycle starts.
- Streaming receipts preserve events on both sides of the response boundary. Steered input is
  reported as delivered but errored because it has no independent employee terminal to own.
- Worker context is acknowledged at the proved employee delivery boundary: streaming and steered
  receipts prove delivery, while queued work waits for its owned first observation. Unknown delivery
  and queued child death retain the exact receipts.
- The shared ordered-operation lane admits at most one consequential employee attempt for a live
  session. A second employee call uses the existing busy outcome before writing another prompt.
- Unknown prompt delivery returns one precise errored result, is never retried, and cannot consume a
  later unrelated completion.
- Independent review found that late delta/tool activity from an observed pre-existing Hermes
  lifecycle could activate queued work before that lifecycle's terminal. The shared consequence now
  keeps a queued start boundary closed when admission observes Hermes already running, and opens it
  only at that predecessor terminal. A later resume snapshot cannot close an already-proved boundary.

`EmployeeStepRunner` required no production changes. Its existing Chat, Ticket, proposal, revision,
claim, and readiness settlement remains the sole product projection.

## RED-to-GREEN evidence

- The queued gateway regression first returned the preceding interrupted completion; it now remains
  pending and returns only the employee lifecycle.
- The one-pending regression first wrote a second `prompt.submit`; it now rejects before that write.
- Queued context tests first acknowledged on queue acceptance; they now acknowledge only at the owned
  start and retain context if the child dies first.
- Steered initially waited for an unrelated terminal; it now settles immediately without claiming it.

## Focused verification

- 143 tests passed across `test_minds_sessions.py`, `test_minds.py`,
  `test_employee_step_runner.py`, `test_return_for_revision.py`, and `test_worker_context.py`.
- Ruff passed for all permitted source and test files.
- Mypy passed across 104 source files.
- `git diff --check` passed.

The authoritative full `./verify` is intentionally left to serial integration after independent
implementation review, as required by the ticket plan.
