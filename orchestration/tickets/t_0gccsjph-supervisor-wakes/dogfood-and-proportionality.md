# Supervisor wake dogfood and proportionality

## Runtime boundary

The dogfood exercised branch `ticket/t_0gccsjph-supervisor-wakes` at
`60181b826d35e188ad919e8684f5de3559f9d654`.

- Isolated root: `/tmp/panels-wake-dogfood-final-svBWEW`
- Server: `127.0.0.1:32797`
- Mode: `PLAN_TEST_MODE=1`, fixed clock `2026-09-22T12:00:00+02:00`
- Poll interval: `PLAN_TICK_SECONDS=60`, the repository default
- Isolated state: `planner.db`, `dispatcher.lock`, `logs/`, `backups/`,
  `server.sock`, and `hermes-home/` all lived below the isolated root.
- `PLAN_APP_ROOT` named this ticket worktree.

The ignored dogfood harness ran the real FastAPI application, SQLite conversation
system and store, `BackgroundLoops`, Worker proposal API, Worker readiness path,
manager-wake loop, and durable wake store. It replaced only the backend child with the
repository scripted backend adapter. The conversation system itself was not faked. A
small delegating wrapper returned `PromptDeliveryUncertain` only for one explicitly
armed Worker opener; every manager send delegated to the real SQLite conversation
system. The ignored harness was not committed.

No live deployment database, path, port, process, or staging integration was read or
written. The isolated server was stopped cleanly after the run.

## Exercised flows

### Idle manager proposal

1. Worker `t_wrfh3ct2` filed its proposal through
   `POST /api/tickets/t_wrfh3ct2/propose`.
2. The real wake loop created wake 1 and delivered batch 1 into newly linked manager
   conversation `conv_0fa7fe71786b4269bf7617d6760d9773`.
3. Conversation event 1 was the exact durable `prompt` carrying batch 1's
   `sender_message_id`.
4. Only after that row existed did batch 1 become `delivered` and wake 1 close.

The scripted backend had one active write and zero cancellations.

### Active manager, real Worker error plus proposal

1. The manager's first turn remained genuinely active. Background loops were stopped
   without completing or interrupting that turn.
2. Error Ticket `t_50vk6fxm` was put on Today, armed, and driven through
   `POST /api/test/run-step/t_50vk6fxm`. The real readiness flow claimed the Worker
   step, received `PromptDeliveryUncertain`, and atomically wrote
   `worker_step_claim=errored`, claim revision 2, and worker-error wake 2 with source
   revision 2.
3. Worker `t_zc38hg09` filed a proposal through its real proposal API, creating
   proposal wake 3 with source revision 1.
4. Background loops restarted while the manager was still active. Both wakes remained
   open, the incumbent remained the only backend write, and cancellation count stayed
   zero.
5. Completing the incumbent recorded `turn_ended` event 2. The wake loop then created
   batch 3 with wake members 2 and 3 and delivered one combined message.
6. Exact `prompt` event 3 carried batch 3's `sender_message_id`; only then did the batch
   become `delivered` and both wakes close.

The backend finished with two writes and zero cancellations.

### Restart with an undelivered wake

1. While the combined manager turn was active, Worker `t_dhpq6rxp` filed a proposal,
   creating wake 4 with source revision 1.
2. The active-manager preflight left wake 4 open and without a retained batch.
3. The server stopped cleanly. The same isolated database was restarted with real
   background loops enabled; no in-memory backend or held prompt survived the restart.
4. The restored manager conversation was idle. The wake loop created batch 5 and sent
   the wake through the normal conversation path.
5. Exact durable `prompt` event 4 carried batch 5's `sender_message_id`; batch 5 then
   became `delivered` and wake 4 closed.

The restarted backend observed exactly one write and zero cancellations.

## Final durable state

- Ticket `t_50vk6fxm`: `worker_step_claim=errored`, claim revision 2.
- Wakes 1 through 4: closed.
- Wake sources: proposal revision 1, worker-error revision 2, proposal revision 1,
  proposal revision 1.
- Batches 1, 3, and 5: `delivered` to
  `conv_0fa7fe71786b4269bf7617d6760d9773`.
- Batch membership: `1 -> [1]`, `3 -> [2, 3]`, `5 -> [4]`.
- Conversation events: prompt 1, completed turn 2, combined prompt 3, recovered prompt 4.
- Each prompt event contained the exact sender ID of its batch.

## Proportionality finding

The active-manager preflight claims a durable batch before checking whether the manager
is running, then quietly deletes that batch when it defers. Wake creation is the useful
external signal, but every later periodic poll and every unrelated commit carried by the
shared change signal repeats this claim/delete cycle while an open wake and active
manager coexist.

Observed mapping:

| Run | Tick | Active interval result |
| --- | ---: | --- |
| Stress investigation | 1 second | 68 transient batch IDs were minted and deleted |
| Clean final run | 60 seconds | one expected transient batch ID was minted and deleted |

This did not interrupt the manager, resend a message, leave durable batch rows behind,
or prevent wake combination. It is nevertheless real write and sequence-ID churn, and
unrelated application writes can amplify it through the shared signal.

The preflight is removable in a follow-up design because queue delivery already avoids
interrupting an active turn and can combine work for the later turn. Removing it should
retain the exact-prompt closure rule and restart-safe `accepted`/`dispatching` boundary;
the change should be judged on whether later wakes still coalesce into the intended
single manager turn.

Safe cleanup candidates, separate from this implementation, are:

- move the scripted backend adapter out of the private unit-test module into reusable
  `tests/support` infrastructure;
- turn the ignored dogfood harness into a maintained isolated-runtime helper if this
  scenario will be repeated;
- document the queue-mode/coalescing contract beside the wake runtime;
- keep any formatter-only cleanup confined to dedicated changes, especially in broad
  files such as `tickets/data.py`, resolution helpers, database tests, and backend
  conformance tests.
