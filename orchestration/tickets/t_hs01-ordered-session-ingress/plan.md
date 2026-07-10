# t_hs01 implementation plan — ordered live-session ingress

## Purpose of this slice

Add the new transport seam beside the existing per-call drains, prove its ordering/lifecycle contract,
and compose it into each existing `SharedGateway`. Do not migrate human chat or employee settlement
yet. This is the expand half of an expand–migrate–contract refactor.

## Contract skeleton first

Extend `src/planner/minds/contracts.py` with only the transport facts later callers need:

- `SubmitDisposition = Literal["streaming", "queued", "steered"]`.
- An immutable submission receipt containing the native disposition.
- An immutable ordered Hermes observation containing the live session ID, event type, and unmodified
  payload.
- An explicit transport-lost/unknown outcome for a request whose write or acceptance cannot be
  disproved after child death/timeout.

Do not add Panels turn IDs, new Hermes fields, product statuses, database shapes, or queue abstractions.

## RED acceptance seams

Create focused coverage in `tests/unit/test_minds_sessions.py` and extend low-level cases in
`tests/unit/test_minds.py`:

1. For both create and resume, events emitted before the RPC response frame and events emitted after
   that frame but before caller binding remain available after the live ID is bound; neither side of
   the response boundary depends on a product caller already being attached.
2. One physical child event ingress receives every session event once in stdout order and demultiplexes
   two live sessions without cross-delivery.
3. A submission attempt is registered before `prompt.submit` is written. Start/delta/complete events
   arriving before its RPC response remain ordered until the native disposition is known.
4. Exact `streaming`, `queued`, and `steered` results survive normalization.
5. Concurrent submit/interrupt calls for one session are written to Hermes in lock order, while calls
   for two sessions can proceed independently. Withhold the first RPC response and prove the second
   same-session command is already written: the session lock covers registration/write only, never
   the response wait.
6. Interrupt acknowledgement is recorded without manufacturing a terminal completion or idle state.
7. Known queued/pending work prevents dormancy across a transient `session.info(running=false)`.
8. Once Hermes is observed idle and no local consequence is pending, the lightweight session state
   can detach; reopening the stored key binds the resumed live ID and snapshot without losing events.
9. A timed-out submit becomes attempt-unknown while its live child/session ingress continues accepting
   ordered observations. Child death separately marks the child offline, wakes the ingress, closes
   live session state, and sends no retry. Gateway shutdown leaves no router/session thread alive.
10. Existing temporary-drain tests and existing callers remain green during this expand slice.

## Implementation shape

### 1. Make low-level ingress gapless

Update `src/planner/minds/gateway.py` so `GatewayChild` owns one child-wide ordered session-event feed
from construction, before its stdout reader starts. `_route_event` always appends session events to
that feed; it no longer relies on a caller already knowing the live session ID for the event to exist.

Keep the current per-session broadcast drains as temporary compatibility for `t_hs02`/`t_hs03`.
During the expand phase an event may be observed by both the new unused session manager and an old
caller, but each mechanism receives it once. `t_hs04` removes product access to the old mechanism.

Child death places one terminal sentinel on the child-wide feed as well as waking legacy request/drain
waiters. Expose exactly one claim operation for the child-wide feed so a second router cannot be
created accidentally.

Split the internal JSON-RPC request operation into a begin/write seam and a response-wait handle.
Beginning a request still allocates and registers its request ID before writing, then returns a handle
whose wait remains correlated by that ID. Keep `GatewayChild.request()` as the compatibility wrapper
that begins and then waits. The session manager uses the split seam so it can release its per-session
command lock immediately after the ordered write, before waiting for the RPC response.

### 2. Add the deep live-session module

Add `src/planner/minds/sessions/__init__.py` and
`src/planner/minds/sessions/service.py`.

One manager is owned by one `SharedGateway`/gateway child. It starts one router that consumes the
child-wide feed and holds lightweight session records keyed by live Hermes session ID. This physical
shape is gapless even before create/resume reveals the ID; it still provides one logical ordered lane
per live session.

The manager hides:

- unbound event buffering by live ID until create/resume binds stored and live identities;
- stored-key/live-ID rebinding on resume;
- ordered observation delivery for each session;
- submission attempts registered before the RPC write;
- buffering while the submit disposition is not yet known;
- a per-session command lock that only linearizes outbound writes;
- observed running/pending facts, dormancy, transport loss, and shutdown.

Use a condition/event per unsettled attempt, not polling. Do not add a timer, worker queue, idle waiter,
automatic promotion, or retry loop. The manager records what Hermes says; it does not decide when
Hermes may run.

### 3. Define lifecycle without becoming a scheduler

The session record has only observational phases:

- **Unbound:** an event arrived for a live ID before create/resume binding; retain it in order.
- **Bound:** stored/live identity and latest resume snapshot are known.
- **Observed active:** Hermes observations or a native submit receipt show running/pending work.
- **Dormant:** Hermes has been observed idle and no registered Panels consequence remains; discard the
  lightweight record while retaining the stored session identity used for later resume.
- **Offline:** the child died or the gateway is shutting down; close its live ingress, wake waiters,
  and never replay a prompt automatically.

Unknown is an attempt outcome, not a session phase. A timed-out `prompt.submit` remains registered as
unknown while the live child/session ingress continues receiving ordered events. Those events remain
faithful observations but are not guessed into a terminal for that attempt.

`session.info` updates observation state but is never permission to send. A known `queued` receipt
outlives a transient idle observation until later ordered lifecycle evidence resolves it. Duplicate
Hermes starts are retained as observations and do not create extra local executions in this slice.

### 4. Compose without migrating callers

Update `src/planner/minds/shared_gateway.py` so each `SharedGateway` starts one live-session manager for
its child, binds create/resume results into it, and shuts it down before the child. Child replacement
discards old live bindings and creates a fresh manager; stored Hermes keys remain the resume authority.

The employee-configured and Chief-configured `SharedGateway` instances remain separate exactly as
wired in `src/planner/core/server.py`. That file is not changed in this ticket; `SharedGateway` must
preserve its existing constructor and shutdown surface for server composition.

Leave `_submit_and_drain`, `_stream_prompt`, and their callers intact for compatibility. They are
migrated only in `t_hs02` and `t_hs03`, then removed in `t_hs04`.

## Concurrency invariants to inspect directly

- Gateway response correlation remains by JSON-RPC request ID.
- The child-wide event feed has exactly one consumer.
- Session observation order matches the stdout reader's receive order.
- The registry lock protects identity/state maps but is never held during a blocking RPC or callback.
- A session command lock covers attempt registration and the low-level begin/write operation only;
  it is released before waiting on the request-ID-correlated response handle.
- Different sessions do not share a command lock.
- Shutdown order is: reject new session work, wake/settle unknown attempts, stop/join router, then stop
  the gateway child.

## Implementation order

1. Add the contract skeleton and RED tests for gapless ingress, native dispositions, ordering, and
   child death.
2. Add the single child-wide event feed and split begin/write plus response-wait request seam in
   `gateway.py`; preserve `request()` as a compatibility wrapper and make the low-level tests green.
3. Add the live-session manager and make its lifecycle/concurrency tests green.
4. Compose the manager into `SharedGateway` create/resume, child replacement, and shutdown while
   retaining old caller behavior.
5. Run the focused gateway/session suite, then Ruff, Mypy, and `git diff --check` for the touched slice.
   After the hs01 changes land in the feature worktree, run the authoritative full `./verify` once for
   this ticket. `t_hs04` will run it again only after later tickets have materially changed the tree.

## Files permitted for this ticket

- `src/planner/minds/contracts.py`
- `src/planner/minds/gateway.py`
- `src/planner/minds/shared_gateway.py`
- `src/planner/minds/sessions/__init__.py`
- `src/planner/minds/sessions/service.py`
- `tests/unit/test_minds.py`
- `tests/unit/test_minds_sessions.py`
- This ticket's plan/review/report files only.

No chat, runtime, database, frontend, Hermes, or unrelated documentation file may change.
