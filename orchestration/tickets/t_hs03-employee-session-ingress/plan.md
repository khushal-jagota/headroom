# t_hs03 implementation plan — employee execution on ordered ingress

## Purpose of this slice

Migrate only `SharedGateway.run_ticket_step` from the temporary per-call event drain to the hs01
ordered session ingress. Keep `EmployeeStepRunner`, its visible worker Chat turn, Ticket settlement,
claim/release rules, and direct-revision handoff behavior materially unchanged.

The ticket already fixes the TDD seams: the public gateway call, the runner's observable Ticket/Chat
state, and the worker-context prepare/acknowledge boundary. Tests must not reach into session-manager
registries or private queues.

## Required shared consequence seam

Use the same smallest disposition-aware per-attempt consequence handle required by hs02. It belongs
to `planner.minds.sessions.service` and is shared by human and employee callers; do not track employee
ownership separately inside `SharedGateway`.

Submitting registers one product consequence before the write and returns Hermes's immutable
`SubmissionReceipt` plus a handle that receives only observations relevant to that accepted
submission. The handle is observational, not a queue: it never schedules, promotes, retries, or waits
for Panels-inferred idle. Releasing the product consumer decrements its consequence count only after
that product outcome settles. A transport-unknown attempt remains recorded as unknown by the session
even after the runner projects its one existing errored outcome. In particular, the active
lifecycle's terminal cannot settle, release, or decrement a queued attempt; that handle becomes
current only when the next Hermes lifecycle starts.

If hs02 has already added this exact shared seam, reuse it unchanged. Otherwise add it once to the
hs01 session module and contracts for both migrations; do not create an employee-only registry,
wrapper queue, turn ID, or second event consumer.

## Contract decisions

- Keep the existing `run_ticket_step(...) -> RunResult` caller surface and existing Ticket statuses.
  Native submit disposition stays typed at the hs01 boundary; it does not become a database field or
  UI state.
- A terminal Hermes event may settle the employee result only after the registered employee attempt
  is observed as the current user execution. Events before that delivery boundary remain faithful
  session observations but are not forwarded to the employee Chat turn.
- `streaming` owns the execution that starts for this registered submission, including start events
  buffered before the RPC response. `queued` owns the next user execution after the preceding one
  terminates. The preceding terminal cannot settle it.
- `steered` proves delivery into an already-active execution but creates no independent employee
  execution. Return an honest existing `RunResult("errored", ...)` without waiting for or claiming a
  later terminal. This prevents the readiness loop from automatically resubmitting the same input.
- A submit whose transport outcome is unknown/offline also returns the existing errored result with a
  precise cause. Do not retry, infer delivery, or claim a later completion.
- Permit only one independently consequential Panels attempt per live session. A second attempt is
  rejected through the existing busy path; do not add a FIFO over Hermes's one merging pending slot.

## RED-to-GREEN tracer bullets

Add one public-behavior test, then the minimum implementation for it, in this order:

1. **Queued ownership at the gateway seam.** Through `SharedGateway.run_ticket_step`, script an
   already-running session, a `queued` submit receipt, the prior execution's completion, then the
   employee start/delta/completion. Assert the call remains pending after the prior completion,
   returns only the employee reply, and forwards only employee-owned observations to `on_event`.
2. **Reported `0.0s` regression at the runner seam.** Block the fake after emitting the old
   `message.complete{status: interrupted}`. Assert the worker Chat turn and Ticket remain
   `agent_running_step`; then emit the employee start/reply and assert the existing Chat and Ticket
   contracts settle exactly once with that reply. This is the regression that must fail against the
   old temporary drain.
3. **Fast streaming path.** Emit employee start/delta/completion around the submit response boundary
   and return `streaming`. Assert gapless delivery and the unchanged visible worker prompt/reply.
4. **Steered is delivered, not independently complete.** Return `steered`, then emit a later unrelated
   completion. Assert the employee call does not claim it, the worker turn/Ticket use the existing
   errored settlement with a disposition-specific cause, and no automatic resubmit occurs.
5. **Worker-context delivery boundary.** With exact recorded receipts, prove `queued` acceptance does
   not acknowledge context; the owned employee start does. In separate cases, child death or unknown
   submit outcome before owned delivery retains the receipts and sends no second prompt. A `steered`
   receipt acknowledges because Hermes reports the input delivered, but still cannot own a
   terminal.
