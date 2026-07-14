# AD04 implementation plan — Ticket-only Review

## 1. Fixed outcome

Replace the current generic Queue read surface with one Ticket-only Review surface.

The only HTTP read is `GET /api/review`. It returns exactly:

```json
{
  "ticket_decisions": [
    {
      "ticket_id": "t_...",
      "field": "plan",
      "title": "...",
      "waiting_since": 123
    }
  ],
  "running_worker_count": 1
}
```

There is no `/api/queues`, old response key, item branch, overdue branch, compatibility alias, or dual
resource key. The Review UI, shell badge, and shell presence consume the same `review` resource.

## 2. Exact backend contract

### `src/planner/tickets/views.py`

Replace the Queue section with a Review section. The public function is exactly:

```python
def review_view(
    conn: sqlite3.Connection,
    *,
    day_id: str,
) -> JsonDict: ...
```

It has no `now`, date, item rows, overdue rows, optional inputs, or defaults.

Use one Ticket-specific private reader, named `_ticket_decisions`, with this responsibility:

1. Read non-terminal Tickets attached to `day_id` in Ticket-id order.
2. For each row, resolve the definition for its stored `worker_type` only to interpret the stored Stage's
   gated field.
3. Exclude `ticket_status=agent_running_step`.
4. Exclude a terminal/ungated Stage, malformed field payload, or a current field without a proposal exactly
   as today. Integrity errors from an unknown Worker type or invalid stored Stage still raise; Review does
   not create a fallback.
5. Emit exactly `ticket_id`, `field`, `title`, and proposal `created_at` as `waiting_since`.
6. Sort by `(waiting_since, ticket_id)`. This makes the current stable equal-time Ticket-id behavior
   explicit.

The view separately executes the existing global count:

```sql
SELECT COUNT(*) AS count FROM tickets WHERE ticket_status = 'agent_running_step'
```

It returns exactly `ticket_decisions` and `running_worker_count`.

Delete `_entity_type`, `_approval_digest`, `_approvals`, `_overdue_digest`, `_overdue`, `_TICKET_CLOSED`,
`_ITEM_CLOSED`, and the now-unused `ItemStatus` import. Update the module docstring/section comment to Review.
Do not move overdue calculation elsewhere.

### `src/planner/tickets/api.py`

Replace:

```python
@router.get("/queues")
async def queues(...): ...
```

with exactly:

```python
@router.get("/review")
async def review(conn: DbConn, cfg: Cfg, clk: Clk) -> JsonDict:
    day_id = resolve_day_id("today", clk.now(), cfg.boundary_hour)
    return tickets_views.review_view(conn, day_id=day_id)
```

Remove `planning_date` and `sprints_views` from this module because the deleted Queue route was their sole
use. Update the module docstring from Queue to Review. No redirect or hidden old handler remains.

### `src/planner/sprints/views.py`

Delete `approval_item_rows` and `overdue_item_rows`, their section, and the module-docstring claim that
Sprint views feed Queue rows to Ticket views. Remove imports only if they become unused; keep all Sprint
status/deadline behavior used by Sprint itself. This is deletion, not relocation.

## 3. Exact frontend contract

### `web/src/lib/types.ts`

Delete `QueueEntry` and `QueuesResponse`. Add exactly:

```typescript
export type ReviewTicketDecision = {
  ticket_id: string;
  field: string;
  title: string;
  waiting_since: number;
};

export type ReviewResponse = {
  ticket_decisions: ReviewTicketDecision[];
  running_worker_count: number;
};
```

No optional compatibility fields or item union remain.

### `web/src/routes/ReviewRoute.svelte`

Rename the resource handle and all helper/parameter/local names from Queue vocabulary to Review/Ticket
decision vocabulary:

```typescript
const review = resource<ReviewResponse>("review", (signal) =>
  fetchJson("/api/review", { signal })
);
```

Use `review.data?.ticket_decisions || []` and `review.data?.running_worker_count ?? 0`.

Every entry is a `ReviewTicketDecision`:

- the key is `${decision.ticket_id}:${decision.field}`;
- detail key/path are always `ticket:${decision.ticket_id}` and `/api/tickets/${decision.ticket_id}`;
- the stale check compares the detail's current proposal and gated field with `decision.field`;
- open Ticket, title edit, accept, and send-back are unconditional Ticket paths;
- item headings, `/api/items`, `entity_type` conditions, generic `kind`, and item casts disappear;
- the Review card exposes `data-ticket-id={decision.ticket_id}` and `data-field={decision.field}`;
  `data-entity-id` and `data-kind` disappear from this Ticket-specific surface;
