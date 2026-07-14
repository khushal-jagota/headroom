# AD04 — Ticket-only Review

## Objective

Make **Review** exactly the human decision surface for parked Ticket proposals, alongside the current
running-worker count.

- Delete the unused Sprint-item approval branch and the entire overdue digest from this surface.
- Replace the generic Queue route, resource, response, entry, helper, test, and documentation vocabulary
  with Review and Ticket-decision names.
- Make the response Ticket-specific: no generic entity id, entity type, item detail branch, or generic
  decision kind remains.
- Preserve the existing Ticket decision order and all accept, send-back, skip, open-Ticket, title-edit,
  keyboard, stale-refresh, manifest, and running-worker behavior.

This is an intentional public contract replacement. There is no compatibility route, response alias,
resource alias, type alias, wrapper, or fallback for the old Queue surface.

## Binding decisions

- `CONTEXT.md`: **Review** is the canonical name. It contains parked Ticket proposals and the current
  running-worker count; it does not contain Sprint items or an overdue digest. Avoid Queues and approval
  queue.
- `D-review-ticket-only`: delete the unused Sprint-item and overdue branches; replace `/api/queues`, the
  `queues` resource key, and generic Queue names in the same change.
- Owner decision: Review is Ticket- and running-worker-focused only for now.
- `D-stored-stage-is-authoritative`: the Ticket stores Stage directly; Worker type is resolved only to
  interpret which field the current Stage gates.
- `D-live-chat-state` and `D-runtime-names`: sending a Ticket proposal back retains the existing direct
  Employee-session delivery and Panels Chat behavior; AD04 changes no turn ownership.
- `D-events-invalidate-resources`: event invalidation remains targeted and uses the live resource name.
- `PRINCIPLES.md`: name things for exactly what they are, remove unearned branches, and keep read assembly
  pure.

## Contract boundary

The delegated plan must freeze the exact declarations and payload in:

- `src/planner/tickets/views.py`;
- `src/planner/tickets/api.py`;
- `src/planner/sprints/views.py` only to delete the two Queue-only item-row helpers and their imports/docs;
- `web/src/lib/types.ts`;
- `web/src/routes/ReviewRoute.svelte`;
- `web/src/App.svelte`; and
- `web/src/lib/eventMapping.mjs` plus every mutation invalidation list that currently names the old resource.

The intended public contract is:

```python
def review_view(
    conn: sqlite3.Connection,
    *,
    day_id: str,
) -> JsonDict: ...
```

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

`GET /api/review` resolves the current planning-day id and returns exactly that response. `review_view`
accepts no time, overdue rows, item rows, or unused compatibility parameter.

`ticket_decisions` contains only Tickets attached to the supplied day whose current gated field holds a
parked proposal. Each row uses `ticket_id` and `field`, not generic `entity_id`, `entity_type`, or `kind`.
Rows retain the existing oldest-proposal-first order, with the existing stable Ticket-id order for equal
times. A Ticket at `agent_running_step` is absent until settlement parks a proposal again. Gated-field
interpretation uses the definition for the Ticket's stored Worker type.

`running_worker_count` retains the existing global count of Tickets whose control status is
`agent_running_step`; it is not limited to today's board or to Tickets currently listed for a decision.

The frontend resource key is exactly `review`. The Review route always fetches Ticket detail from
`/api/tickets/{ticket_id}` and every Review action invalidates or refreshes `review`. The shell badge reads
`ticket_decisions.length`; the shell presence and empty-state copy read `running_worker_count` while keeping
their current visible wording and behavior.

## Deletion and naming rule

Delete, do not alias or relocate:

- `GET /api/queues` and the `queues` route function;
- `tickets.views.queues_view`, `_approvals`, `_approval_digest`, `_overdue`, `_overdue_digest`, and
  `_entity_type`;
