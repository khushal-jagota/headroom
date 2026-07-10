# t_hs02 implementation review resolutions

## 1. Idle consequence cleanup and reopen

Finding: a completed human consequence never detached its lightweight live-session record, so a
later product reopen reused the cached live ID instead of resuming the durable stored key.

RED: a public `SharedGateway` regression completed and released a first send, then sent again with
the stored key. The second prompt used the old live ID and the test hung waiting for the resumed
session's event.

Resolution: routed consequence accounting now remains pending until both Hermes terminal state and
product release are observed. At that settlement boundary, an idle session with no other admitted
operation or consequence detaches automatically. The next send performs `session.resume`, preserves
the durable key, binds the returned live ID, and submits there.

GREEN: `test_completed_released_human_session_reopens_through_resume`.

## 2. TransportUnknown cannot own later observations

Finding: an unknown submission stayed at the head of the routable waiting lane. A later accepted
submission's lifecycle was assigned to the unknown record and the accepted consumer hung.

RED: unknown A followed by accepted streaming B delivered no observations to B.

Resolution: unknown delivery still increments honest unknown accounting and prevents dormancy, but
its provisional consequence is removed from waiting/active routing and its buffered observations
are discarded. There is no retry, timer, ID, or local scheduler. A later native accepted
submission owns its own observed lifecycle exactly once.

GREEN: `test_unknown_submission_never_owns_a_later_accepted_lifecycle`.

## 3. Pre-receipt terminal on an already-running resumed session

Finding: when a resumed snapshot said Hermes was running but Panels had no local active
consequence, a terminal event arriving before the prompt receipt was dropped. A later `steered`
receipt then waited forever.

RED: the deterministic resumed-running test emitted terminal before the RPC receipt. `steered`
hung; `queued` correctly did not claim the old terminal.

Resolution: provisional unknown-disposition submissions buffer current-lifecycle observations even
when the current lifecycle has no local consequence. The immutable receipt decides their meaning:
`steered` replays and settles from that lifecycle, while `queued` discards it and waits for Hermes's
next lifecycle.

GREEN:

- `test_pre_receipt_terminal_on_resumed_running_session_uses_native_disposition[steered]`
- `test_pre_receipt_terminal_on_resumed_running_session_uses_native_disposition[queued]`

## 4. Atomic command/dispatch to derived prompt write

Finding: `slash.exec`/`command.dispatch` released the same-session operation lane before a
model-backed result was prepared and submitted. A concurrent human message could write its prompt
between the command RPC and the command's derived prompt.

RED: a blocking worker-context preparation seam made the interleaving deterministic. The concurrent
message wrote `prompt.submit` first.

Resolution: `LiveSessionOperation` admits one compound same-session write operation. Command RPC,
one alias dispatch when needed, context preparation, consequence registration, and the derived
`prompt.submit` write all occur under that operation lane. The lane releases before waiting for the
prompt receipt or consuming model lifecycle observations. Ordinary message and image submission use
the same admission accounting, so idle cleanup cannot invalidate a caller already waiting for the
lane.

GREEN: four parameterized regressions cover sync and streaming calls, direct model commands and
alias-to-model dispatch. All assert derived command prompt precedes concurrent human B.

## 5. Hermes merged pending queue slot

Finding: Hermes merges multiple busy queued submissions into one pending prompt. Panels assigned
the one next lifecycle only to B, leaving queued C open forever.

RED: A streaming, B queued, C queued, A terminal, then one next start/delta/terminal lifecycle.
B completed; C timed out.

Resolution: when Hermes starts the next lifecycle for a queued consequence, all adjacent accepted
`queued` consequences in that one native pending slot become observers of the same merged
lifecycle. Each settles once from Hermes's terminal event. Panels does not send later, promote work,
or create a queue of its own.

GREEN: `test_multiple_queued_submissions_share_hermes_merged_next_lifecycle`.

## 6. Unknown outcome is an observational lifecycle barrier

Finding: removing timed-out A from the routable lane was not enough. Accepted queued B could still
claim A's later lifecycle because there was no local record that the next observed lifecycle was
ambiguous.