- send-back remains hidden for kickoff exactly as today;
- error/loading/manifest/skip/open/title/gate/revision/keyboard markup and visible copy remain unchanged.

Rename `refreshQueuesAfter` to `refreshReviewAfter`. Avoid the existing duplicate follow-on fetch:

- accept and send-back omit `review` from `mutateJson`'s expected-invalidations array and then await the one
  explicit `review.refresh()` that advances the card;
- title edit keeps `review` in its mutation invalidations because it has no explicit refresh wrapper;
- every other expected invalidation key remains unchanged.

Dispose `review` on destroy. Rename `.review-queue-line` to `.review-ticket-decision-line` in both the
component and `assets/app.css`; declarations and visual values are byte-for-byte unchanged.

### `web/src/App.svelte`

Import and use `ReviewResponse`. Create the shared shell handle with key `review` and endpoint
`/api/review`. The badge reads `ticket_decisions.length`; presence reads `running_worker_count`. Keep the
same badge/presence rendering, copy, routing, and disposal timing.

### Mutation invalidation call sites

Change mutation invalidations only where the mutation can change a Review input:

- `web/src/routes/TicketRoute.svelte` splits the current shared list into a base list without Review and a
  Review-dependent list that adds `review`;
- `patch(body)` uses the Review-dependent list only when `"title" in body`; project, priority, deadline,
  implementer, and other Ticket metadata patches use the base list;
- `acceptField` uses the Review-dependent list because accepting a gated field removes a decision;
- takeover/release uses the Review-dependent list because it changes `ticket_status` and therefore the
  global running-worker count;
- scope, note, field-value, and recap mutations use the base list because none changes `ReviewResponse`;
- `web/src/routes/BacklogRoute.svelte` deletes the obsolete `queues` key from Sprint-item creation and does
  not replace it with `review`, because Sprint items are absent from Review;
- `ReviewRoute.svelte` behaves as specified above: title changes invalidate Review, while accept and
  send-back perform one explicit refresh instead of also invalidating it through `mutateJson`.

Do not refactor the resource cache itself; AD08 owns the catalogue.

## 4. Event invalidation contract

In `web/src/lib/eventMapping.mjs`, remove `review` from the prefix-only Ticket mapping and add it only for
event kinds that can change `ReviewResponse`:

- Ticket events `stage_changed`, `proposal_accepted`, `proposal_superseded`, `proposal_filed`,
  `kickoff_proposal_filed`, `kickoff_accepted`, `approval_returned`, `ticket_status_changed`, and
  `ticket_deleted` add `review`;
- `ticket_updated` adds `review` only when `payload.field === "title"`; priority, deadline, implementer,
  project, and sprint edits do not change Review;
- `day_ticket_added` and `day_ticket_removed` add `review` only when
  `options.todayId === entityId || options.includeTodayAlias`; membership on another known day does not
  change Review;
- `web/src/lib/ws.ts` captures `const cachedTodayId = todayId()` once per event and calls
  `keysForEvent(plannerEvent, { todayId: cachedTodayId, includeTodayAlias: cachedTodayId === null })`.
  This preserves direct-Review and cold-start live updates conservatively until `day:today` has been read,
  while known off-day membership remains excluded;
- `ticket_created` itself does not add `review`. Creation also emits `proposal_filed` and
  `ticket_status_changed` before day placement; those event kinds conservatively add Review because their
  frontend payloads contain no day-membership snapshot and the same kinds can change an already-placed
  Ticket. This bounded safe refetch is intentional; distinguishing the creation batch would require an
  unearned event/payload change outside AD04;
- Ticket-scoped Chat/session/turn events, note/recap/value/scope events, link events, and Sprint-item events
  do not add `review` because none changes a Review input;
- generic blocker-link and affected-blocked-target augmentation does not add `review`.

Keep this bounded set beside `keysForEvent`; do not build AD08's general catalogue. The event-kind
completeness test still requires every backend event kind to map to at least one resource, whether or not it
maps to Review.

Update `web/tests/event-mapping.test.mjs` exact arrays. Preserve the completeness assertion over every
backend event kind and all other resource keys.

