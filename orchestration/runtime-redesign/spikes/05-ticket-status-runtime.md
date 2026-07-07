# Spike 05 — Ticket status + runtime control (build plan, revised)

## Goal

Make ticket status mean only the ticket's durable runtime/parking state, and move collision control to the settled shared-child gateway topology.

The ticket status values are:

- `empty`: parked; nothing running, nothing pending; System A may pick it up.
- `agent_running_step`: an agent is autonomously running a ticket step now. This replaces `agent_working` everywhere.
- `awaiting_approval`: a proposal is parked, waiting for the human.
- `user_takeover`: the human has taken the ticket; System A leaves it alone.
- `errored`: a run failed and stopped; no auto-retry.

The collision guard is not ticket status, not a DB field, and not an app-level registry. The planner runs one persistent shared Hermes gateway child with the worker role and planner home. Every ticket has one durable Hermes session inside that child. Every System B step and every chat send/command goes through that one child.

Because all turns for a ticket route to the same in-process gateway session, Hermes's own per-session `4009 session busy` guard is the collision guard. It serializes step and chat sends for one ticket session. Different ticket sessions run concurrently in the same child. There is no app `agent_running` flag, no send registry, no MindQueue, and no queue/buffer of deferred sends.

When the gateway returns `4009`, the planner treats it as "an agent is already running on this ticket":

- Ticket chat returns HTTP 409 with `already_running`.
- A concurrent System B poll step skips cleanly.

## Naming Decision

Rename `tickets.status` to `tickets.ticket_status`.

Reason: `status` is ambiguous in this repo because sprint items also have `status`, tickets also have `state`, and this redesign depends on the distinction that ticket status is not a collision lock. The clearer name earns the wire churn.

Impact:

- `Ticket.status` becomes `Ticket.ticket_status`.
- JSON responses expose `ticket_status` instead of `status` for tickets and board cards.
- Frontend reads `detail.ticket_status` and `card.ticket_status`.
- `ticket_status_changed` event payload uses `{"ticket_status": "..."}`.
- Do not rename `chat_session_key` in this spike.

Fresh-build schema only; no migration.

## Settled Runtime Topology

The production server owns one shared gateway child as an `app.state` singleton.

Lifecycle:

- Spawn it during server startup, or lazily on first use if startup chooses degraded/lazy behavior.
- Keep it warm for the server's life.
- Configure it with:
  - `HERMES_HOME = resolve_planner_home()`
  - `HERMES_TUI_SKILLS = config.worker_skill`
  - `HERMES_PYTHON_SRC_ROOT` from the resolved Hermes interpreter
- Inject the same singleton into System B and the chat path.
- Respawn it if the child dies. Durable sessions persist in Hermes `state.db`, so the new child resumes ticket sessions by stored key.
- Shut it down on server stop, after System A/System B have stopped accepting new set-offs.

The singleton may have a lifecycle lock around spawn/respawn and child-pointer replacement. That lock is not a session collision guard and must not track busy ticket/session state.

One durable session per ticket is required. Before any prompt or command for a ticket, the shared gateway path must resolve or create the ticket's durable `chat_session_key` and persist any newly minted or resumed tip key. This is session identity initialization, not a running flag. Do not add fallback busy keys like `new-ticket-session:<ticket_id>`.

The global/main-agent chat as a separate child is explicitly out of scope and deferred. This spike only wires the shared worker gateway used by ticket steps and the existing chat path.

## Invariants

- Ticket status is stored in SQLite and changed only by transition-specific functions in `tickets/data.py`.
- The shared Hermes gateway child is the only production child used for ticket steps and ticket chat sends/commands.
- Hermes `4009 session busy` is the only collision guard.
- There is no app-level send registry, `agent_running` flag, MindQueue, queue, or buffer.
- Chat never changes ticket status.
- Proposal parking is written at the proposal source, not inferred at run end.
- Run completion is dumb and idempotent: if the ticket is still `agent_running_step`, set `empty`; otherwise do nothing.
- Run error sets `errored`.
- Accepting a proposal sets `empty`.
- Takeover and release are explicit human actions.
- System A composes the next-step prompt.
- No no-progress guard.

## File-by-File Plan

### `src/planner/core/db.py`

