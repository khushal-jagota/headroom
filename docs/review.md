# Review

Review is Khushal's queue: one pass through everything that is waiting on him, oldest first, so he never has to open each Ticket or conversation to find it.

## What it holds

- **Proposals parked for Khushal.** A proposal whose holder is Chief or a Manager is that agent's to decide, in its own conversation. It is not in this queue. See [Ticket lifecycle](tickets.md).
- **Unread messages to Khushal** from any agent conversation: a Worker, a Manager, or Chief.

Items about one Ticket share a card. A card is as old as its oldest item. Messages drop out when their Ticket is Done or backlogged, or when their Manager is [settled](managers.md). Proposals stay until they are decided.

## How it works

Review is a reading of other records. It stores nothing of its own beyond where Khushal is in the current pass. Membership and order come from the navigation index, which tracks attention without loading every conversation. Acting on a card goes through the ordinary writers: approving or sending back goes to the proposal resolver, a reply goes through Send Message, and marking read updates the conversation. Each action clears only what that card showed, and the next card is whatever is now oldest.

The proposal on a card is the same approval surface as on the Ticket page. Skipping a card defers it for this visit only.

If the index is not yet trustworthy, Review says it is unavailable instead of showing an empty queue that might be wrong.

## Where it shows up

Review is a primary destination in the browser, with a count. [Notifications](notifications.md) are a separate path. They tell Khushal that something newly needs him, and link to the Ticket.

## Decisions that shape it

- **Review is derived, not stored.** It cannot drift from the proposals and messages it shows.
- **Only what is addressed to Khushal.** Supervisors handle what is addressed to them, so the owner's queue stays short.
- **Fail closed.** An unknown queue is shown as unknown, never as empty.

## Code

- `src/planner/navigation/`: the index, and Review's membership and ordering (`review.py`, `review_logic.py`).
- `web/src/routes/ReviewRoute.svelte` and `web/src/components/Review*.svelte`: the pass and its cards.

_Last verified: 2026-11-04._
