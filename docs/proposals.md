# Proposals and gates

A proposal is the Worker asking for the current stage’s blank to become real. Headroom treats that ask as the only way a gated field changes and the only way a stage advances. The point of the machinery is the product choice on each Ticket: how far this Worker may go before a person has to look.

## The ceiling and the holder

The ceiling is the last stage the Worker may complete on its own. A new Ticket starts with the ceiling on Brief unless creation sets it further down the ladder. While the current stage is still before that ceiling, a filed proposal settles immediately, the stage advances one step, and the field reads Auto-accepted. When the stage is at the ceiling or past it, the same filing parks. The stage does not move. The Ticket reads as awaiting approval, and the proposal is addressed to the ceiling holder.

The holder is who the parked proposal is for. It is the owner, Chief, or the Manager this Ticket is assigned to. Holding the review is narrower than general authority: Chief holds a review for a Chief-assigned Ticket, and a Manager holds a review only for its own child. Anyone who stands strictly above the Ticket may still approve or send it back. The holder says who is on the hook to look. It does not grant a private approval route.

Stage ownership is a second, quieter choice, declared by the Worker type. A worker-owned stage is the Worker’s to fill. A user-owned stage gets one automatic opening turn when the Ticket becomes ready for it, and then the person and the Worker continue in the same conversation until the blank is filled or the person completes the gate directly. Ownership does not add a second kind of parking. The ceiling still decides whether a filed proposal waits.

## How a filing is decided

Only that Ticket’s Worker may file. Khushal, Chief, and Managers do not propose on a Ticket’s behalf. Proposing is the Ticket speaking about its own work. The body is prose, or a page the holder can scan. See [Proposal pages](proposal-pages.md).

The resolver looks at the stage, the ceiling, and the holder, then does one of two things in the same transaction as the write:

- Below the ceiling: store the body, clear any pending proposal, advance one stage, and give the worker-step claim back.
- At or beyond the ceiling: store a pending proposal, leave the stage where it is, remember the holder, and give the claim back. If the holder is Chief or an ordinary Manager, a manager wake is recorded in that same transaction so the supervisor’s conversation can be prompted. An owner-held proposal does not wake a Manager. It becomes Review and, when the preference allows, a notification.

Accepting a parked proposal can still edit the body. The accept names the next ceiling and the next holder, clears the proposal, records an explicit approval, and advances exactly one stage. The visible proposal’s revision is checked so a replaced draft cannot be approved from a stale card. On the final gate the screen can supply the terminal ceiling itself.

Sending a proposal back clears it and leaves the stage. Optional revision guidance is stored for that stage and goes out with the next Worker step. The claim returns to rest, and a user-owned stage’s opener fact is cleared so the opening turn can happen again. Rejecting does not jump backward and does not invent a calendar placement. A reply typed into the conversation does not decide the proposal.

A person can complete the current user-owned stage directly, without a Worker proposal. That completion uses the same resolver and advances one stage. It is the human door for collaborative stages, not a way to skip the ladder.

## What feeds a gate, and what a gate feeds

The Worker’s conversation feeds the proposal, because the Worker has to decide to file. The Ticket’s ceiling, holder, and stage feed the routing. The result feeds settled field text, Review, manager wakes, attention flags, and the next readiness decision once the claim is at rest and the Ticket reads empty again.

Changing the ceiling or the holder on a live Ticket is an edit by someone who stands above it. The holder must still be a legal reviewer for that assignment. Moving a Ticket to another Manager without naming a new holder returns the holder to Khushal when the previous holder would no longer be legal. Chief can lose a held review that way even though Chief still stands above the Ticket.

## Where it shows up

On the Ticket, the current stage shows the proposal as an editable card: Approve, with the next ceiling and holder when the stage is not the last one, and Send back. Review shows the same card when the proposal is addressed to Khushal, one Ticket at a time, oldest first. A proposal addressed to Chief or a Manager shows up as a prompt in that supervisor’s conversation, and the Ticket still shows the parked text to anyone who opens it.

The accent on Approve is the visual meaning of the gate. See [Design](design.md). The queue itself is [Review](review.md).

## Pointers

Routing and the accept, reject, and user-completion decisions live in `src/planner/tickets/logic/proposal_routing.py` and `src/planner/tickets/logic/resolution.py`. The writers that apply them live in `src/planner/tickets/data.py`. The cards are `web/src/components/WorkerProposalCard.svelte` and `web/src/components/ReviewProposalCard.svelte`.

_Last verified: 2026-10-31._