- Bump `SCHEMA_VERSION` from `3` to `4`.
- In `tickets`, rename `status` to `ticket_status`.
- Replace the CHECK with:
  - `empty`
  - `agent_running_step`
  - `awaiting_approval`
  - `user_takeover`
  - `errored`
- Remove the `worker` column.
- Update comments so ticket status is described as durable ticket state-of-control, not a lock or System B-owned worker field.

### `src/planner/tickets/contracts.py`

- Rename `TicketStatus.agent_working` to `agent_running_step`.
- Add `TicketStatus.user_takeover`.
- Rename `Ticket.status` to `Ticket.ticket_status`.
- Remove `Ticket.worker`.
- Update comments to say ticket status is written by canonical data-layer transition functions.

No takeover request body is needed.

### `src/planner/core/contracts.py`

- Change `ticket_status_changed` payload comment to `{"ticket_status": ...}` plus optional `error`.
- Add `ErrorCode.already_running = "already_running"`.

Reason: this is the stable API error for Hermes `4009 session busy`.

### `src/planner/core/server.py`

- Map `ErrorCode.already_running` to HTTP 409.
- Own the shared gateway singleton on `app.state`.
- Initialize the shared gateway before wiring production System A/System B, or provide a lazy singleton that both paths share.
- Expose the same singleton to:
  - System B for ticket steps.
  - Chat routes/services for sends and commands.
- On lifespan shutdown:
  - Stop background loops/System A first.
  - Shut down the shared gateway child after no new set-offs are accepted.

### `src/planner/core/loops.py`

- Accept or resolve the shared gateway singleton and pass it into `SystemB`.
- Do not construct per-step gateway children here.
- Keep System A's idle poke wiring, but no queue/has-inflight integration remains.
- Ensure shutdown order stops System A before the shared gateway is closed.

### `src/planner/tickets/data.py`

Centralize status writes here.

Keep `_txn`. Add small private helpers only if they remove duplication:

- `_write_ticket_status(conn, ticket_id, ticket_status, now, error=None)`.
- `_persist_ticket_chat_session_key(conn, ticket_id, session_key, now)`.

Remove `set_run_status` as a general "set any status" door. Replace it with transition-specific functions:

- `start_run_if_runnable`
- `finish_run_if_still_running_step`
- `mark_run_errored`
- `take_over_ticket`
- `release_ticket`

Required transitions:

- `create_ticket` inserts `ticket_status = empty`.
- `start_run_if_runnable`:
  - Uses the existing `BEGIN IMMEDIATE`.
  - Re-reads the ticket inside the transaction.
  - Returns `None` unless `ticket.ticket_status == empty`.
  - Then applies the injected readiness guard.
  - If both pass, writes `agent_running_step` and appends `ticket_status_changed`.
- `file_proposal`:
  - Runs resolution as today.
  - Applies the decision in the same transaction.
  - If the proposal path parked a proposal, writes `awaiting_approval` immediately.
  - Do not add a new `Decision` field just for this; the parked branch already stores a proposal and emits `proposal_filed`.
- `finish_run_if_still_running_step`:
  - Persists the returned `chat_session_key` if supplied.
  - If current `ticket_status == agent_running_step`, writes `empty`.
  - Otherwise leaves status unchanged.
- `mark_run_errored`:
  - Persists the returned `chat_session_key` if supplied.
  - Writes `errored` with the error in the status event payload.
- `accept_proposal`:
  - Applies the resolution decision.
  - Writes `empty` in the same transaction.
- `take_over_ticket`:
  - Human-only at API layer.
  - Writes `user_takeover`.
- `release_ticket`:
  - Human-only at API layer.
  - Writes `empty`.

Do not add no-progress event scanning.

### `src/planner/tickets/logic/resolution.py`

- Rename comments that imply run completion decides approval status.
- Keep resolution pure: it decides fields/state/scope/events, not ticket status.

### `src/planner/tickets/logic/machine.py`

- No behavioral change expected.
- Update comments only if they mention the old status semantics.

### `src/planner/runtime/readiness.py`

- Keep `is_runnable` focused on ticket state, fields, scope, and blockers.
- Do not add queue, registry, or gateway-busy checks here.
- Update module comments to remove `has_inflight` and queue language.

### `src/planner/minds/gateway.py`

Required shared-child change: event demux.

`GatewayChild` currently routes JSON-RPC responses by request id, but puts all non-ready events on one shared queue. That is unsafe with a shared child running concurrent turns, because one drain loop can steal another session's `message.complete`.

