# ACP-05 requested-cancel recovery review corrections

## Disposition

Every finding in `implementation-review.md` is resolved. This is a bounded correction record, not a
second broad review.

### P1 — timeout abandoned the actor deadline and could expose N

Resolved. `_cancel_timed_out` reuses `active.requested_cancel_deadline`; it does not create another
budget. Registry planned retirement applies that same deadline to employee-gate acquisition, exact
record validation, child close, and planned-death settlement. Any error, cancellation, or expiry
consumes the plan, removes the exact record/callback identity, and detaches child close. Broker failure
settles predecessor, successor, and FIFO once and never publishes reusable idle.

Proof: the broker deadline/invalidation control and the registry cancellation-resistant exact-
retirement control pass, alongside the admitted-old-ingress, fresh initialize/death, durable-binding
drift, and hard-deadline controls.

### P1 — failed close-owned retirement was swallowed

Resolved. Exceptional New Conversation/shutdown prompt settlement invalidates the exact lease on
retirement failure, records the close failure, fails the generation, and returns the failure to the
close owner. The owner force-disposes the actor and does not continue into runtime New Conversation.
Error and hanging-retirement controls cover both close causes under the original caller deadline and
prove no recovery, idle, successor, or old-child reuse.

### P2 — failure/deadline and official-SDK proof matrix was incomplete

Resolved. The registry suite now covers admitted ingress, candidate initialize/death, durable-binding
drift, and one hard replacement deadline. The broker suite covers reverse-service cleanup, replacement
error, invalid replacement, hub commit, requested-recovery deadline, and exact timeout invalidation.
The production hub test continues to prove unexpected old-child death is owned once.

The official-SDK Stop/Send Now test now installs real generation-N worker collectors and proves both
remain empty, requires `reset -> ready -> queue_snapshot -> successor started`, and observes the exact
marked successor answer once. Its existing external audit still proves both late old-process sends
completed while their text remained absent from the product transcript.

## Verification

The final affected gate is recorded in `focused-checks.txt`: Ruff passed, strict Mypy passed, all 119
named Python unit/e2e tests passed, all four ACP browser suites passed, and Svelte check reported zero
errors and zero warnings. Canonical `./verify` was not run because ACP-10 owns it.
