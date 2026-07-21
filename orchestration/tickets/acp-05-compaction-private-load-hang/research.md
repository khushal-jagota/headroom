# ACP-05 compaction private-load hang research

Date: 2026-07-20  
Scope: read-only diagnosis of Panels, the pinned ACP Python SDK, and Hermes; no product or test changes  
Conclusion: **Panels has two independently reproducible ordering defects. Hermes does not hang.**

## Executive conclusion

The first and most immediate failure is at the private-capture boundary in
`OrderedAcpConversationIngress`:

1. Panels arms a private `session/load` epoch.
2. The official SDK's `send_request` awaits the write before notifying stream observers of the
   outgoing request.
3. During that yield, Hermes can deliver the forked session's scheduled
   `available_commands_update`.
4. Panels sees a private epoch whose request ID is still `None` and treats the valid notification as
   fatal: `AcpSessionUpdateCallbackMismatch: private ACP capture received an update before its load
   request`.
5. The child connection is retired while the load call is outstanding. The outer five-minute breaker
   eventually reports the private-load failure; required restore is then attempting to reuse the
   failed child/ingress.

This is not an invalid Hermes order. ACP notifications are asynchronous and session-scoped, and
official ACP material explicitly permits lifecycle updates after `session/fork`. The Panels capture
router must use the notification's `sessionId`, not the observation time of an outgoing request, to
decide whether an update belongs to the private fork.

There is also a separate deterministic lock/FIFO deadlock. `prepare_compaction_capture` holds the
per-employee publication/update gate across `fork_session`, private `load_session`, and restore.
Public ingress needs that same gate. Any older ordinary update at the ordered FIFO head blocks there,
which prevents later private replay slots from reaching the capture barrier. Releasing the gate
drains the complete prefix immediately. The existing ACP-02 plan already says generation transitions
use the gate only for short state marks and final publication/quiescence; the current implementation
does not follow that rule.

Both defects need focused correction. Fixing only the request-observer race still leaves a fresh
attach-then-compact or any concurrent ordinary session update able to deadlock. Changing the
five-minute breaker, adding sleeps, changing Hermes, or reconstructing private state from prose would
not correct either defect.

## Incident state

The durable Panels binding remained at generation 10 after the failed real compaction:

```text
$ sqlite3 -readonly data/planning.db \
  "SELECT employee_id,acp_session_id,backend_key,binding_generation
   FROM conversation_session_bindings
   WHERE employee_id='agent_panels_chief_of_staff';"
agent_panels_chief_of_staff|735aea7e-f76d-49bf-a4b9-0576846a5489|hermes|10
```

The read-only Hermes state snapshot contains the source and the official fork candidate, both small:

```text
id                                    source  parent_session_id  message_count
------------------------------------  ------  -----------------  -------------
735aea7e-f76d-49bf-a4b9-0576846a5489  acp                        8
7667eb9a-63fc-4ff4-a15c-3dc4ea9e9d76  acp                        7
```

