# Spike 05 — Ticket status + runtime control (build plan)

## Goal

Redesign ticket run-status and runtime control so the live SQLite `tickets.status` row is the only collision guard. Remove `MindQueue`, move proposal parking status writes to the proposal source, make run completion dumb and idempotent, make accept release the ticket back to the poll, add `user_takeover`, and protect the shared ticket chat/session from step/chat collisions.

Current anchors:

- Status enum is four values at `src/planner/tickets/contracts.py:49`.
- DB status `CHECK` is four values at `src/planner/core/db.py:68`.
- `start_run_if_runnable` already owns an atomic `BEGIN IMMEDIATE` start path at `src/planner/tickets/data.py:226`, but it checks only the injected readiness guard, not live status.
- System A still queries `status IN ('empty','awaiting_approval')` at `src/planner/runtime/system_a.py:28` and calls `has_inflight` at `src/planner/runtime/system_a.py:100`.
- System B still uses `MindQueue` at `src/planner/runtime/system_b.py:41` / `src/planner/runtime/system_b.py:84`, exposes `has_inflight` at `src/planner/runtime/system_b.py:97`, and maps successful run completion to `awaiting_approval` at `src/planner/runtime/system_b.py:177`.
- Proposal parking currently happens in `resolution.decide_file_proposal` at `src/planner/tickets/logic/resolution.py:100`, but `tickets_data.file_proposal` only applies fields/state at `src/planner/tickets/data.py:259`.
- Accept currently applies fields/state only at `src/planner/tickets/data.py:268`.
- Chat currently resolves only `chat_session_key` at `src/planner/chat/service.py:42` and sends without ticket status guarding at `src/planner/chat/service.py:114`.
- Ticket UI already has `detail.status` in the header at `assets/screens-ticket.js:349` and passes only chat availability into `chatPanel` at `assets/screens-ticket.js:572`.
- `chatPanel` currently receives only `available` and always creates a composer when online at `assets/components.js:840`.

## Recommendations On Open Questions

1. **Takeover entry/exit: recommend manual takeover.**

Add explicit human-only `POST /api/tickets/{ticket_id}/takeover` and `POST /api/tickets/{ticket_id}/release` actions. `takeover` writes `user_takeover`; `release` writes `empty` and pokes System A.

Tradeoff: manual takeover is slightly more UI, but it keeps status semantics exact. Automatic takeover on chat/edit would make ordinary use unexpectedly park tickets, and it creates ambiguous recovery after a chat turn because chat itself is a transient agent run.

For this ticket, chat is not takeover. Chat is a run turn using the shared session; takeover is an explicit instruction to the poll: leave this ticket alone.

2. **No-progress guard: recommend poll-side “last step made no progress” detection, error on first confirmed no-progress step.**

Do not retry. A healthy step always either parks a proposal or advances state via auto-accept. Retrying a step that produced no field/state change risks an infinite loop and burns a model turn without adding evidence.

Implementation shape: System B marks step starts with status event metadata (`run_kind: "step"`, `state`, `gating_field`). On the next System A poll, before setting off an `empty` candidate, System A checks the latest completed step-start window. If no `proposal_filed`, `proposal_accepted`, or `state_changed` event occurred after that start, mark the ticket `errored` with a no-progress error and skip set-off. Chat starts use `run_kind: "chat"` and are ignored by this guard.

This keeps the run-end path dumb while preserving the old “no proposal is an error” safety for step turns.

3. **Writer discipline: restate the invariant.**

The old invariant “System B is the sole status writer” does not survive the design because proposal parking and accept are source-owned transitions. Replace it with:

- All status writes are centralized in `tickets/data.py`.
- Each status transition has one canonical public owner function.
- Runtime callers invoke transition-specific helpers, not arbitrary `UPDATE tickets SET status = ...`.

Canonical transition ownership:

- create -> `empty`: `tickets_data.create_ticket`.
- step/chat start -> `agent_working`: `tickets_data.start_run_if_runnable` for steps, `tickets_data.start_chat_turn` for ticket chat.
- proposal parking -> `awaiting_approval`: `tickets_data.file_proposal`, based on resolution’s parked decision.
- complete while still working -> `empty`: `tickets_data.finish_run_if_still_working`.
- run/chat error -> `errored`: `tickets_data.mark_run_errored`.
- accept proposal -> `empty`: `tickets_data.accept_proposal`.
- take over -> `user_takeover`: `tickets_data.take_over_ticket`.
- release -> `empty`: `tickets_data.release_ticket`.