RED: A timed out, B received Hermes `queued`, then A's delayed lifecycle arrived before B's own.
B incorrectly completed with A's reply.

Resolution: an unknown routed submission leaves an observational barrier, not a local work item.
The ambiguous lifecycle is consumed through its Hermes terminal without being assigned to B; B
then owns its native next lifecycle exactly once. A native `streaming` or `steered` receipt can
clear the barrier according to Hermes semantics. Unknown accounting remains honest. There is no
ID, timer, retry, promotion, or scheduler.

GREEN: `test_unknown_current_lifecycle_is_a_barrier_for_later_queued_submission`.

## 7. Response-frame order owns pre-receipt queue routing

Finding: B and C response frames could both be received as `queued`, but routing still depended on
their waiter threads copying those dispositions into local state. If the merged lifecycle arrived
first, only B was assigned.

RED: both response frames were delivered, neither waiter ran, and the merged lifecycle was fully
routed before either caller called `wait()`. C received nothing.

Resolution: each consequence retains its request handle. Before routing a later Hermes event, the
single ingress reconciles any already-received successful response frames in wire order without
consuming them. The normal caller still validates and returns the immutable receipt. B and C now
share the one Hermes merged lifecycle independent of caller scheduling.

GREEN: `test_merged_queue_routing_uses_response_frames_before_waiter_scheduling`.

## 8. Shutdown closes admission before waiting on operation lanes

Finding: a compound command held `command_lock` while waiting for `slash.exec`; shutdown waited for
that same lock before cancelling the request handle, creating a self-block until request timeout.

RED: a withheld `slash.exec` response left shutdown blocked and allowed the compound operation to
remain live.

Resolution: shutdown marks admission closing and cancels tracked request handles before acquiring
session lanes. Locked request writes now re-check live admission while holding the manager gate, so
the cancelled operation cannot write its derived prompt after shutdown begins.

GREEN: `test_shutdown_cancels_compound_operation_before_waiting_for_its_lane`.

## 9. Reuse-to-admission detach race

Finding: streaming yields the durable session chunk after live-session reuse lookup. The previous
turn could terminal, release, and auto-detach during that yield, so continuing the new generator
failed before writing its prompt.

RED: A streamed, B reused A's live session and yielded its session chunk, A then interrupted and
detached, and B continuation raised `gateway_offline`.

Resolution: the human write seam resolves the current live lease immediately before admission. If
that returned lease detached before admission, the typed dormant signal permits exactly one
`session.resume` and a first prompt write on the resumed live ID. No prompt outcome is retried.

GREEN: `test_stream_retry_persists_rotated_key_after_reused_session_detaches`.

## 10. Known active ownership outranks an unknown barrier

Finding: timed-out B installed the unknown lifecycle barrier even while accepted A was actively
streaming. The barrier ran before active routing, swallowed A's terminal, and left A open forever.

RED: accepted A received start, B timed out, then A's known terminal arrived and was not delivered.

Resolution: the barrier is consulted only when no accepted consequence currently owns the
lifecycle. A receives and closes on its terminal; B's ambiguity remains quarantined for the later
unowned lifecycle.

GREEN: `test_unknown_barrier_does_not_swallow_known_active_terminal`.

## 11. Pre-receipt steered lifecycle survives an unknown barrier

Finding: after unknown A, B's delta and terminal could arrive while B's `steered` response frame
was deliberately withheld. The barrier discarded those observations, so the later native receipt
left B waiting forever.

RED: the router fully observed B's shared delta and terminal before the steered response became
visible to the request handle.

Resolution: while protecting later queued work, the barrier provisionally buffers observations
for submissions whose native disposition is not yet known. A later `steered` receipt replays and
settles that buffered lifecycle. A later `queued` receipt discards it and waits beyond the
ambiguous lifecycle as before.

GREEN: `test_unknown_barrier_buffers_pre_receipt_steered_lifecycle`.

## 12. Ready RPC rejections leave routing before later lifecycle