Change the event side to mirror the response side:

- Keep response demux by request id.
- Route events by `session_id`.
- Provide a turn drain API that reads events only for the requested live session id.
- Register the session event drain before submitting a prompt so early events are not lost.
- Child death wakes all pending request waiters and all session event drainers.
- `gateway.ready` remains process-level.
- Unknown/process events without `session_id` must not be delivered to an arbitrary turn drain. Log, retain separately, or ignore intentionally.
- Clean up per-session event drain state after the turn completes.

This is mandatory for the shared-child topology.

### New shared gateway runtime module

Add a small shared-child owner, for example `src/planner/minds/shared_gateway.py`.

It owns:

- The one `GatewayChild`.
- Startup/lazy spawn.
- Respawn on child death.
- Shutdown.
- Process env construction for planner home + worker role.
- Session create/resume helpers.
- High-level operations for:
  - ticket step turn
  - chat send
  - chat command
  - command catalog/status, if kept on this path

It must not own:

- Ticket status transitions.
- A send registry.
- A per-session busy map.
- A queue.
- A pending-send buffer.

`GatewayRpcError(code=4009)` becomes a typed busy result or `PlannerError(ErrorCode.already_running, "an agent is already running on this ticket", ...)` depending on caller. Other gateway failures remain `gateway_offline` or step errors as appropriate.

Session identity rules:

- Resolve the stored `chat_session_key`.
- Resume when present.
- Create when absent.
- Persist a newly created key or resumed tip key through the data layer/service helper.
- Do not use synthetic fallback busy keys.
- Do not create more than one durable session for one ticket.

### `src/planner/minds/runner.py`

- Stop using child-per-send for production ticket steps.
- Either retire `run_step` from System B or rewrite it as a thin helper over the shared gateway runtime.
- No status writes.
- No queue.
- No per-run `GatewayChild(...); finally child.shutdown()` for ticket steps.

### `src/planner/runtime/system_b.py`

- Delete `MindQueue`, `_Item`, `_queue`, and `has_inflight`.
- `set_off` starts one daemon thread for the requested step.
- Keep `wait_idle` only as a test helper if needed; implement it with a simple active-thread counter/condition, not a queue or guard.
- Step thread flow:
  1. Resolve or create the ticket's durable gateway session through the shared gateway path.
  2. Call `tickets_data.start_run_if_runnable`.
  3. If it returns `None`, stop.
  4. Submit the step prompt through the shared gateway child.
  5. If the gateway returns `4009`, treat it as a skip; if this thread already wrote `agent_running_step`, call `finish_run_if_still_running_step` to clear it.
  6. On `complete`, call `finish_run_if_still_running_step`.
  7. On `interrupted`, `errored`, child death, or exception, call `mark_run_errored`.
  8. Fire the idle callback after the thread settles so System A can poll again.

Remove the proposal-present/no-progress check at run end.

### `src/planner/runtime/system_a.py`

- Candidate SQL becomes today's tickets where:
  - `ticket_status = 'empty'`
  - `state NOT IN ('done','dropped')`
- For each candidate:
  - Load the ticket.
  - Check `readiness.is_runnable`.
  - Call `SystemB.set_off`.
- Do not consult `has_inflight`.
- Do not consult a queue.
- Do not consult a send registry.
- Keep `_next_step_prompt`; System A owns next-step prompt composition.
- Update docs/comments to describe the shared gateway `4009` guard, not System B queue state.

### `src/planner/minds/queue.py`

- Delete the file.

### `src/planner/minds/__init__.py`

- Remove `MindQueue` import/export.
- Update package comments: minds now use a shared warm gateway child, not child-per-run plus queue.

### `src/planner/chat/service.py`

- Chat sends and chat commands use the shared gateway singleton.
- Ticket chat:
  - Resolve `chat_session_key`.
  - Use the shared gateway to resume/create the ticket session.
  - Call shared-gateway send or command.
  - If the gateway returns `4009`, raise `PlannerError(ErrorCode.already_running, "an agent is already running on this ticket", ...)`.
  - Persist returned session key as today.
  - Do not read or write ticket status.
- Day chat follows the same shared-child path for the existing day chat session behavior.
- Preserve existing first-key persistence behavior in `_persist_key`.

