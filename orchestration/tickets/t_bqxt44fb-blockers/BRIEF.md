# Ticket t_bqxt44fb — Make blockers the one explicit Ticket relationship

## Approved success

Panels has one explicit Ticket relationship: a Ticket can block another Ticket or a Sprint item. An active source (not `done` or `dropped`) blocks consistently; active incoming Ticket blockers stop worker readiness, active Sprint-item blockers affect planning status, and outgoing or cleared relationships are informational. People and workers see resolved, navigable `blocked by` and `blocks` context. Obsolete link kinds and duplicate membership are removed without a generic relationship graph or Ticket-screen redesign.

## Approved approach

Narrow the existing link subsystem in place. Migrate to `blocks` only, centralize one active-blocker rule, expose resolved summaries through existing contracts/API/CLI/copy/UI, and preserve Sprint-item membership in `tickets.sprint_item_id`, deletion cleanup, events, affected-entity reporting, invalidation, and readiness ringing.

## Approved implementation plan

1. Work only in branch `ticket/t_bqxt44fb-blockers` and worktree `/private/tmp/panels-t_bqxt44fb`.
2. Reduce relationship vocabulary/schema constraints to `blocks`; safely remove obsolete rows, require real Ticket → Ticket/Sprint-item endpoints, reject self/duplicates, and cycle-check active blockers only. Test migration on disposable databases.
3. Build one resolved blocker read model and use its active rule for Ticket detail/readiness and Sprint-item status, including `done` and `dropped` sources and blocked children.
4. Tighten API and purpose-built CLI writes while preserving transactions, events, affected IDs, invalidation, doorbell ringing, and Ticket-deletion cleanup.
5. Give worker lookup/copy and the existing Ticket screen named, navigable incoming/outgoing/cleared context. UI is read-only and minimal; no graph or relationship editor.
6. Use strict TDD. Update live docs. Obtain independent Codex review, run canonical `./verify` in the worktree, commit the verified branch, and stop. Do not merge or migrate the live database.
7. Closeout later owns merge, live DB backup/migration, post-merge `./verify`, readback, and cleanup.

## UI placement approved in planning

Add a small read-only `Blockers` section within the current Ticket document, after Recap and before gated fields. Two groups show `Blocked by` and `Blocks`; each resolved row links to the Ticket or Sprint item and distinguishes active from cleared. Existing editing behavior and header marker remain unchanged.

## Required plan-review corrections

1. A blocker source state change that activates or clears its outgoing blocks must report every affected target, invalidate those Ticket/Sprint resources, and ring readiness only after commit.
2. Active-only cycle checks apply both when creating a block and when reopening a `done` blocker. Sprint items are terminal graph targets; blocks do not propagate through them.
3. Bump `SCHEMA_VERSION` and rebuild the existing `links` table so its persisted `CHECK` changes to `blocks` only. Sequence this after legacy `sprint_items.blocked_by` conversion, filter obsolete rows, remove `idx_links_one_belongs_to`, preserve `tickets.sprint_item_id`, and remove seed `belongs_to` writes.
4. Validate real source Ticket and target Ticket/Sprint-item existence inside the same `BEGIN IMMEDIATE` block used for cycle/read/insert checks. Cover missing endpoints and add/delete serialization; API and CLI remain thin.
5. Own one resolved blocker summary contract in the backend and use it for readiness, Ticket detail, `/tickets/by-session`, copy text, and Sprint-item direct/child status. Both `done` and `dropped` are cleared. `worker my-ticket` JSON and copy text expose the summaries; the automatic worker prompt is unchanged.
6. Replace obsolete-kind deletion tests with incoming and outgoing `blocks` coverage, including Sprint-item targets, survivor `link_removed` events, `linked_entity_ids`, affected invalidation, and exactly one readiness ring per committed action.
7. Ticket rows navigate directly to `#/ticket/<id>`. Sprint-item rows use the smallest real navigation contract: `#/sprint?item=<id>` selects/scrolls/focuses the existing item row without creating a new screen.

## Required evidence

- Exact RED and GREEN focused commands for new behavior.
- Migration proof on disposable legacy database copies, including deletion of obsolete relationship rows without changing canonical `tickets.sprint_item_id`.
- Focused backend/API/CLI/runtime/UI tests, then one full `./verify` output.
- Independent review with every finding addressed or explicitly refuted.