## 5. Behavior traces

### Parked Ticket decision

1. The API resolves today's planning-day id.
2. `review_view` reads only Ticket membership for that day.
3. The stored Worker type definition interprets the stored Stage's `field`.
4. A parked proposal becomes one `ReviewTicketDecision`, ordered by creation time then Ticket id.
5. Review fetches only the Ticket detail and verifies the proposal is still current.
6. Accept uses the same canonical Ticket accept endpoint; send-back uses the same direct revision endpoint;
   title uses the same atomic Ticket edit endpoint.
7. One explicit refresh after accept/send-back advances Review. Ordinary events and title mutation target
   the `review` resource.

### Running worker

`running_worker_count` counts every Ticket with `ticket_status=agent_running_step`, regardless of day
membership and regardless of whether any Ticket decision is listed. A running Ticket is excluded from
`ticket_decisions`. Settlement can both reduce the global count and add a parked decision; the Ticket event
invalidates `review` once.

### Sprint item and overdue deletion

No Sprint view produces Review rows. No Ticket view reads item rows or deadlines for Review. No API route
computes a planning date for an overdue digest. No frontend type or branch can fetch item detail from
Review. Past-due Tickets and Sprint items affect their existing Ticket/Sprint screens only.

## 6. RED-first tests

### Backend contract tests

Delete `tests/unit/test_queues_approval_type_driven.py` and add
`tests/unit/test_review_ticket_decisions_type_driven.py`.

Before implementation, the new test must fail on the missing `review_view`/new payload. Cover:

- coding and `new_worker` parked proposals use their own gated fields;
- the existing test-only probe Worker type uses its novel field;
- only supplied-day Tickets appear; no-day/other-day proposals do not;
- running Tickets are excluded from decisions;
- equal `waiting_since` values order by Ticket id, and older proposals order first;
- every decision has exactly the four locked keys;
- response has exactly `ticket_decisions` and `running_worker_count`;
- a running off-day Ticket contributes to the global count;
- past-due Ticket and Sprint item fixtures do not add a response field or entry.

Update the existing kickoff, revision, and runner tests in:

- `tests/unit/test_tickets_engine.py`;
- `tests/unit/test_employee_step_runner.py`; and
- `tests/unit/test_return_for_revision.py`.

They call `review_view` or `/api/review`, assert `ticket_decisions`, and use `ticket_id`/`field`. Preserve all
existing behavioral assertions, especially revision leaving Review while running and returning with the
new proposal timestamp.

Add an API contract assertion in the renamed Review unit test or an existing allowed API test: `/api/review`
returns the exact payload and `/api/queues` returns 404. Do not create a compatibility test fixture.

### Frontend/invalidation tests

Update `web/tests/event-mapping.test.mjs` to prove:

- the listed proposal/Stage/status/deletion Ticket events include `review`;
- current-day membership events include `review`, while the same events for another known day omit it;
- an unknown cached today id uses `includeTodayAlias` so a direct-Review cold start cannot miss a membership
  update;
- `ticket_created` omits Review while its companion `proposal_filed` and `ticket_status_changed` events
  conservatively include it for the payload-limitation reason above;
- Sprint-item-only events omit `review`;
- no mapping emits `queues`.

Also prove Ticket-scoped `chat_message_recorded`, `chat_turn_started/updated/finished`, and
`chat_session_created` do not emit `review`; title `ticket_updated` does, while a priority update does not.
Add source assertions for the mutation split: Ticket title/accept/takeover paths include Review, Ticket
scope/note/value/recap paths omit it, and Backlog Sprint-item creation contains neither `queues` nor
`review`. Assert that `ws.ts` passes `includeTodayAlias` exactly when its one captured `cachedTodayId` is
null, so the cold-start guarantee cannot disappear silently.

Add source/static assertions to the renamed Review test that the scoped live files contain none of:

- `/api/queues`, `queues_view`, `approval_item_rows`, `overdue_item_rows`;
- `QueueEntry`, `QueuesResponse`, `refreshQueuesAfter`, the `queues` resource key;
- Review `entity_id`, `entity_type`, `kind`, `approvals`, `overdue`, `running_agents`;
- Review `data-entity-id` / `data-kind` attributes; or
- `.review-queue-line`.

