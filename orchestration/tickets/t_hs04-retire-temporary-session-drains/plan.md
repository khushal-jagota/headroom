# t_hs04 implementation plan — contract the temporary drain boundary

## Purpose of this slice

Finish the expand–migrate–contract refactor after hs02 and hs03 are green. Remove every compatibility
surface that still lets production code consume an unowned session event, prove lifecycle and role
isolation through public behavior, and update the live plain-language documentation. This ticket does
not change Hermes, the product UI, transcript shapes, or scheduling policy.

## Preconditions

Before changing code, confirm the integrated tree has both migrations:

- Chief, Ticket, and Day messages, model-backed commands, images, and Stop use the shared
  consequence-owned session ingress from hs02.
- `SharedGateway.run_ticket_step` uses that same consequence ownership from hs03.
- The hs02 Stop-A/send-B regression and hs03 queued-employee/internal-completion regression pass.

If either production path still calls a raw drain or waits on a session-wide `message.complete`, stop
and return it to its owning migration ticket. Do not preserve a second compatibility route in hs04.

## Final compatibility boundary

Production has one event path:

1. `GatewayChild` writes every session event once to its one child-wide ordered ingress.
2. The `LiveSessionManager` is the sole production claimant and routes observations into the
   consequence handles introduced by hs02/hs03.
3. Human chat and employee execution consume only the consequence they registered before their
   outbound write. They cannot read a session-wide completion queue.

Keep the low-level child-wide ingress primitive because the session manager and isolated protocol
smoke helpers need it. `planner.minds.runner.run_step` and `planner.minds.smoke` may claim that raw
feed only because each owns a standalone child and never composes a `LiveSessionManager` for the
same child. No other `src/planner` module may claim or expose it.

Keep JSON-RPC `request()`/`begin_request()`, native `streaming`/`queued`/`steered` receipts,
consequence-owned observation handles, ordered interrupt, stored/live session identity, dormancy,
and transport-unknown/offline outcomes. Keep the existing `GatewayAdapter`, `SharedGateway`, and
server constructor/shutdown surfaces.

## Explicit removal list

Delete, rather than deprecate, the expand-phase compatibility machinery:

- `GatewayChild.open_session_events`, `SessionEventStream`, `_close_session_events`, the
  `_session_events` broadcast registry, and all event/death fan-out into that registry;
- `SharedGateway._submit_and_drain` and `SharedGateway._stream_prompt`, plus dead imports and branches
  left after the hs02/hs03 migrations;
- the receipt-only `LiveSession.submit` and session-wide `LiveSession.next_observation` compatibility
  surfaces from hs01; product submission must use the consequence-owned operation;
- the generic `_SessionState.observations` queue and any coarse/test-only pending-consequence API that
  is unused after consequence ownership lands;
- low-level tests whose only contract was that several temporary drains could subscribe to the same
  session event, and product tests that exercise the removed helpers rather than current behavior.

Rewrite the standalone runner and smoke commands to claim the single child-wide ingress before
create/resume/submit and to reject an event for the wrong live session. They remain transport smoke
tools, not an alternative production gateway. Do not add another queue, listener, adapter, or wrapper
to preserve the old calls.

## RED tests first

### 1. Make the source boundary executable

Add an AST/source-contract test that fails before contraction and scans production Python under
`src/planner`:

- `open_session_events`, `_submit_and_drain`, `_stream_prompt`, and session-wide
  `next_observation` do not exist or appear at product call sites;
- `claim_session_event_ingress` is called only by `minds/sessions/service.py`,
  `minds/runner.py`, and `minds/smoke.py`;
- `shared_gateway.py`, `chat/`, and `runtime/employee_step_runner.py` contain no raw ingress/drain
  claim and no independent settlement from an arbitrary session-level completion.

Use syntax-aware call/attribute inspection rather than a comment-sensitive substring check. The test
is the lasting guard against reintroducing the wrapper race.

### 2. Preserve the one remaining raw transport seam

Replace the old multi-drain unit cases with focused `GatewayChild` tests proving:

- the single child-wide feed receives session events once in stdout order and excludes process-only
  events;
- it can be claimed once only and child death wakes that claimant;
- `run_step` and the isolated concurrency smoke still reject cross-session delivery and settle their
  expected standalone session without a per-session drain.

These tests cover transport only. They must not become product lifecycle tests.

### 3. Verify lifecycle and recovery through composed gateways

Add or consolidate public-behavior tests around the integrated `SharedGateway`:

- complete a consequence, observe Hermes idle, detach the lightweight live session, then send again;
  assert the durable stored key remains and Hermes receives `session.resume`, not a new conversation;
- shut down a gateway with an accepted, queued, or unknown consequence and assert all waiters settle
  exactly once as their existing honest outcome, the manager/router terminates, and no prompt is
  retried;
