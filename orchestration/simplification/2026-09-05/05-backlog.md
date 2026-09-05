# Make Backlog show the work that is actually waiting

Status: design for root review; implementation not yet dispatched.

## Intent and evidence

Backlog currently means only Sprint Items whose `sprint_id` is null. That omits the
canonical unparented Tickets: the live snapshot has 12 active unscheduled Tickets
that the screen does not show. The three unscheduled Sprint Items are still useful
briefs and each currently owns Tickets, so they should remain reachable rather than
being reinterpreted or deleted.

The screen has two related defects. Its Sprint Item rows link back to `#/backlog`, so
they open nothing. A canonical `#/workspace/item/<id>` address already exists, but
`BoardRoute` rejects an Item that is absent from today's board before the Item detail
resource can answer for it. Backlog must link to Workspace, and Workspace must allow a
direct Item address to load an Item that is not represented on today's board.

Backlog is a view over existing records. It gets no record, status, authority, or
backend resource of its own.

## Contract decision

Use the existing bounded list endpoints:

- active unscheduled Tickets: `GET /api/ticket-summaries?sprint_id=null&limit=30&offset=<n>`;
- unscheduled Sprint Item briefs: `GET /api/sprint-item-summaries?sprint_id=null&limit=30&offset=<n>`.

The Ticket endpoint already excludes every Worker type's done and dropped stages by
default, filters canonical `tickets.sprint_id IS NULL`, returns a short
`recap_preview`, and supplies `page` facts. The Item summary endpoint supplies the
same page contract without loading each brief body. Do not use `/api/tickets`,
`/api/items`, board cards, or full Ticket details to synthesize these lists. Do not
download field prose.

Root owns these exact frontend wire shapes in `web/src/lib/types.ts` before dispatch:

```ts
export type ListPageFacts = {
  match_count: number;
  return_count: number;
  limit: number;
  offset: number;
  omitted_before: number;
  omitted_after: number;
  complete: boolean;
  next_offset: number | null;
};

export type TicketSummary = {
  id: string;
  title: string;
  worker_type: string;
  stage: string;
  ticket_status: string;
  priority: Priority;
  project_id: string | null;
  project: string | null;
  sprint_item_id: string | null;
  sprint_item: string | null;
  sprint_id: string | null;
  effective_sprint_id: string | null;
  recap_preview: string;
};

export type TicketSummariesResponse = {
  tickets: TicketSummary[];
  page: ListPageFacts;
};

export type SprintItemListSummary = {
  id: string;
  title: string;
  status: string;
  priority: Priority;
  deadline: string | null;
  project_id: string;
  project: string;
  sprint_id: string | null;
};

export type SprintItemSummariesResponse = {
  items: SprintItemListSummary[];
  page: ListPageFacts;
};
```

`SprintItemSummary` and `SprintItemsResponse` remain the existing unbounded
`/api/items` shapes used elsewhere; the summary endpoint does not return their `kind`
field, so merging the two contracts would be inaccurate.
There are no Python contract, data, API, migration, or database changes.

## Screen behavior

`web/src/lib/queryCatalogue.ts` adds parameterized Backlog queries whose keys include
their offset and whose URLs use the two summary endpoints above. Each list has its own
offset because there is no reason for paging Tickets to move the briefs list.

`BacklogRoute.svelte` becomes two explicit sections:

1. **Tickets** is the primary backlog. It groups the returned page by P0–P3 and links
   every row with `workspaceAddress({ kind: "ticket", id })`. The row uses only summary
   facts: title, Project, state/Worker label where useful, and the bounded recap preview.
2. **Unscheduled briefs** keeps the Sprint Items visible as a smaller secondary list.
   Each row links with `workspaceAddress({ kind: "item", id })`, showing its title,
   Project, priority, and deadline from the summary response. The brief body remains
   in its canonical Workspace screen.

Both sections always show the current returned range and total from `page`. Previous
is available when `offset > 0`; Next uses `next_offset` and is unavailable when null.
Changing page updates only that section. Empty, loading, and error states remain
separate so one failed resource does not hide the other. The headline count names the
active unscheduled Ticket total; it does not add Tickets and Sprint Items together as
if they were interchangeable.

The current “New backlog item” form must stop creating Sprint Items. No reusable
Ticket creation component exists in the current frontend or its history, so do not
invent a shared component with one caller. Keep the compact form local to Backlog and
submit the canonical `POST /api/tickets` body: selected served `worker_type`, title,
`kickoff_note`, Project, optional explicit priority and deadline, plus explicit
`sprint_id: null` and `sprint_item_id: null`. Worker choices come from
`queries.workerTypeManifests()` and Project choices from `queries.projects()`; do not
copy either catalog or hardcode `coding`. The form uses the same creation defaults and
validation as every other Ticket creator. A successful commit reaches the list through
the existing global change signal. If a reusable canonical Ticket creation component
has landed on the final integration base before dispatch, use it instead and delete
the local form state rather than keeping two creation paths.

`BoardRoute.svelte` must treat an address-selected Item id as sufficient to mount
`SprintItemWorkspace`. Remove the effect that redirects an Item merely because it is
absent from the board-derived rail. The Item workspace query then owns not-found and
other read errors, just as `TicketRoute` already does for a directly addressed Ticket.
The rail still contains only board-represented Items and highlights only rows it owns.
No `workspaceAddress.ts` contract change is needed.

## Scope and minimum gates

After root lands the exact `web/src/lib/types.ts` shapes above, the implementation
ticket owns `web/src/routes/BacklogRoute.svelte`, the direct-Item handling in
`web/src/routes/BoardRoute.svelte`, the two Backlog entries in
`web/src/lib/queryCatalogue.ts`, Backlog/Workspace-only rules in `assets/app.css`,
`web/tests/backlog-ideas.test.mjs`, focused query-catalogue/type tests if needed, and
the Backlog prose in `docs/backlog-and-ideas.md` and `docs/frontend.md`.

Update the existing Backlog browser fixture rather than adding an E2E suite. Its
focused proof must show that the screen requests bounded unscheduled Ticket and Item
summaries, reports page ranges/totals, moves each list independently through page
facts, creates an explicitly unscheduled Ticket through `/api/tickets`, links a Ticket
to its canonical Workspace Ticket address, and links an unscheduled Item to its
canonical Workspace Item address. Add the smallest Workspace fixture assertion that a
direct off-board Item address mounts `SprintItemWorkspace` instead of being rewritten;
do not exercise conversation behavior.

Minimum gates:

```text
npm --prefix web run check
node web/tests/backlog-ideas.test.mjs
```

Run any existing focused Workspace-address/component test changed to prove the
off-board Item boundary. The final program run owns `./verify`, the production build,
and broad frontend coverage. Conversation code, conversation CSS, agent backends,
Ticket creation actions, Sprint Item actions, and every Python backend file are
protected.

## Review

Pending root review.
