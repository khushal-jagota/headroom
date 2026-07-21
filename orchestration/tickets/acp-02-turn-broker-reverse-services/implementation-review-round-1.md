# ACP-02 implementation review — round 1

## Verdict

**NOT READY**

The official-SDK bridge, ordered response-consumption barrier, descriptor-backed filesystem,
Hermes-only normalization boundary, and the ordinary FIFO/Send Now flow are directionally correct,
and the recorded focused gate is green. Four concrete ownership failures remain in the race and
shutdown behavior that this ticket explicitly freezes. The first one can deliver a prompt to the
wrong child generation. The others can leave callbacks or cleanup tasks unresolved.

## Spec

### [P0] 1. A concurrent same-binding runtime refresh can redirect an accepted generation-N prompt into N+1

The actor is specified as the single writer of its runtime handle, but `_actor()` replaces
`actor.handle` directly under the broker's lookup lock whenever a caller presents a newer record
identity (`turn_broker.py:1290-1319`). That mutation is outside the actor command queue. `_start()`
publishes `accepted` before it acquires the runtime lease, and it acquires the lease from the mutable
`self.handle` rather than from the handle submitted with that delivery (`turn_broker.py:229-261`).

Consequently, if publication of generation N's accepted receipt is paused, a concurrent call using
the respawned same-binding N+1 handle changes the actor immediately. When N's command resumes, it
acquires N+1 and sends N's prompt to N+1. A narrow reproduction against the production broker
produced:

```text
child1 prompts []
child2 prompts ['first']
```

This violates the exact-record lease, stale-work rejection, actor single-writer, and no-redirection
rules in reviewed plan sections 1 and 3. Runtime adoption must itself be serialized as an actor
command with a monotonic generation/record-identity check, and each delivery command must either use
its submitted exact handle or reject it as stale; it must never reread a handle that another caller
can replace while the command is awaiting publication. Add a latch test at the accepted-receipt
publication boundary proving that N's prompt cannot reach N+1.

### [P1] 2. Commands submitted after actor-runner exit have no settlement owner

`_Actor.run()` returns once lifecycle is `closed` and the command queue is empty
(`turn_broker.py:127-146`). `_command()` unconditionally enqueues and shield-awaits a new future
without checking lifecycle or `runner.done()` (`turn_broker.py:1321-1325`). The external delivery
path happens to reject a closed actor in `_actor()`, but child-death, permission-publication-failure,
and terminal-publication-failure callbacks retrieve the existing actor directly and call `_command()`
(`turn_broker.py:960-1027`). A callback that arrives after `_close_actor_steps()` has awaited the
runner is therefore queued behind a task that no longer exists.

The direct production reproduction was:

```text
after close closed True
late callback HUNG
```

This contradicts the implementation report's claim that closed actors reject late callbacks and the
contract's generation-guarded/idempotent callback rule. `_command()` needs an atomic admission rule
that fails or no-ops the future when the actor is closed/failed with no live runner, including the
runner-exit race between the check and enqueue. Add late child-death and both reverse-service failure
callback tests after `prepare_new_conversation()` and after shutdown.

### [P1] 3. The shared shutdown deadline is followed by unbounded cancellation waits

The broker waits only until the absolute deadline, but then cancels pending close tasks and awaits
them with an unbounded `gather`; it later does the same for actor runners
(`turn_broker.py:1087-1120`). `_close_actor()` also cancels and unboundedly gathers its inner steps and
runner after timeout (`turn_broker.py:1140-1148`). The permission and terminal owners repeat the same
shape after their deadline expires (`permission_broker.py:350-364`,
`reverse_services/terminal.py:589-612`). Terminal `_release()` additionally raises on a watcher
deadline without disposing the live handle (`reverse_services/terminal.py:545-559`); `_release_many()`
only force-disposes tasks that were still pending, not a release task that already returned that
error.