4. **Schema: bump fresh-build schema to version 4.**

Add `user_takeover` to the `tickets.status` `CHECK` constraint and bump `SCHEMA_VERSION` from `3` to `4` in `src/planner/core/db.py:11`. No live-row migration is needed because this app rebuilds fresh DBs for schema changes.

## File-By-File Plan

### `src/planner/tickets/contracts.py`

Add `TicketStatus.user_takeover = "user_takeover"` beside the existing enum at `src/planner/tickets/contracts.py:49`.

Update comments on `TicketStatus`, `Ticket.status`, and `Ticket.worker` so they no longer say System B is the sole writer. Use the new invariant: status is code-owned, transition-specific data-layer helpers write it.

No request body is strictly required for takeover/release, but adding typed `TakeoverBody` is unnecessary unless the endpoint will accept metadata.

### `src/planner/core/db.py`

Change:

```sql
CHECK (status IN ('empty','agent_working','awaiting_approval','errored'))
```

to include `'user_takeover'`.

Bump:

```python
SCHEMA_VERSION: Final = 3
```

to `4`.

Update the column comment at `src/planner/core/db.py:68` to remove “System B is the writer”.

No migration script.

### `src/planner/tickets/logic/decisions.py`

Extend `Decision` with a boolean metadata field:

```python
parked_proposal: bool = False
```

This keeps resolution pure and lets `tickets_data.file_proposal` know whether the proposal path parked a proposal, without importing status concepts into resolution logic.

### `src/planner/tickets/logic/resolution.py`

In `decide_file_proposal`:

- Auto-accept branch at `src/planner/tickets/logic/resolution.py:107` returns `parked_proposal=False`.
- Park branch at `src/planner/tickets/logic/resolution.py:118` returns `Decision(..., parked_proposal=True)`.

This should apply to both gating and non-gating parked proposals. If the proposal is stored in a slot awaiting a human, status becomes `awaiting_approval`.

Do not make run-end infer parking by inspecting post-run fields.

### `src/planner/tickets/data.py`

Keep `_txn` as the transaction primitive.

Add private helpers:

- `_write_status(conn, ticket_id, *, status, worker, session_key=_UNSET, error=None, now, extra_payload=None) -> Ticket`
- `_append_status_event(...)` if useful to avoid duplicated event payload assembly.
- `_persist_session_key(conn, ticket_id, session_key, now)` for run completion cases where status was already changed by proposal parking or takeover and must not be clobbered.

Refactor `set_run_status` into either:

- a compatibility wrapper around `_write_status`, or
- a narrower public helper retained only for tests.

Do not leave it as an unconstrained “any caller may set any status” door in new code.

Update `start_run_if_runnable` at `src/planner/tickets/data.py:226`:

- Inside the existing `BEGIN IMMEDIATE`, immediately after `_load_ticket`, add:

```python
if ticket.status is not TicketStatus.empty:
    return None
```

- Keep this before the injected `guard`.
- Add optional `run_kind: Literal["step", "chat"] = "step"` or `worker` metadata so the status event includes `run_kind`, pre-run `state`, and `gating_field`.
- Keep the start write as `agent_working`, `worker=role`.

Add `finish_run_if_still_working`:

- Transactional.
- Load ticket.
- If a `session_key` is supplied, persist it regardless of current status.
- If `ticket.status is TicketStatus.agent_working`, write `empty`, `worker=NULL`, append `ticket_status_changed`.
- If status is anything else, do not change status or worker. This is the dumb idempotent message.complete behavior.

Add `mark_run_errored`:

- Transactional.
- Persist session key if supplied.
- Write `errored`, `worker=NULL`, append `ticket_status_changed` with `error`.
- This is used by System B, chat, and System A’s no-progress guard.

Update `file_proposal` at `src/planner/tickets/data.py:259`:

