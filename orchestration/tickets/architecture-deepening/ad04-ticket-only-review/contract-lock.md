# AD04 contract lock

The orchestrator generated this lock after the corrected implementation plan passed independent review.
Implementation may replace the old Queue surface and rewire the reviewed allowlist, but it must not add a
compatibility route, response/resource/type alias, Sprint-item Review branch, overdue branch, generic
Review entry, or broader resource-catalogue refactor.

## Backend read contract

`src/planner/tickets/views.py` owns exactly:

```python
def review_view(
    conn: sqlite3.Connection,
    *,
    day_id: str,
) -> JsonDict: ...
```

It returns exactly `ticket_decisions` and `running_worker_count`. A decision contains exactly
`ticket_id`, `field`, `title`, and `waiting_since`. Decisions are parked proposals on Tickets attached to
`day_id`, exclude `agent_running_step`, and sort by `(waiting_since, ticket_id)`. The Ticket's stored Worker
type is resolved only to interpret the stored Stage's gated field. There is no fallback Worker type or
Stage derivation.

`running_worker_count` is the global count of Tickets whose `ticket_status` is `agent_running_step`; it is
not day-scoped.

`GET /api/review` resolves today's planning-day id and calls `review_view`. `GET /api/queues` is absent.
The route and view accept no time, overdue rows, item rows, optional inputs, or compatibility parameters.

## Frontend contract

`web/src/lib/types.ts` owns exactly:

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

The resource key is exactly `review` and its endpoint is exactly `/api/review`. Review always loads
`ticket:${decision.ticket_id}` from `/api/tickets/${decision.ticket_id}`. Cards expose
`data-ticket-id` and `data-field`; generic entity/type/kind branches and item detail paths are absent.
The shell badge reads `ticket_decisions.length`; presence and the Review empty state read
`running_worker_count` without changing visible behavior.

Accept and send-back omit `review` from `mutateJson` invalidations and perform one explicit Review refresh.
Title edits invalidate Review directly. Ticket-route title, accept, and takeover/release mutations include
Review; scope, note, field-value, recap, and unrelated metadata mutations do not. Backlog Sprint-item
creation removes `queues` and does not add Review.

## Event invalidation

The Ticket prefix mapping does not add Review by default. Review is added for `stage_changed`,
`proposal_accepted`, `proposal_superseded`, `proposal_filed`, `kickoff_proposal_filed`,
`kickoff_accepted`, `approval_returned`, `ticket_status_changed`, and `ticket_deleted`. A
`ticket_updated` event adds Review only for `payload.field === "title"`.

Current-day `day_ticket_added` and `day_ticket_removed` add Review. A known off-day membership event does
not. The event stream captures the cached today id once per event and sets `includeTodayAlias` only while
that id is unknown, preserving cold-start correctness conservatively. `ticket_created` itself omits Review;
its creation-companion proposal/status events conservatively include Review because their payloads do not
contain day membership. Chat/session/turn, note, recap, value, scope, link, generic blocker augmentation,
and Sprint-item events do not add Review.

No mapping emits `queues`.

## Deletion and preservation

Delete the Queue route/view/helpers, Sprint approval/overdue row helpers, generic Queue types and response
keys, item/overdue frontend branches, old resource key, old card attributes, and Queue-specific live prose.
Do not relocate or alias them. Preserve Review loading/error/empty/stale, skip/open, accept, send-back,
title edit, keyboard, manifest, badge, presence, and file-preview behavior. The CSS rename is selector-only;
the production bundle is generated from reviewed source and not hand-edited.

Any discovered need to change this skeleton or cross the corrected plan's implementation allowlist
returns to the orchestrator before work continues.
