# ACP-02 implementation review — round-one correction check

## Verdict

**READY**

The bounded correction resolves all five findings from
`implementation-review-round-1.md`. No finding remains open.

## Finding-by-finding disposition

### 1. Concurrent runtime refresh could redirect generation-N work — RESOLVED

Normal and queued submissions now retain the exact `ConversationRuntimeHandle` that admitted them.
Runtime adoption occurs inside the actor command queue, `_actor()` no longer mutates an existing
actor's handle, and `_start()` revalidates then leases the submitted handle on both sides of the
accepted-receipt publication await. The controlled compaction-capture handoff is the sole serialized
replacement path and rebases only FIFO entries owned by the replaced record identity.

The accepted-publication latch regression proves that concurrent N+1 admission cannot redirect an
accepted generation-N prompt. The capture regression separately proves that the reviewed N-to-N+1
FIFO handoff still advances on the exact replacement handle.

### 2. Late commands could be admitted after actor-runner exit — RESOLVED

Command admission and runner exit now share `command_admission_lock` plus the
`accepting_commands` state. `_command()` rejects a closed, runnerless, or completed actor before
enqueueing, while the runner finalizer settles its current and already-admitted command futures.
Late child-death, permission-publication-failure, and terminal-publication-failure callbacks treat
that rejection as an already-closed generation.

The parametrized regression covers all three callbacks after both new-conversation closure and
broker shutdown and proves that none hangs.

### 3. Shutdown exceeded its absolute deadline during cancellation — RESOLVED

Broker, permission, and terminal cleanup no longer follow deadline expiry with an unbounded gather.
At the original absolute deadline they cancel and synchronously remove owned actor commands/tasks,
permission records/tasks, and terminal handles/tasks. Terminal force-disposal also kills a live
process before detaching cancellation-resistant reader, watcher, or publication coroutines. The
unfinished owner identifiers remain the typed cleanup result; detached coroutines no longer retain
an owner table entry from which work can resume.

Cancellation-resistant regressions hold actor publication, permission outcome publication,
terminal release publication, and terminal reading across cancellation. Each returns within the
hard deadline, clears the corresponding ownership tables, and preserves the no-prompt/no-live-
terminal outcome after the held coroutine is released.

### 4. Permission failure could revoke or strand the ACP response — RESOLVED

Successful outcome publication is now the response commit point. The pending record is removed and
its ACP future is resolved before activity restoration. A later activity-publication failure can
fail the owning generation but cannot rewrite the already-selected response. If outcome publication
itself fails, the broker resolves exactly one cancelled response. Publication-failure notification
runs as a separately tracked task instead of synchronously re-entering an actor from the permission
task that actor cleanup owns.

The active-cancel regression proves that outcome-publication failure resolves the callback, rejects
queued work, starts no successor prompt, and leaves no pending settlement. The activity-failure
regression proves that a visible selected option remains the agent's terminal response.

### 5. Frozen lifecycle/activity types were weakened — RESOLVED

`_Actor.activity()` now accepts `ConversationActivityState`, `_TerminalHandle.lifecycle` now uses
`ConversationTerminalLifecycle`, and the terminal snapshot constructs the frozen state without a
type suppression.

## Independent correction evidence

The reviewer reran only the deterministic correction set named in the implementation report:

```text
...........                                                              [100%]
11 passed
```

The implementation record also contains green scoped Ruff, strict Mypy, the 214-test affected ACP
Python acceptance set, and scoped `git diff --check` output. No web source changed during the
correction, and no web gate or canonical `./verify` was rerun for this bounded review.