- Run resolution and `_apply_decision` as today.
- If `decision.parked_proposal` is true, write `awaiting_approval`, `worker=NULL`, in the same transaction after field/event writes.
- Return the reloaded ticket.
- This is the source-owned proposal parking transition.

Update `accept_proposal` at `src/planner/tickets/data.py:268`:

- After `_apply_decision`, write `empty`, `worker=NULL`, same transaction.
- Append a status event after proposal/state/scope events.
- Return reloaded ticket.
- This applies to gating and non-gating accepts.

Add `take_over_ticket` and `release_ticket`:

- `take_over_ticket`: human-only at API layer; data function loads ticket, rejects terminal only if desired by product, writes `user_takeover`, `worker=NULL`.
- `release_ticket`: writes `empty`, `worker=NULL`.
- Release should not auto-clear proposals; if a proposal remains parked, release should normally be unavailable or should be a no-op validation error. Recommended: reject release from `awaiting_approval`; release only from `user_takeover`.

Add `start_chat_turn`:

- Transactional check-and-set for ticket chat.
- Load ticket.
- If `status == agent_working`, raise `PlannerError(ErrorCode.run_in_progress, ...)`.
- To preserve exact status semantics, allow chat start only from `empty`. For `awaiting_approval`, `errored`, and `user_takeover`, return a structured conflict/validation telling the user what must happen first. This avoids overwriting a parked proposal or takeover with a dumb `empty` completion.
- Write `agent_working`, `worker="chat"`, status event with `run_kind: "chat"`.

### `src/planner/runtime/system_b.py`

Remove:

- `from planner.minds.queue import MindQueue`
- `_Item`
- `_queue`
- `has_inflight`
- queue-backed `wait_idle` semantics.

`set_off` should spawn a bare daemon thread immediately:

```python
threading.Thread(
    target=self._run_from_db,
    args=(ticket_id, role, prompt, guard),
    name=f"system-b-{ticket_id}",
    daemon=True,
).start()
```

Keep a lightweight thread registry/condition only if tests still need `wait_idle`; it must not participate in runtime guarding.

`_run` flow:

1. Read current session key at execution time.
2. Call `tickets_data.start_run_if_runnable(..., guard=guard, run_kind="step")`.
3. If it returns `None`, skip without spawning.
4. Call `run_step`.
5. If `result.status == "complete"`, call `tickets_data.finish_run_if_still_working(..., session_key=result.session_key)`.
6. If interrupted/errored, call `tickets_data.mark_run_errored(..., session_key=result.session_key, error=result.error or ...)`.
7. In `finally`, fire `_idle_cb(ticket_id)` after the run thread settles so System A gets its fast-path poke.

Remove the proposal-present check currently at `src/planner/runtime/system_b.py:177`. Proposal parking is no longer inferred at run end.

Update docstrings: System B is no longer the sole status writer and no longer serializes through `MindQueue`.

### `src/planner/runtime/system_a.py`

Update `_CANDIDATE_SQL` at `src/planner/runtime/system_a.py:28` to:

```sql
AND t.status = 'empty'
AND t.state NOT IN ('done','dropped')
```

Keep today’s `day_tickets` join.

Remove `not self._system_b.has_inflight(ticket_id)` from `poll_once`.

Add poll-side no-progress guard:

- Before adding a candidate to `ready`, call a helper like `_mark_no_progress_if_needed(conn, ticket) -> bool`.
- It should inspect the latest `ticket_status_changed` event with `status='agent_working'` and `run_kind='step'`.
- If that step later produced no `proposal_filed`, `proposal_accepted`, or `state_changed` event and the ticket is now `empty`, call `tickets_data.mark_run_errored(..., error="agent completed a step without advancing or parking a proposal")`.
- Return `True` when it marked errored so the poll does not set off the ticket.

Keep prompt composition in `_next_step_prompt`, but update comments to state System A owns next-step prompt shaping and this function will grow.

Update `poke` docstring: it is now an idle callback from bare System B threads, not `MindQueue`.

### `src/planner/runtime/readiness.py`

Update module comments to remove `has_inflight` language at `src/planner/runtime/readiness.py:4`.