A publisher that suppresses its first cancellation kept production `shutdown(deadline)` unfinished
at 151 ms for a 30 ms deadline, and it was still unfinished immediately after the publisher was
released:

```text
shutdown_done_after_150ms False elapsed 0.151
shutdown_done_after_release False
```

The existing hard-deadline test uses a normally cancellable publisher, so it cannot detect this.
All post-deadline cancellation/disposal must remain bounded by the same absolute deadline, report
the exact unfinished employees/terminal IDs, and leave no owned prompt, runner, permission, reader,
watcher, publisher, process, or live terminal handle. Add cancellation-resistant latches for actor
publication, permission outcome publication, terminal output/release publication, and decoder/reader
completion as required by the reviewed acceptance map.

### [P1] 4. Permission publication failure can revoke a visible selection or strand the ACP callback

Permission settlement treats outcome publication and activity restoration as one transaction
(`permission_broker.py:419-472`). If the exact selected outcome publishes but the following activity
publication fails, the code overwrites the agent response with ACP `cancelled` at lines 455-459. The
browser has already seen the user's selected option while the agent receives a denial, violating the
first-valid-response rule.

There is also a re-entrant cancellation failure. Active cancel owns a
`permission_cancel_task` (`turn_broker.py:447-471`). If its outcome publication fails,
`_notify_publication_failure()` awaits a callback into the same actor
(`permission_broker.py:467-488`, `turn_broker.py:975-1003`). The actor's failure command then cancels
and awaits that very permission task (`turn_broker.py:556-577`). Because the pending record was
already marked `settling`, generation cancellation skips it, and the shielded ACP permission future
is never resolved. A narrow production exercise ended with:

```text
permission_callback_done False actor failed runner_done False active False
```

Commit the exact selected/cancelled ACP response once outcome publication succeeds; an activity-only
failure may be generation-fatal but cannot change that response. Publication-failure notification
must not synchronously await an actor command from a task that the command cancels. Add both an
activity-after-outcome failure test and an active-cancel outcome-publication failure test, asserting
one terminal ACP response and no pending settlement task.

## Standards

### [P3] 5. Two frozen state types are weakened to `str`/`Any`

`_TerminalHandle.lifecycle` is a plain `str`, forcing a type suppression when constructing the
frozen `ConversationTerminalState` (`reverse_services/terminal.py:58-70,681-690`). `_Actor.activity()`
similarly accepts `Any` even though the publisher requires `ConversationActivityState`
(`turn_broker.py:224-227`, `runtime_event_publisher.py:26-32`). These are not current runtime
failures, but they bypass the contracts-first static boundary. Retain the existing literal types and
remove the suppression.

The other standards-axis suggestions to add capture methods to the frozen `AcpEmployeeChild`
protocol, remove the reviewed generation-bound prompt port, or replace the actor state machine were
not accepted: the reviewed plan deliberately requires those exact seams.

## Confirmed areas

- The SDK client callbacks use the installed official ACP models and truthful declared reverse-
  service checks; no parallel JSON-RPC implementation was added.
- Prompt response-consumption waits through the observer ordinal, and a fresh unpublished capture
  generation routes load replay to the private typed sink before publication.
- Planned retirement distinguishes an exact intentional close from unexpected child death, and the
  generation-bound Hermes Steer path does not resolve a replacement child by binding.
- Queue head removal, FIFO snapshots, ordinary single delivery, Send Now priority, exact Hermes
  provenance parsing, structural summary ambiguity failure, and generic-vs-Hermes source boundaries
  match the reviewed design in the non-racing path.
- Filesystem I/O is descriptor-backed with no-follow traversal and resists the specified final-path
  swaps. Terminal spawn uses literal argv, confined cwd, injected environment, and bounded typed
  output snapshots.

No `./verify` was run. The review used current source inspection, the recorded focused evidence, the
installed SDK interface signatures, and the three narrow race exercises quoted above.