6. **One consequential pending attempt.** Hold one queued employee attempt before its start and invoke
   a second `run_ticket_step` for the same stored/live session. Assert the second uses the existing
   busy outcome and no second `prompt.submit` is written.
7. **Unknown settles once without stealing later work.** At the public `run_ticket_step` seam, make
   `prompt.submit` delivery unknown, then emit a later unrelated session completion. Assert one
   errored result, one submit frame, retained context receipts, no forwarded unrelated event, and no
   retry. Repeat through `EmployeeStepRunner`: its worker Chat turn and Ticket fail exactly once, the
   later completion creates no message or second status settlement, and the readiness doorbell rings
   only for that one settlement.

Use literal event sequences and observable results; do not assert private lock state, registry shape,
or internal callback counts.

## Implementation shape

Use the shared consequence seam in `src/planner/minds/sessions/service.py`, then replace only the
employee path inside `SharedGateway.run_ticket_step`/its employee-specific helper:

1. Resume or create exactly as today, including strict stored-session resume for revisions and
   persisting the resolved key before prompt submission.
2. Prepare hidden worker context immediately before registering the consequential employee attempt.
3. Submit through the hs01/hs02 shared per-attempt consequence handle, which registers intent before
   the write and preserves the native disposition. No employee-only tracking belongs in
   `SharedGateway`.
4. Wait on that attempt's owned observation stream, not `GatewayChild.open_session_events`. Forward
   start/delta/tool activity only after the attempt crosses its delivery boundary; convert its owned
   terminal into the unchanged `RunResult` fields.
5. Acknowledge the exact prepared worker-context receipts once, at proved start/delivery. Queued
   acceptance alone is insufficient. Pre-delivery interruption, unknown transport, and child death
   retain the context.
6. Release the product consequence only after that attempt's own terminal/error or its one projected
   unknown outcome. A prior active completion must not release or decrement a queued employee handle.
   Leave the transport-attempt record unknown with the hs01 ingress as designed; do not manufacture a
   terminal, forward a later unrelated completion, or retry it.

Do not migrate human `send`, `stream`, commands, images, or Stop here; hs02 owns those paths. Extend
only the shared disposition-aware consequence seam if hs02 has not already done so. Do not change the
low-level child feed, open a new raw drain, or add employee-only transport state.

`EmployeeStepRunner` should need no new scheduler logic: its existing complete/interrupted/errored
mapping remains the sole Ticket/Chat settlement. Change it only if narrow glue is required to retain
the precise gateway error text. The existing worker Chat projection functions continue unchanged;
correct event filtering belongs below them at the gateway attempt boundary.

## Required regression coverage

Keep these current behaviors green while adding the tracer bullets:

- automatic proposal and no-proposal settlement, claim loss, takeover, concurrent claim, readiness
  recheck, doorbell, and shutdown drain in `tests/unit/test_employee_step_runner.py`;
- strict same-session revision delivery, stale-session failure, accepted handoff, and proposal
  settlement in `tests/unit/test_return_for_revision.py`;
- exact worker-context revision acknowledgement/retention in `tests/unit/test_worker_context.py` and
  the focused SharedGateway context cases;
- multiple employee sessions remain independent in the shared employee-role child.

## Permitted files

- `src/planner/minds/contracts.py` — use hs01 contracts; add no product status.
- `src/planner/minds/sessions/service.py` — the same shared per-attempt consequence handle as hs02,
  only if not already present.
- `src/planner/minds/shared_gateway.py` — employee `run_ticket_step` migration only.
- `src/planner/runtime/employee_step_runner.py` — narrow result-text glue only if required.
- `tests/unit/test_minds.py`
- `tests/unit/test_employee_step_runner.py`
- `tests/unit/test_return_for_revision.py`
- `tests/unit/test_worker_context.py`
- this ticket's plan/review/report files.

No `gateway.py`, human-chat service, readiness/claim writer, database, frontend, Hermes, or unrelated
documentation change belongs to hs03.

## Verification

Run the focused minds, employee-runner, revision, and worker-context tests after the final GREEN
slice, then Ruff, Mypy, and `git diff --check`. After hs03 lands serially in the feature worktree, run
the authoritative full `./verify` once before advancing to hs04.
