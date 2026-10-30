# Review

Review is the pass through everything that needs Khushal, oldest first, without opening each Ticket to find it. It holds owner-addressed parked proposals and unread messages to him. It does not hold Managers as objects, and it does not hold a digest of overdue work. A proposal addressed to Chief or a Manager is that supervisor’s to see in their own conversation. It is not mixed into this queue.

## How a pass works

One card at a time. Skip defers a card for this visit. Open jumps to the Ticket or the conversation in the Workspace. Opening Review does not mark messages read. Marking read, sending a reply, or resolving the proposal clears only what that card showed.

A Ticket card can carry a proposal and one or more messages. The card’s age is the earliest of those. A message-only card is a Manager, Chief, or Ticket conversation that has an unread run addressed to him, with a reply box on the card. The proposal block is the same approval surface as on the Ticket: edit the body, approve with the next ceiling and holder, or send it back. Keyboard approve and send follow whichever block is in front.

When the queue is exhausted, Review settles. The screen says whether anything still awaits a later pass, shows how many agents are running, and offers a Chief reply that does not load the whole Chief transcript. A return pass shows fresh cards first, then the ones skipped this visit.

If the navigation index is not yet trustworthy, Review says it is unavailable rather than showing an empty queue that might be a lie.

Messages on a Ticket drop out of Review when that Ticket is Done or backlogged. Settlement of a Manager does not hide its approvals or its messages.

## What feeds Review, and what Review feeds

Parked proposals whose holder is Khushal, and unread addressed messages, feed the queue. The navigation index keeps the order and the membership so the screen does not load every transcript to decide the next card. Acting on a card feeds the proposal resolver or Send Message. Those writes update the Ticket, the conversation, and the attention flags. The next card is whatever the index says is now oldest.

Skip feeds nothing durable. It is this visit’s order.

## Where it shows up

`#/review` is the primary destination in the shell, with a count. The same proposal card exists on the Ticket for anyone who opens the work directly. Notifications can deep-link to the Ticket. They are a separate path from walking the queue. See [Notifications](notifications.md).

## Pointers

`web/src/routes/ReviewRoute.svelte` is the pass. Cards live beside it under `web/src/components/Review*.svelte`. Eligibility and ordering come from the navigation index in `src/planner/navigation/`.

_Last verified: 2026-10-30._