- `sprints.views.approval_item_rows` and `sprints.views.overdue_item_rows`;
- response keys `approvals`, `overdue`, and `running_agents` on this surface;
- Review-entry keys `entity_id`, `entity_type`, and `kind`;
- frontend `QueueEntry`, `QueuesResponse`, `queues` resource/variable names, `refreshQueuesAfter`, and the
  `queues` resource key; and
- live Queue/approval-queue prose, test names, comments, and generated frontend output.

Python's standard-library `queue.Queue`, Hermes `MindQueue`, the ordinary verb “queues” where it means
enqueueing work, and historical orchestration evidence are unrelated and remain unchanged.

## Planning task

Produce `plan.md` only. Do not edit implementation or contract files.

The plan must:

1. Inventory every production, frontend, test, e2e, generated-bundle, documentation, and root path affected
   by the public route/payload/resource/type replacement and branch deletion.
2. Give the exact `review_view`, HTTP, TypeScript, resource-key, and frontend consumer shapes above. No
   optional argument, old-name alias, compatibility response field, or dual endpoint is allowed.
3. Trace one parked Ticket from day membership and stored Worker type through its current gated proposal,
   ordered `ReviewTicketDecision`, detail fetch, stale check, accept/send-back/title/skip/open behavior, and
   targeted refresh.
4. Prove the running-worker count remains global and changes with Ticket control status independently of
   whether a parked decision exists.
5. Delete the Sprint-item approval and overdue call graph completely, including the now-unused
   `ItemStatus`, deadline/date, cross-view, API, test-fixture, and generic entity-dispatch machinery it
   supported.
6. Decide and test the exact event-invalidation mapping for the `review` resource: every Ticket or current-day
   membership event that can change the response must invalidate it; no old `queues` key remains. Remove
   Sprint-item-only invalidation only where the deleted item/overdue branches were its sole reason.
7. Preserve the current visible Review behavior and keyboard/accessibility contracts while simplifying all
   item conditionals to unconditional Ticket paths.
8. Define a bounded implementation allowlist. A renamed Queue test is an explicit delete plus add, and the
   checked-in `web/dist` entry/hash changes are build-only outputs from the reviewed source.
9. Add static assertions that fail if the old route, functions, response fields, TS types, resource key,
   generic Review entry fields, Sprint-row helpers, or Queue-specific identifiers return in live code.

## Acceptance

- `GET /api/review` returns exactly `ticket_decisions` and `running_worker_count`; `/api/queues` is absent.
- Every decision row contains exactly `ticket_id`, `field`, `title`, and `waiting_since`, and only today's
  parked Ticket proposals are returned in the preserved order.
- Both shipped Worker types and a test-only novel Worker type use their own current gated field.
- A running Ticket is not a decision; `running_worker_count` still counts all running Tickets globally.
- Sprint-item approvals, item detail branching, and Ticket/Sprint-item overdue calculation are deleted, not
  hidden in another helper or response field.
- The Review UI keeps its current visible behavior for empty, loading, error, stale, accept, send-back,
  title edit, skip, open, keyboard, manifest, badge, and presence cases.
- Event and mutation invalidation use only the `review` resource name and remain complete for its actual
  Ticket/day/status/proposal inputs.
- No live Queue/approval-queue surface, old resource key, old response field, generic Review entry field,
  compatibility alias, or dead Sprint helper remains.
- Live Markdown/HTML docs describe Review and the `review` resource accurately; served `web/dist` is built
  from the reviewed source.
- The full canonical `./verify` passes once after implementation and independent review fixes.

## Out of scope

- Changing Ticket proposal, acceptance, revision, Stage, scope, or Employee-session behavior.
- Adding Sprint-item review, an overdue screen, a new digest, filters, pagination, persistence, events, or
  database migration.
- Redesigning Review visuals, copy, navigation, keyboard shortcuts, or interaction order.
- Refactoring the general resource cache/catalogue beyond the exact `queues` to `review` replacement; AD08
  owns the full resource catalogue.
- Chat ingress/turn, managed Markdown, or Employee-session-history work from AD05–AD07 and AD09.
