# Review

Review is Khushal's queue: one pass through everything that is waiting on him, oldest first, so he never has to open each Ticket or conversation to find it.

## What it holds

- **Proposals parked for Khushal.** A proposal whose holder is Chief or a Manager is that agent's to decide, in its own conversation. It is not in this queue. See [Ticket lifecycle](tickets.md).
- **Unread messages to Khushal** from any agent conversation: a Worker, a Manager, or Chief.

Items about one Ticket share a card. A card is as old as its oldest item. Messages drop out when their Ticket is Done or backlogged, or when their Manager is [settled](managers.md). Proposals stay until they are decided.

## How it works

Review reads other records. Membership and order come from the navigation index, which tracks attention without a read of every conversation. Actions use the ordinary writers: decisions use the proposal resolver, replies use Send Message, and read marks update the conversation. Each action clears only what that card showed. The next card is whatever is now oldest.

The proposal on a card is the same approval surface as on the Ticket page. Both keep one note box beside Approve and Send it back. The note goes with either decision. Both decisions use the displayed proposal revision, so a replacement proposal cannot receive the old decision. Skipping a card defers it for this visit only.

Approve and Send back submit the complete note with the exact visible proposal. The successful decision saves one attributed instruction for the Worker. A stale proposal fails without a success message. Retries keep the same decision identity and cannot duplicate the decision or its instruction.

The conversation system delivers that instruction and shows it in Full history as a system message. It names the authenticated User, Chief, or Manager and the proposal. A busy Worker receives it through the existing queue. Queue admission does not prove receipt. Undelivered notes survive stage changes and restart. An uncertain delivery stops automatic replay. The existing explicit Worker Restart authorizes recovery, including another attempt when the previous delivery remains uncertain.

Protected decision operations retain the exact command and its outcome with the Ticket transition. A lost response can be recovered without a second decision. A receipt proves the transition, not delivery of a note or guidance into a conversation. Edited approval text and send-back guidance must fit the canonical limit before any receipt stores them. Receipt lookup reveals only the caller’s own operations. Unknown and other actors’ operations return the same supported absence. A new operation can change the Ticket, so submission refuses an unavailable identity without exposing its saved contents.

If the index is not yet trustworthy, Review says it is unavailable instead of showing an empty queue that might be wrong.

On iPhone, Review and its badge share a snapshot of saved outgoing work. Only changes to outgoing work refresh that snapshot. Hidden Review screens do no store work. The visible queue uses memory and preserves the current decision and reply rules. If a conversation is replaced, its open card becomes historical and read-only. Its displayed messages and draft stay intact.

## Where it shows up

Review is a primary destination in the browser, with a count, and the initial tab on iPhone. The final page lets Khushal message Chief and revisit skipped items. Its composer shares Chief’s draft and keeps each send tied to its original destination. Check delivery reads the existing attempt without another send. [Notifications](notifications.md) are a separate path. They tell Khushal that something newly needs him, and link to the Ticket.

## Decisions that shape it

- **Review is derived, not stored.** It cannot drift from the proposals and messages it shows.
- **Only what is addressed to Khushal.** Supervisors handle what is addressed to them, so the owner's queue stays short.
- **Fail closed.** An unknown queue is shown as unknown, never as empty.

## Code

- `src/planner/navigation/`: the index, and Review's membership and ordering (`review.py`, `review_logic.py`).
- `web/src/routes/ReviewRoute.svelte` and `web/src/components/Review*.svelte`: the pass and its cards.
- `ios/Panels/Features/Review/`: the native pass and its saved-work snapshot.

_Last verified: 2026-11-14._
