# ACP-04 implementation report

## Outcome

ACP-04 now cuts Chief and Ticket conversation traffic over to one production ACP composition.

- SQLite owns one durable employee/session binding with atomic Ticket and Chief mirrors.
- One source-aware registry, hub, turn broker, permission broker, and synchronous step gateway own
  both browser and automatic Employee-step demand.
- Production starts no legacy shared gateway, entity router, pool, relay, or transcript tee for this
  path. `/api/conversation` is the active typed WebSocket.
- Board, Ticket, and Chief routes always mount the restrained non-visual ACP wrapper. No shared
  tokens, CSS, route hierarchy, or generated frontend assets were changed for the cutover.
- Shutdown closes admission, drains the runner, and stops conversation owners under one deadline.
  Loop-start failure also closes the already-built composition.

## Implementation-review disposition

The single implementation review raised four findings. The bounded correction check resolved the
three production defects and most Phase 8 coverage, then retained one proof-only finding for three
exact vertical cases. Those cases now pass in the official-child e2e file; no second broad review
was used.

1. Phase 8 now includes a real Playwright page served by Vite, proxied to a uvicorn subprocess,
   through FastAPI and the official ACP SDK child. The complete evidence also covers sequence-1
   first binding and mirrors, a fresh uvicorn-process durable restart, two-browser replacement,
   worker-first mid-turn attach, permission ownership and stale worker rejection, callback and exact
   interrupt behavior, rejection/capture collector ordering, slow consumer/reset overflow, and an
   explicit child audit proving DB rows alone are not delivery.
2. Captured notifications whose ACP session belongs to the retired binding are ignored when the
   same child source is rebound. The regression drives the exact capture-then-generation-2 flush
   path and proves the replacement child remains live.
3. Active replay now reserves one outbound queue slot for the global ready publication. Equality at
   capacity fails as replay unavailable without enqueueing a partial prefix.
4. A production background-loop startup exception closes admission and shuts down the already-built
   conversation composition before propagating.

## Phase 8 acceptance map

- Case 1: `test_browser_and_worker_share_one_real_sdk_session` asserts reset/ready sequences 1/2,
  durable binding, Ticket mirror, typed updates, and actual child prompt receipt.
- Case 2: `test_fresh_uvicorn_process_resumes_durable_binding` restarts a separate uvicorn process
  on the same database/session and raises reset above the supplied cursor.
- Case 3: the main vertical moves two live browsers to generation 2 with the atomic mirror, injects
  a stale session notification through the retired source, publishes replacement activity to both
  browsers, and proves the replacement session remains live.
- Case 4: `test_automatic_worker_starts_stream_before_midturn_browser_attach` starts the real runner
  with no browser, waits on a UDP child receipt latch, then proves complete private replay and one
  output on attach.
- Case 5: the main vertical rejects an unattached permission responder;
  `test_stale_worker_permission_cannot_settle` rejects a stale Ticket/session guard.
- Case 6: the automatic-runner vertical calls the exact synchronous gateway interrupt and proves
  runner settlement. The parameterized official-child failure vertical proves protocol rejection
  errors the tracked worker and rejects the queued successor before any second prompt, while capture
  failure removes the worker collector before the queued successor reaches the child.
- Case 7: the parameterized active-turn vertical attaches a real hub subscriber alongside a live
  WebSocket. It proves slow-subscriber eviction leaves the live browser healthy, and a one-byte
  reset limit rejects replay with an empty queue. Hub tests retain the exact ready-slot proof.
- Case 8: the main vertical inserts Chat/event rows before attach, observes no child prompt, and then
  checks the official child audit contains only the five real ACP prompts and never the DB-only text.

## Changed surfaces

- Production backend: `src/planner/conversation/**`, `src/planner/runtime/acp_step_gateway.py`,
  `src/planner/core/db.py`, `src/planner/core/server.py`, `src/planner/core/loops.py`, and the narrow
  Chief mirror writer in `src/planner/chat/data.py`.
- Production frontend: `web/src/lib/acp/**`, `web/src/components/AcpConversation.svelte`, and the
  existing Board, Ticket, and Chief route mount points.
- Focused proof: ACP binding/hub/WebSocket/gateway/composition/broker tests, production mount/browser
  tests, `tests/e2e/test_acp_conversation.py`, and the scripted SDK support/uvicorn entry point.

## Verification

The exact focused output is in `focused-checks.txt`.

- Ruff: pass.
- Mypy over 24 production source files: pass.
- Focused Python: **164 passed**.
- Five ACP web suites: pass.
- Svelte check: 0 errors and 0 warnings.
- Scoped diff check: pass.

The implementer did not run `./verify` or a production frontend build. The orchestrator owns the
authoritative integration run.