The active adapter reports Hermes `0.18.2`. The official repository's current `main` at research
time was commit
[`8f33e39682ae27963c93c87ea13bd0a3c2a55ed8`](https://github.com/NousResearch/hermes-agent/commit/8f33e39682ae27963c93c87ea13bd0a3c2a55ed8).
Its relevant fork/load scheduling is still the same, so an upgrade is not a fix for this Panels bug.

## Reproduction A: private epoch starts before the outgoing observer

A deterministic no-model, same-child trace fails within its approximately 2.5-second total run.
Event-to-event milliseconds were not measured; the causal wire order was:

```text
prior source session/load completed
incoming response to session/fork
  candidate sessionId = 6ab03c51-...
Panels capture_load_session arms a private session/load epoch
incoming candidate session/update
  sessionUpdate = available_commands_update
  epoch.request_id = None
Panels raises AcpSessionUpdateCallbackMismatch
outgoing observer for session/load would run only after the SDK send await
connection/ordered ingress becomes fatal
```

The exact fatal text is:

```text
private ACP capture received an update before its load request
```

Controls:

- Loading the candidate on a fresh child succeeds in about 1.6 seconds.
- A fresh-child fork without the preceding source load can pass, showing why simplified tests can miss
  the race.
- Waiting one second after the prior source load does not prevent the candidate update from racing the
  next load. It is scheduled by `session/fork`, not merely leftover transcript replay.
- No model prompt is involved.

The causal Panels code is:

- `src/planner/conversation/sdk_child.py:523-547`: the private epoch is created before calling the SDK
  `load_session`.
- `src/planner/conversation/ordered_ingress.py:213-241`: only the outgoing observer assigns the load
  request ID.
- `src/planner/conversation/ordered_ingress.py:254-269`: any notification observed while the private
  epoch exists but its request ID is unset is fatal, without checking `params.sessionId`.

The pinned official SDK explains how the incoming notification can win. In
[`Connection.send_request`](https://github.com/agentclientprotocol/python-sdk/blob/0.11.0/src/acp/connection.py#L126-L137),
the sender is awaited before outgoing observers are notified. Its receive loop notifies incoming
observers before dispatch. Thus another task can receive an agent notification during the write
await; Panels cannot use the outgoing observer as the moment a private session begins to exist.

## Reproduction B: the registry gate blocks its own ordered ingress

The second harness used the real `SdkAcpEmployeeChild` and the same lock topology as
`prepare_compaction_capture`. It acquired the employee publication/update gate before fork; public
ingress used `async with gate`, exactly like the registry callback. With a three-second external test
timeout:

```text
1.564 employee-gate-held matches prepare_compaction_capture
1.564 fork-call
1.565 public-attempt sid=735aea7e-... type=available_commands_update gate_locked=True
2.062 fork-return 73235702-f94a-49f0-9edb-038c96a79332
2.062 private-call 73235702-f94a-49f0-9edb-038c96a79332
5.063 RED-TIMEOUT 3s: private load barrier did not settle
5.063 red-state public_delivered=0 private_count=0 alive=True
5.063 employee-gate-released
5.063 public-delivered source available_commands_update
5.063 public-delivered source usage_update
5.063 private-delivered candidate available_commands_update + replay prefix
5.063 private-delivered candidate trailing available_commands_update + usage_update
child remained alive; restore then returned in about 0.6s
```

This is a self-deadlock, not a slow callback:

- `src/planner/conversation/employee_registry.py:990-993` acquires the employee gate.
- The gate remains owned across fork and private load at lines 1022-1060, and across required restore
  at lines 1088-1092.
- `guarded_ingress` at lines 476-500 requires the same gate for every public update and holds it
  through the downstream sink.
- `OrderedAcpConversationIngress._consume` is one serial FIFO. A later private slot cannot pass an
  earlier public slot waiting on the gate.
- `finish_response_consumption_epoch` waits for the complete reserved prefix through the load
  response, so it correctly waits behind that older public slot.

The project had already designed against this. `orchestration/tickets/acp-02-turn-broker-reverse-services/plan.md`
states that generation transitions use the employee gate only for short state marks and final
publication/quiescence and warns that holding the update gate across an awaited child operation can
deadlock typed ingress.

## Backend controls: Hermes answers normally

A raw official Python ACP connection, with no Panels ordered-ingress or registry callback, ran the
whole sequence against the same source session:

```text
0.419 initialize returned
0.419 source load called
1.458 source load returned
1.458 fork called
1.782 fork returned candidate 473ea50b-ac01-4f9d-a250-9aa75631c596
1.782 candidate load called
1.782 IN  candidate available_commands_update
1.782 OUT candidate session/load
2.135 candidate load returned
2.135 source restore called
2.745 source restore returned
```

The important order is visible even in the green control: the candidate notification can be observed
before the outgoing load request observer. All backend RPCs completed in under three seconds.

The same exact `SdkAcpEmployeeChild` sequence with a nonblocking public ingress was also green:

```text
0.435 initialized
1.437 source load returned
1.817 fork returned candidate f64b6267-e326-4a11-9e9d-b0ad905df74c
2.279 private candidate load returned
2.611 source restore returned
```

This falsifies a Hermes private-load hang. A `RuntimeError: mssage queue already closed` appeared only
when deliberately closing the raw SDK harness after every operation had succeeded; it is unrelated to
the compaction failure.

## What Hermes intentionally emits

Read-only inspection of both the installed adapter and
[`acp_adapter/server.py` on official `main`](https://github.com/NousResearch/hermes-agent/blob/main/acp_adapter/server.py)
shows:

- `load_session` awaits transcript replay, schedules `available_commands_update` and `usage_update`,
  then returns its response object.
- `fork_session` creates the new session, schedules `available_commands_update` for the new session,
  then returns its response object.
- both schedulers use `loop.call_soon(asyncio.create_task, ...)`;
  `_schedule_available_commands_update` explicitly says the advertisement is sent after the session
  response is queued.

That scheduling makes the exact race unsurprising: the fork response unblocks Panels, Panels arms the
private load, and the already-scheduled candidate notification can run while the SDK is yielding in
its next request write.

No matching upstream Hermes issue or fixed release was found in the official issue/release/commit
searches. Official `main` retains the behavior, and it is compatible with ACP's asynchronous
notification model.

## Official ACP guidance

Primary sources support treating these updates as independent session traffic:

- [ACP architecture](https://agentclientprotocol.com/get-started/architecture) says one connection can
  support several concurrent sessions and that ACP makes heavy use of JSON-RPC notifications.
- The completed [Session Context Size and Cost RFD](https://agentclientprotocol.com/rfds/session-usage)
  says agents may send `usage_update` after `session/new`, `session/load`, `session/resume`, **or
  `session/fork`**, and at other times when state changes.
- [GitHub Copilot's official ACP documentation](https://docs.github.com/en/copilot/reference/copilot-cli-reference/acp-server#discovering-available-commands)
  says `available_commands_update` is a notification sent after create/load and whenever commands
  change; there is no request clients can use to fetch it on demand.
- Every `session/update` carries `params.sessionId`. The completed
  [Session Info Update RFD](https://agentclientprotocol.com/rfds/session-info-update) explicitly notes
  that the session ID is already present in notification params, and a forked session has its own ID.
- The completed [Rust SDK v1 RFD](https://agentclientprotocol.com/rfds/rust-sdk-v1#limitation-no-ordering-guarantees)
  calls out that the prior SDK cannot guarantee a notification is fully handled before another
  request's response. Panels' ordered consumer is reasonable, but it must not turn asynchronous,
  session-labelled traffic into a request-boundary fatal error.
- [Session fork](https://agentclientprotocol.com/rfds/session-fork) is still draft, but its purpose is
  exactly to create a separate session for work such as summaries without polluting the source.

Inference, stated explicitly: ACP does not appear to promise that all effects of `session/fork` are
delivered before the fork response or before the client's next request. Given concurrent sessions,
agent-pushed notifications, `sessionId` on every update, and official implementations that schedule
post-lifecycle updates, a client must route by session identity and tolerate valid inter-request
notifications.

No exact matching issue was found in the official ACP Python SDK issue search. The SDK behavior is
visible in its pinned source and is sufficient to reproduce the race; a Panels correction should not
depend on a hypothetical SDK change.

## Ranked falsifiable hypotheses

1. **Private epoch/request-observer race — confirmed and immediate.** Prediction: a candidate
   notification between epoch creation and outgoing observer sees `request_id=None`, raises the exact
   callback mismatch, and kills ingress. The deterministic no-model trace does exactly that. A
   session-ID-aware router would not fail it.
2. **Employee-gate/FIFO self-deadlock — independently confirmed.** Prediction: with the gate held,
   an older public slot blocks the private barrier; releasing only the gate drains everything without
   restarting Hermes. The exact-child harness does exactly that.
3. **Hermes itself hangs on candidate load — falsified.** Prediction: the raw SDK sequence or fresh
   child candidate load also hangs. Both complete in seconds.
4. **Transcript size or private sink is simply slow — falsified.** The real source/candidate contain
   only 8/7 messages; nonblocking exact-child capture completes in under three seconds. The red cases
   stop before private delivery (`private_count=0`).
5. **Process death/transport closure precedes the failure — falsified.** The child is alive throughout
   the gate-deadlock repro. The private-boundary repro first records the precise ordered-ingress fatal;
   connection retirement is an effect, not the initiating cause.

## Recommended narrow correction

### 1. Route a private capture by expected session ID

Arm the private capture with the fork candidate's exact `session_id`, not only method name and an
unset future request ID.

- Candidate-session notifications received after fork and before/during the private load go to the
  private sink.
- Source-session or other-session notifications remain on normal public ingress.
- The outgoing request ID still defines which `session/load` response freezes the capture barrier;
  it must not define which session owns a notification.
- Keep one connection-wide wire ordinal/FIFO and the existing typed-fingerprint validation. This is a
  routing correction, not a second transcript path.
- An unexpected notification without a valid session ID can still fail visibly. A valid update for a
  different session must not.

Do not solve this with `sleep(0)`, a quiet period, waiting after fork, or by moving the epoch later.
The incoming notification is allowed to arrive at any inter-request boundary, and moving the epoch
later could leak candidate traffic to public ingress.

### 2. Separate transaction exclusion from publication/update quiescence

Use a per-employee compaction/mutation reservation to serialize lifecycle transitions. Use the
existing publication/update gate only for:

1. short exact-handle validation and reservation publication;
2. final exact-handle validation, durable result publication, and N-before-N+1 quiescence; and
3. short abort/failure state publication.

Release the publication/update gate across fork, private load, required restore, and other child or
repository I/O. Ordinary generation-N ingress must be able to drain while the transaction reservation
prevents a conflicting transition. Reacquiring the gate for final publication preserves the frozen
rule that all admitted N sinks finish before N+1 becomes visible.

Do not bypass the gate only inside the callback or add task-reentrant locking. The callback runs in a
different task, and such a special case would weaken the generation ordering invariant.

The existing five-minute breaker should remain as emergency diagnosis and settlement. It did not
cause either bug.

## Regression seam

One production-shaped deterministic test should compose the real `SdkAcpEmployeeChild`, real ordered
ingress, and real registry lock topology with a scripted agent that:

1. completes source load;
2. on `session/fork`, returns a candidate and emits candidate
   `available_commands_update` after the response is queued;
3. also permits an older source update to remain ahead of candidate replay;
4. replays the candidate during `session/load`; and
5. supports restoring the original session.

Assert, under a short test-only external timeout:

- no `AcpSessionUpdateCallbackMismatch` when the candidate notification precedes the outgoing load
  observer;
- candidate updates/replay go only to private capture; source updates go only to public ingress;
- no publication-gate/FIFO deadlock, and public N ingress completes while capture is prepared;
- prepare leaves the durable binding at N;
- commit performs the existing exact CAS and publishes N+1 only after admitted N sinks drain;
- an injected failure after private load restores N within the same deadline;
- abort/restore leaves no pending epochs, slots, lock owners, or tasks; and
- the existing late-N and exact-generation concurrency tests remain green.

A real-Hermes no-model focused check should retain the raw event-order assertion as integration
evidence, but the scripted test is the canonical red/green regression because it forces both races
without relying on scheduler luck.

## Disposition

This research does not authorize product changes by itself. It establishes that the next correction
belongs in Panels' ordered-ingress private routing and registry transaction/gate ownership. There is
no evidence for modifying Hermes, lowering or removing the five-minute compaction breaker, adding
backward-compatibility state, or adding a second/private prose reconstruction path.