- kill the real fake-backed child while an accepted product consequence is pending and assert the
  public `SharedGateway` caller settles exactly once with its existing unknown/offline outcome, the
  session manager/router terminates without a leaked listener or waiter, no prompt is retried, and a
  separately configured sibling role gateway continues serving its own session;
- construct a new gateway after shutdown with the same stored key and assert resume/history/running
  snapshot re-establishes the conversation without replaying prior product input;
- kill one role gateway child and prove the separately configured other role gateway still serves its
  sessions; shutting down Chief must not shut down employees, and vice versa;
- run two employee sessions through one employee role child and prove their consequence observations
  and settlement remain isolated.

Prefer extending existing hs01 lifecycle, hs02 human race, hs03 employee ownership, role-wiring, and
lifespan-shutdown tests over creating duplicate fixtures. Assertions may inspect deterministic router
termination in the session-focused test, but product correctness must be asserted at the public
gateway/API/result boundary.

### 4. Run both reported regressions together

Run the hs02 Chief/Ticket/Day Stop-A/send-B test and the hs03 old-completion/queued-employee test in
one focused gate after contraction. The old interrupted completion must belong only to A; the
employee's prior/internal completion must not settle its registered step. Existing message,
command, image, activity, worker-context, direct-revision, and employee-settlement suites remain
unchanged and green.

## Documentation update

Update the live docs in plain language, without presenting implementation history:

- `docs/systems.md`: production owns two independently configured role gateways—employee and Chief.
  Each child has one listener, which separates observations by live Hermes session and gives each
  accepted Panels operation its own consequence.
- `docs/chat.md`: Stop changes the visible Panels turn immediately but does not guess that Hermes has
  finished unwinding. A following send goes straight to Hermes and follows Hermes's native result;
  delayed events cannot complete the wrong visible turn. Keep all existing transcript, activity,
  image, and system-message presentation descriptions.
- `docs/employee-runtime.md`: every Ticket employee has a durable Hermes session inside the shared
  employee-role child. A lightweight Panels listener may detach when Hermes is observed idle and no
  consequence remains; reopening resumes the stored session. Unknown delivery is recorded honestly
  and never retried automatically.

Correct the current singular “one shared gateway owner” wording and any stale recovery text that says
Panels silently replaces a lost durable conversation. Distinguish the persistent Hermes session ID
from the detachable in-process listener. Do not add UI instructions or redesign language.

## Implementation order

1. Confirm hs02/hs03 preconditions and add the failing source-boundary contract test.
2. Remove the temporary per-session drain implementation and migrate only the standalone runner and
   smoke tooling to the one child-wide feed; make low-level transport tests green.
3. Remove the obsolete `LiveSession` and `SharedGateway` compatibility surfaces; make the static
   boundary and focused hs02/hs03 regressions green.
4. Add/consolidate lifecycle, restart, role-isolation, and thread/waiter shutdown coverage.
5. Update the three live docs and remove dead imports/comments/tests. Run focused minds, chat,
   employee-runner, worker-context, revision, role-wiring, and lifecycle tests; then Ruff, Mypy, and
   `git diff --check`.
6. Run the authoritative `PYTHONPATH="$PWD/src" ./verify` once on the fully integrated feature branch
   and record its complete output in this ticket's implementation report. Do not rerun merely to
   quote it.
7. After the integrated implementation review reports no violations, stage the complete hs01–hs04
   feature and create exactly one squashed commit on `codex/hermes-session-ingress`. The commit must
   contain the ordered-ingress implementation, both product migrations, contraction, tests, docs,
   ticket records, and current memory updates so the whole feature can be reverted atomically. Do not
   include unrelated worktree changes or create intermediate feature commits.

## Permitted files

- `src/planner/minds/gateway.py`
- `src/planner/minds/runner.py`
- `src/planner/minds/smoke.py`
- `src/planner/minds/contracts.py` only to remove a now-dead compatibility type
- `src/planner/minds/sessions/__init__.py`
- `src/planner/minds/sessions/service.py`
- `src/planner/minds/shared_gateway.py`
- `tests/unit/test_minds.py`
- `tests/unit/test_minds_sessions.py`
- `tests/unit/test_core_loops.py` and `tests/unit/test_request_identity.py` only for composed
  shutdown/role-isolation coverage
- existing hs02/hs03 unit/API/E2E test files only to consolidate the named regressions without
  changing their presentation contract
- `docs/systems.md`
- `docs/chat.md`
- `docs/employee-runtime.md`
- this ticket's plan/review/report files

Do not change `src/planner/core/server.py`, chat/database contracts, schema or migrations, Ticket
readiness/claim writers, frontend source, CSS, assets, Hermes source/configuration, or any stored
transcript. No new identity, durable replay, retry scheduler, local message queue, or UI state belongs
in this ticket.