Do not add status checks here. The injected guard should continue to check state/fields/ceiling/blocked only; status is checked atomically in `start_run_if_runnable`.

### `src/planner/minds/queue.py`

Delete this file.

### `src/planner/minds/__init__.py`

Remove `MindQueue` import/export at `src/planner/minds/__init__.py:25`.

Update package docstring if it still claims queue behavior.

### `src/planner/core/loops.py`

No major behavior change. Keep `system_b.set_idle_callback(system_a.poke)` at `src/planner/core/loops.py:111`, but update comments to say “finished run thread” instead of `MindQueue`.

### `src/planner/core/contracts.py`

Add:

```python
run_in_progress = "run_in_progress"
```

to `ErrorCode`.

This is the structured conflict used when a chat turn collides with an active run.

### `src/planner/core/server.py`

Map `ErrorCode.run_in_progress` to HTTP `409` in `_STATUS_BY_CODE` at `src/planner/core/server.py:38`.

### `src/planner/chat/service.py`

Change `_resolve` for ticket IDs at `src/planner/chat/service.py:42` to select both `chat_session_key` and `status`.

For day chat, behavior stays unchanged.

For ticket `send` and `run_command`:

- Start with `tickets_data.start_chat_turn(conn, entity_id, worker="chat", now=now)`.
- Then call `gateway.send` / `gateway.run_command` outside the transaction.
- Persist returned session key as today.
- On success, call `tickets_data.finish_run_if_still_working(..., session_key=result.session_key)`.
- On exception after chat start, call `tickets_data.mark_run_errored(..., error=...)` before re-raising the structured error.

Keep first-key race behavior in `_persist_key`; do not regress the current lost-race and stale-key tests.

### `src/planner/chat/api.py`

No route shape change.

`send_message` and `run_chat_command` should surface `PlannerError(ErrorCode.run_in_progress, ...)` naturally through server error handling.

Keep human-only gates at `src/planner/chat/api.py:46` and `src/planner/chat/api.py:65`.

### `src/planner/tickets/api.py`

Import `TicketStatus` only if needed for endpoint validation.

Add routes:

- `POST /tickets/{ticket_id}/takeover`
- `POST /tickets/{ticket_id}/release`

Both call `reject_agents(ctx)`.

`release` should call `_poke(sa)` after writing `empty`.

Do not poke after `takeover`.

Existing `accept_field` already pokes at `src/planner/tickets/api.py:357`; after accept writes `empty`, that poke becomes the main continuation path.

### `src/planner/tickets/views.py`

`ticket_json` already includes `"status"` at `src/planner/tickets/views.py:46`; no schema change needed.

Update docstring at `src/planner/tickets/views.py:31` to remove “System B is the sole writer”.

Board cards already include status at `src/planner/tickets/views.py:197`; add `user_takeover` marker support in frontend only unless board needs a special chip.

### `assets/components.js`

Add `user-takeover` to `CHIP_MARKERS` near `assets/components.js:143`.

Update `chatPanel(entityId, opts)` at `assets/components.js:840`:

- Accept `opts.status`.
- If `opts.status === "agent_working"`, render online presence but disable/omit the composer and show a concise busy state.
- Add `aria-disabled`/disabled attributes on the textarea and send button if the composer remains visible.
- Keep offline behavior unchanged.
- Do not use client state as the guard; server 409 remains authoritative.

### `assets/screens-ticket.js`

Update header markers at `assets/screens-ticket.js:349`:

- Existing `agent_working` and `errored` markers stay.
- Add `user_takeover` marker.
- Add a small human-only control:
  - If `detail.status !== "user_takeover"`, show “Take over” button.
  - If `detail.status === "user_takeover"`, show “Release” button.
  - Use existing `submit`/`headSave` error behavior and POST the new endpoints.
- Pass ticket status into chat rail:

```js
C.chatPanel(id, { available: statusRes.available, status: detail.status })
```

at `assets/screens-ticket.js:575`.

## Test Plan

All tests must stay hermetic: fake gateway/adapters only, no real child process.

### Update Existing Tests

`tests/unit/test_system_b.py`:

- Update parked proposal test: proposal source writes `awaiting_approval`; message.complete does not clobber it.
- Update auto-accept test: run completion now leaves status `empty`, not `awaiting_approval`.
- Replace `test_no_proposal_errors`: System B complete no longer errors directly; assert it ends `empty`, then System A no-progress guard marks `errored`.
- Delete `test_has_inflight_true_during_run_then_clears`.
- Replace double-set-off/MindQueue serialization tests with DB-guard tests:
  - Two concurrent `set_off` calls on an `empty` ticket result in one successful `agent_working` start/spawn; the loser re-reads non-empty in `start_run_if_runnable` and skips.
  - A queued/stale set-off after a proposal parks skips because status is not `empty`.

`tests/unit/test_system_a.py`:

- Candidate query excludes `awaiting_approval`, `agent_working`, `errored`, and `user_takeover`.
- Accept resets to `empty`; after accept and `_poke`, poll sets off the next step.
- Auto-accepted step ends `empty`; poll continues the chain.
- No-progress guard marks `errored` before re-set-off.
- Remove expectations that `awaiting_approval` is a candidate for auto-accepted advancement.

`tests/unit/test_minds.py`:

- Delete queue tests at `tests/unit/test_minds.py:435`.
- Remove `MindQueue` imports and helper classes only used by queue tests.

`tests/unit/test_tickets_engine.py`:

Add focused data-layer tests:

- `file_proposal` parked branch sets status `awaiting_approval`.
- Auto-accept proposal does not set `awaiting_approval`.
- `accept_proposal` writes status `empty`.
- `take_over_ticket` writes `user_takeover`; `release_ticket` writes `empty`.
- `start_run_if_runnable` returns `None` without calling guard when status is not `empty`.

`tests/unit/test_chat_seed.py` and `tests/unit/test_chat_commands.py`:

Add fake gateway cases:

- Ticket chat while status `agent_working` returns HTTP 409 with `run_in_progress`.
- Successful ticket chat writes `agent_working` during the call and `empty` after success.
- Ticket chat gateway failure marks `errored`.
- Day chat remains unchanged and has no ticket status writes.
- `/command` shares the same collision guard as `/send`.

Use blocking fake adapters to assert mid-call status without real child processes.

### Add/Update E2E

Add a ticket-screen flow in existing Playwright e2e coverage:

- Force a ticket to `agent_working` through test setup or API.
- Assert chat composer is disabled/absent.
- Force `user_takeover`.
- Assert header shows the takeover marker and the release control.
- Release and assert status returns to `empty`.

Keep this inside the existing fake/test-mode server.

## Gated Commit Sequence

Each commit must pass `./verify`.

1. **Status schema and data transitions**
   - `TicketStatus.user_takeover`
   - schema version 4 and `CHECK`
   - data-layer status helpers
   - proposal parking -> `awaiting_approval`
   - accept -> `empty`
   - takeover/release data functions
   - targeted data tests

2. **System B without MindQueue**
   - bare daemon thread set-off
   - atomic status guard in `start_run_if_runnable`
   - dumb complete -> `finish_run_if_still_working`
   - error -> `mark_run_errored`
   - remove `has_inflight`
   - delete `minds/queue.py` and queue exports/tests
   - rewrite System B tests

3. **System A as continuation brain**
   - candidate SQL `status = 'empty'`
   - remove `has_inflight` call
   - add no-progress guard
   - update prompt ownership comments
   - rewrite System A tests

4. **Chat collision guard**
   - `ErrorCode.run_in_progress`
   - HTTP 409 mapping
   - ticket chat start/finish/error status transitions
   - `/send` and `/command` guarded
   - chat tests

5. **Takeover API and UI**
   - takeover/release routes
   - header marker/control
   - chatPanel receives status and disables on `agent_working`
   - frontend/e2e coverage

6. **Cleanup pass**
   - remove stale comments saying System B is sole status writer
   - remove stale `MindQueue` references in docs/comments where code-adjacent
   - run `rg "MindQueue|has_inflight|System B is the sole writer|awaiting_approval"` and reconcile remaining references
   - final `./verify` green

## Non-Goals

- No retry path from `errored` back to `empty`; note as recovery gap.
- No live migration.
- No new in-memory runtime guard replacing `MindQueue`.
- No real Hermes child in tests.