### `src/planner/chat/api.py`

- No route shape change.
- Existing human-only checks stay.
- Use the shared gateway from `app.state`, directly or through the adapter object that wraps it.
- 409 is produced through `PlannerError(ErrorCode.already_running, ...)`.
- Command catalog caching may stay, but it must not force a separate prompt child for ticket sends.

### `src/planner/core/adapters/base.py`

- Update comments/protocols as needed so production gateway calls are shared-child calls, not child-per-send calls.
- Keep fakes easy to inject in tests.
- Do not put ticket status logic in adapters.

### `src/planner/core/adapters/real.py`

- Remove per-send `GatewayChild` construction from chat send and command execution.
- Make real chat gateway behavior delegate to the shared gateway runtime, or move the production implementation out of this adapter.
- Ensure there is exactly one production owner of the `GatewayChild`.
- Map Hermes `4009` to `already_running`; leave other transport failures as `gateway_offline`.
- Do not put ticket status logic here.

### `src/planner/core/adapters/fakes.py`

- Add fake behavior needed to test shared-child busy collisions:
  - blocking calls
  - a forced `4009`/busy response
  - command and send sharing the same fake session behavior
- Do not add status behavior to gateway fakes.

### `src/planner/tickets/api.py`

Add human-only routes:

- `POST /tickets/{ticket_id}/takeover`
  - `reject_agents(ctx)`
  - calls `tickets_data.take_over_ticket`
  - does not poke System A
- `POST /tickets/{ticket_id}/release`
  - `reject_agents(ctx)`
  - calls `tickets_data.release_ticket`
  - pokes System A

Existing `accept_field` poke stays; after accept writes `empty`, it is the continuation path.

### `src/planner/tickets/views.py`

- Emit `ticket_status`, not `status`, in `ticket_json`.
- Remove `worker` from ticket JSON.
- Board cards emit `ticket_status`.
- Update comments to remove status/worker lock language.

### `assets/components.js`

- Rename marker support from old working status to the new value:
  - `agent-running-step`
  - `user-takeover`
- Update `eventSummary("ticket_status_changed", payload)`:
  - Read `payload.ticket_status`.
  - Show error if present.
  - Remove worker display entirely.
- Leave chat thinking dots as local chat UI state only.

### `assets/screens-ticket.js`

- Read `detail.ticket_status`.
- Replace `agent_working` checks with `agent_running_step`.
- Header markers:
  - `agent_running_step` -> running-step marker
  - `errored` -> errored marker
  - `user_takeover` -> takeover marker
- Add a small control in the header:
  - If `ticket_status !== "user_takeover"`, show "Take over".
  - If `ticket_status === "user_takeover"`, show "Release".
  - Use existing error handling.
- Pass only availability to chat panel unless needed for display; chat status remains local to chat pending state.

### `assets/screens-board.js`

- Read `card.ticket_status`.
- Replace old `agent_working` marker check with `agent_running_step`.
- Add `user_takeover` marker if useful on board cards.

### `assets/app.css`

- Rename/add chip classes for:
  - `agent-running-step`
  - `user-takeover`
- Remove obsolete `agent-working` styling after frontend references are gone.

## Tests

Update tests to match the settled model.

### Data-layer tests

Add or update tests for:

- New `TicketStatus` values.
- Fresh ticket has `ticket_status == empty`.
- `start_run_if_runnable` starts only from `empty`.
- Guard is not called when status is not `empty`.
- Parked proposal writes `awaiting_approval`.
- Auto-accepted proposal does not write `awaiting_approval`.
- `finish_run_if_still_running_step` writes `empty` only from `agent_running_step`.
- `finish_run_if_still_running_step` leaves `awaiting_approval` unchanged.
- `mark_run_errored` writes `errored`.
- `accept_proposal` writes `empty`.
- `take_over_ticket` writes `user_takeover`.
- `release_ticket` writes `empty`.
- `ticket_status_changed` payload has no `worker`.

### Gateway/shared-child tests

- `GatewayChild` responses still demux by request id.
- `GatewayChild` events demux by `session_id`.
- Concurrent drains for two sessions cannot receive each other's `message.complete`.
- Child death wakes all session drainers.
- Shared gateway startup creates one child and reuses it.
- Shared gateway respawns after child death.
- Shared gateway shutdown closes the child.
- System B and chat use the same shared child instance in production wiring.
- Session create/resume persists one durable session key per ticket.