The guard must parse/search only live scoped files so standard-library `queue.Queue`, `MindQueue`, ordinary
enqueue prose, historical orchestration, and unrelated approval language do not create false positives.

### E2E preservation

Update `tests/e2e/test_flows_a.py` and `tests/e2e/test_flows_b.py` from `/api/queues`/`approvals` to
`/api/review`/`ticket_decisions`, and from generic keys to `ticket_id`/`field`. Rename the “Queue departure”
comment to Review language. Update `tests/e2e/test_ticket_file_previews.py` only where its Review-card
selector uses `data-entity-id`; it must select the locked `data-ticket-id` instead. Preserve every
assertion's meaning and browser behavior.

The browser suite must continue to prove empty Review, badge, decision arrival/departure, open/skip,
approve, send-back, title edit, shortcut, error/stale behavior, and disabled/offline request handling. Add
only the smallest old-route 404 assertion if no focused API test owns it.

## 7. Documentation and served bundle

Update:

- `docs/frontend.md` from `queues` to the `review` resource;
- `docs/systems.md` and generated `docs/systems.html` to the same resource name and Ticket-only meaning;
- live module docstrings/comments in the three Python files and the renamed test/e2e comments.

`CONTEXT.md` and `D-review-ticket-only` already state the intended model and need no delegated product edit.
Orchestrator memory remains outside the bounded implementation.

Run the production frontend build after reviewed source changes. Keep only generated:

- `web/dist/index.html` if its script hash changes;
- deletion of the old hashed `web/dist/assets/index-*.js`; and
- addition of the new hashed `web/dist/assets/index-*.js`.

Do not hand-edit dist or include unchanged font/CSS files.

## 8. Bounded implementation allowlist

Production/backend:

- `src/planner/tickets/views.py`
- `src/planner/tickets/api.py`
- `src/planner/sprints/views.py`

Frontend/source and shared CSS:

- `web/src/lib/types.ts`
- `web/src/lib/eventMapping.mjs`
- `web/src/lib/ws.ts` (pass the existing mapping options only)
- `web/src/routes/ReviewRoute.svelte`
- `web/src/routes/TicketRoute.svelte`
- `web/src/routes/BacklogRoute.svelte`
- `web/src/App.svelte`
- `assets/app.css`
- `web/tests/event-mapping.test.mjs`

Backend/unit/e2e tests:

- delete `tests/unit/test_queues_approval_type_driven.py`
- add `tests/unit/test_review_ticket_decisions_type_driven.py`
- `tests/unit/test_tickets_engine.py`
- `tests/unit/test_employee_step_runner.py`
- `tests/unit/test_return_for_revision.py`
- `tests/e2e/test_flows_a.py`
- `tests/e2e/test_flows_b.py`
- `tests/e2e/test_ticket_file_previews.py` (Review selector only)

Docs:

- `docs/frontend.md`
- `docs/systems.md`
- `docs/systems.html`

Generated build paths only:

- `web/dist/index.html`
- old/new `web/dist/assets/index-*.js`

No schema, migration, data writer, Ticket contract, Sprint contract/data/API, resource-cache implementation,
event kind, worker/session/chat, other route, design asset, root instruction, or configuration path is in
scope. A discovered need outside this list returns to the orchestrator before implementation.

## 9. Implementation order and checks

1. Add/rename the Review tests and event-mapping assertions; run the focused set and record the expected
   missing-contract failures.
2. Replace the backend view/API and delete Sprint item/overdue helpers.
3. Replace TS contracts, Review/shell consumers, mutation keys, event mapping, and CSS selector.
4. Update unit/e2e callers and live docs; run the static old-name/path guard.
5. Run focused Review backend tests, event-mapping Node test, affected e2e tests, Ruff, mypy, Svelte check,
   frontend tests, production build, docs Markdown/HTML parity checks, `git diff --check`, and exact path
   audit.
6. Leave all implementation unstaged for independent Codex diff review.
7. After accepted review fixes and a clean corrected-diff re-review, commit the bounded checkpoint and run
   the canonical `PYTHONPATH="$PWD/src" ./verify` once. Do not run it during implementation.

## 10. Non-goals

No UI redesign, copy change, new Review feature, item Review replacement, overdue replacement, filter,
pagination, persistence, migration, event, lifecycle rule, revision/session change, general resource
catalogue, Chat work, Markdown work, or Employee-history work is part of AD04.
