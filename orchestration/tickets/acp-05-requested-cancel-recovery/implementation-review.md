# ACP-05 requested-cancel recovery implementation review

## Verdict

`NOT READY`

Two behavioral blockers and one required-proof gap remain. This was a read-only source/test review;
no tests were run.

## Findings

### P1 — Cancellation timeout abandons the actor deadline and can expose generation N again

`_begin_cancel` stores the requested-cancel deadline on the active turn
(`turn_broker.py:1128-1145`), but `_cancel_timed_out` creates a new
`loop.time() + cancel_timeout_seconds` deadline (`turn_broker.py:1263-1272`). Production config makes
both timeouts 10 seconds, and the hub timer starts before the cancellation timer, so the quarantine
deadline can expire first and the timeout cleanup can then run for another full budget. That violates
the one actor-owned absolute deadline.

The fallback is also not fail-closed. `retire_runtime_lease` failure is swallowed, but neither the
timeout branch nor `_fail_generation` calls `fail_runtime_handle`. Registry retirement does not clean
up a failed/timed-out planned retirement, and `get_or_spawn` returns a live `_records` entry without
checking whether its callback generation was removed (`employee_registry.py:172-183,1431-1471`). A
later attach can therefore reload and republish the indeterminate generation N that the timeout was
required to make generation-fatal.

Required correction: reuse `active.requested_cancel_deadline` for every requested Stop/Send Now
timeout operation, make exact retirement itself deadline-bounded from gate acquisition through
settlement, and invalidate/detach the exact handle if retirement cannot prove completion. Add a test
where the quarantine deadline wins and retirement resists cancellation; assert elapsed time stays
inside the original deadline, N is not resolvable/reusable, all intent settles once, and all gates and
owned tasks release.

### P1 — Failed close-owned retirement is swallowed before New Conversation reuses the old child

For exceptional `new_conversation`/`shutdown` unwind, `_settle_prompt` catches
`retire_runtime_lease` failure, calls `_fail_generation`, and returns normally
(`turn_broker.py:542-552`). `_close_actor_steps` can consequently complete, and `hub.new_conversation`
continues into `registry.new_conversation`. Because the failed registry retirement may leave the live
old record in `_records`, `_replace_conversation -> get_or_spawn` can select that exact indeterminate
child and call `new_session` on it. This contradicts the frozen rule that close failure never marks the
exceptional cancelled child reusable.

Required correction: on retirement failure, invalidate the exact lease/record and propagate failure
to the close owner; New Conversation must not proceed on that child. Add both New Conversation and
shutdown failure controls with a retirement that hangs/errors, proving no recovery-port call, no
same-session idle/successor, no old-child reuse, and completion bounded by the caller's original
deadline.

### P2 — The claimed failure/deadline proof matrix was not implemented

The focused suites have only four requested-cancel registry tests and do not cover admitted-old-ingress
quiescence, fresh initialize/death, durable-binding drift, or a hard replacement deadline. Broker
recovery failure coverage parameterizes only replacement and hub commit; it omits reverse-service
cleanup, invalid replacement, requested recovery deadline, and the production unexpected-death
disposition. These omissions leave both P1 paths above undetected despite `focused-checks.txt` being
green.

The official-SDK test correctly proves the post-cancel late-send audit and fresh same-binding child,
but its Send Now assertions do not require the queue snapshot before successor start or assert the
successor's exact visible answer, and it installs no worker collector while claiming absence from
worker collection (`test_acp_conversation.py:1259-1332`). Complete the contract's named deterministic
proofs before treating the focused run as acceptance evidence.

## Reviewed behavior that is sound

- Quarantine begin is awaited before ACP cancel; normal `PromptResponse` resumes held ingress before
  the prompt-settlement barrier, while exceptional recovery commit discards it.
- Planned replacement death is transaction-owned, and unexpected exact-old death is routed through
  one hub failure before matching broker settlement.
- Successful replacement keeps employee/binding/session exact, increments child generation, changes
  child/record identity, privately captures replay, and atomically publishes reset/replay/ready/queue.
- Successful Stop and Send Now retarget the frozen handles and settle predecessor/successor/FIFO once;
  the official late-send audit demonstrates a real post-cancel SDK send attempt.

No other concrete violation was raised in this bounded pass.

## Correction check

### Verdict

`READY`

The three findings above are resolved. This was only the requested correction check; no broader
review or test run was performed.

- **Original actor deadline and exact-N invalidation — resolved.** `_cancel_timed_out` passes the
  stored `requested_cancel_deadline` into exact retirement. Registry retirement applies that same
  absolute deadline to gate acquisition, exact-record proof, child close, and death settlement; its
  `BaseException` path removes the matching record, callback generation, and record identity, then
  detaches any still-live child close. The broker timeout control proves the original token deadline
  reaches retirement and all frozen intent settles once; the cancellation-resistant registry control
  proves deadline return, exact-N unresolvability, gate reuse, and fresh same-binding demand.
- **Close-owned retirement failure — resolved.** Exceptional New Conversation/shutdown retirement
  failure invalidates the exact handle, records `close_retirement_failure`, fails the actor, and is
  re-raised by `_close_actor_steps`; `_close_actor` force-disposes on either error or deadline. Since
  `ConversationHub.new_conversation` awaits that broker close before calling
  `registry.new_conversation`, it cannot reuse the old child. The `new_conversation`/`shutdown` ×
  `error`/`hang` matrix proves the caller deadline, no recovery-port replacement, no idle, exact
  handle failure, dead/unresolvable N, and no same-session successor.
- **Required proof matrix — resolved.** The registry suite now has deterministic admitted-ingress,
  candidate-death-during-initialize, durable-binding-drift, private-load hard-deadline, and
  cancellation-resistant retirement controls. The broker matrix covers reverse-service cleanup,
  replacement failure, invalid replacement, hub commit, and requested-recovery deadline; the hub
  test exercises the production unexpected-old-death disposition. The official-SDK e2e asserts
  `reset -> ready -> queue_snapshot -> successor started`, the exact marked successor answer once,
  FIFO start once, completed late-send audit, and empty production hub worker collectors installed
  against each exact generation-N predecessor.

`implementation-review-corrections.md` and `focused-checks.txt` accurately describe the corrected
source and named assertions. Their recorded focused gate is green; it was inspected, not rerun.