### System B tests

- Remove MindQueue serialization tests.
- Verify `set_off` starts daemon step work without a queue.
- Verify two concurrent step set-offs do not both run the same ticket step.
- Verify gateway `4009` during a step is treated as a skip, not an errored run.
- Verify a step parks a proposal and completion does not overwrite `awaiting_approval`.
- Verify auto-accepted step completion leaves `empty`.
- Verify run error writes `errored`.
- Verify no no-progress scan exists in System B.

### System A tests

- Candidate SQL excludes `awaiting_approval`, `agent_running_step`, `user_takeover`, and `errored`.
- Poll sets off only `empty`, runnable, non-terminal tickets.
- Poll does not call `has_inflight`.
- Poll does not consult a queue or send registry.
- Accept writes `empty`, then poke/poll can continue the ticket.
- No no-progress guard tests.

### Chat tests

- Ticket chat does not change ticket status from `empty`, `awaiting_approval`, or `user_takeover`.
- Ticket chat refuses with HTTP 409 / `already_running` when the gateway returns `4009`.
- `/command` and `/send` share the same shared gateway/session path.
- Day chat uses the shared child path for existing day chat behavior.
- Session key persistence behavior stays unchanged.

### Frontend and e2e

- Ticket header shows `agent_running_step`, `user_takeover`, and `errored` correctly.
- Take over posts takeover and shows `user_takeover`.
- Release posts release and returns to `empty`.
- Chat thinking dots appear for chat sends without changing ticket status.
- Board cards render the renamed status marker.

## Gated Commit Sequence

Each commit must pass `./verify`.

1. **Schema and contracts**
   - Rename `status` to `ticket_status`.
   - Rename `agent_working` to `agent_running_step`.
   - Add `user_takeover`.
   - Remove `worker`.
   - Add `already_running`.
   - Bump schema version.
   - Update serializers and tests that compile against contracts.

2. **Ticket status transitions**
   - Replace generic status setter with transition-specific functions.
   - Implement proposal parking, completion, error, accept, takeover, and release transitions.
   - Make completion dumb and idempotent.
   - Update data-layer tests.

3. **Gateway event demux**
   - Replace the single shared event queue with per-session event routing.
   - Keep request-id response demux intact.
   - Add concurrent-session event tests.

4. **Shared gateway lifecycle**
   - Add the shared gateway owner.
   - Wire startup/lazy spawn, respawn, status, and shutdown.
   - Configure planner home + worker role.
   - Ensure session create/resume/key persistence helpers exist.
   - Add lifecycle tests.

5. **System B without queue**
   - Delete `MindQueue` usage and `has_inflight`.
   - Convert set-off to daemon step threads.
   - Route steps through the shared gateway.
   - Treat gateway `4009` as a skip.
   - Remove no-progress checks.
   - Rewrite System B tests.

6. **System A poll**
   - Query only `ticket_status = empty`.
   - Remove `has_inflight`.
   - Remove queue/registry comments.
   - Update System A tests.

7. **Chat send path**
   - Route `/send` and `/command` through the shared gateway.
   - Return `already_running` on Hermes `4009`.
   - Keep chat from touching ticket status.
   - Update chat tests.

8. **Takeover API and frontend**
   - Add takeover/release routes.
   - Add ticket header controls and markers.
   - Rename frontend status reads to `ticket_status`.
   - Update e2e coverage.

9. **Cleanup**
   - Delete `src/planner/minds/queue.py`.
   - Remove `MindQueue`, `has_inflight`, `agent_working`, `agent-working`, send-registry, `agent_running` flag, and worker-column/event references.
   - Run targeted `rg` checks:
     - `rg "agent_working|agent-working|MindQueue|has_inflight|send registry|send_registry|session_sends|new-ticket-session|worker|\\.status|\\[\"status\"\\]"`
   - Reconcile remaining hits intentionally, especially legitimate `worker_skill` references.
   - Final `./verify`.

## Non-Goals

- No live migration.
- No `chat_session_key` rename.
- No DB busy column.
- No app-level send registry.
- No `agent_running` flag.
- No MindQueue.
- No in-memory queue or buffer.
- No chat-driven ticket status changes.
- No separate global/main-agent chat child in this spike.