Finding: A's RPC error response could already be received while its caller had not yet run. The
router only peeked successful responses, so it assigned accepted B's lifecycle to already-rejected
A.

RED: A's prompt waiter was deliberately paused after Hermes's error frame arrived; B's accepted
streaming lifecycle was then fully routed before A's waiter resumed.

Resolution: the request handle exposes a non-consuming peek for an already-received RPC rejection.
The ingress removes and closes that consequence before routing the next event. The caller still
receives the original `GatewayRpcError` and performs image compensation exactly once; the router
does not consume the response or perform side effects.

GREEN: `test_ready_rpc_error_is_removed_before_later_lifecycle_routing`.

## 13. Race-time resume propagates a rotated durable key

Finding: the dormant-session retry used the resumed live ID for the prompt but discarded Hermes's
rotated stored key. Final send/stream results and the persistence callback kept the old key.

RED: both message and model-backed command streaming resumed from the post-session-chunk detach to
`OTHER_KEY`; a synchronous send reproduced the same race while worker-context preparation was
paused. Prompts reached the new live session, but result keys stayed stale.

Resolution: the shared human write and command seams return the effective stored key with their
accepted operation. If resume rotates it, the persistence callback receives the new key and the
final `ChatSendResult`, `CommandRunResult`, or done chunk carries it. The initial stream session
chunk remains unchanged, and each path still writes one prompt only.

GREEN:

- `test_stream_retry_persists_rotated_key_after_reused_session_detaches[message]`
- `test_stream_retry_persists_rotated_key_after_reused_session_detaches[command]`
- `test_send_retry_persists_rotated_key_after_prepare_time_detach`

## 14. Known image rejection cleanup precedes later prompt writes

Finding: early router reconciliation correctly removed rejected image prompt A from lifecycle
routing, but it also cleared the last ownership preventing dormancy before A's waiter performed
`image.detach`. If B completed first, A could lose its live lease and mask the original RPC error;
even without detaching, B could write while A's rejected attachment was still staged.

RED: A attached an image and Hermes's prompt error frame became ready while A's waiter was paused.
The router observed and removed A, then B wrote its prompt before A resumed and detached.

Resolution: each session has a narrow prompt-admission gate. An image-bearing submission holds it
from attachment through native prompt acceptance or known rejection compensation. Later ordinary
or model-derived prompt operations take it only around their prompt write, never across receipt
waiting or model lifecycle. A blocked prompt releases the broad command lane while waiting, so
Stop remains available. Transport-unknown image delivery releases the gate without detach, retry,
or any delivery guess.

GREEN: the strengthened
`test_ready_rpc_error_is_removed_before_later_lifecycle_routing` proves the exact order
`image.attach -> rejected prompt -> observation -> interrupt -> image.detach -> B prompt`, one
detach, B completion, and A's original `GatewayRpcError(4019)`.

## 15. Compensation failure cannot mask the prompt rejection

Finding: the known-rejection path used bare `raise` only after `image.detach`. If detach itself
failed, its error replaced Hermes's original prompt rejection even though the admission gate was
eventually released.

RED: the item 14 regression now makes `image.detach` return RPC error 4020 after prompt rejection
4019.

Resolution: known-rejection compensation remains exactly-once and best-effort. Its gateway failure
is contained inside the original exception handler, which rethrows Hermes's prompt error 4019.
The prompt-admission `finally` still releases the gate, so B writes and completes afterward; Stop
continues to succeed while B waits.

GREEN: strengthened `test_ready_rpc_error_is_removed_before_later_lifecycle_routing`.

## Focused verification

- `tests/unit/test_minds.py tests/unit/test_minds_sessions.py tests/unit/test_chat_seed.py`: 123
  passed with the existing Starlette/httpx warning.
- `tests/unit/test_chat*.py tests/unit/test_return_for_revision.py`: 93 passed with the existing
  Starlette/httpx warning.
- Employee auto-advance compatibility regression: passed.
- Ruff: passed.
- Mypy: passed for all 104 source files.
- `git diff --check`: passed.

Full `./verify` was intentionally not run in this review-fix cycle; it follows the independent
follow-up review.
