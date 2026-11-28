# Review

Review shows everything waiting on the owner, oldest first.

## What it holds

- **Proposals parked for the owner.** A proposal whose holder is Chief or a Manager is that agent's to decide, in its own conversation. It is not in this queue. See [Ticket lifecycle](tickets.md).
- **Unread messages to the owner** from any agent conversation: a Worker, a Manager, or Chief.
- **Asks waiting for the owner.** A running turn can stop on a question or a permission ask that only the owner can answer. The ask stays until it is answered or the turn ends.

Items about one Ticket share a card. A Manager's or Chief's ask is a card of its own. A card is as old as its oldest item, and an ask is as old as the moment it was raised. Messages and asks drop out when their Ticket is Done or backlogged, or when their Manager is [settled](managers.md). Proposals stay until they are decided, except that a Ticket behind an unfinished blocker is not waiting on him yet: its parked proposal leaves the queue and its count, and comes back when the last blocker is Done. Its messages and asks stay.

## How it works

Review reads other records. Membership and order come from the navigation index, which tracks attention without a read of every conversation. Actions use the ordinary writers: decisions use the proposal resolver, replies use Send Message, read marks update the conversation, and answers use the conversation's own answer routes. Each action clears only what that card showed. The card on screen stays that card until the person acts or it leaves. The next card is the oldest one still waiting at that moment, and that choice stays when the queue refreshes.

The proposal on a card is the same approval surface as on the Ticket page. Both keep one note box beside Approve and Send it back. The note goes with either explicit decision. Every composer shares one picker, file paste, and a drop onto the whole box. Replies, approval notes, and free-text question answers accept the same file types and sizes, including videos and controls beneath an open artifact. They go with the decision and reach the Worker with the note. Text entry never chooses a proposal decision. Both decisions use the displayed proposal revision, so a replacement proposal cannot receive the old decision. Skipping a card puts it on this device's skipped list. See [Skipped cards](#skipped-cards).

Approve and Send back submit the complete note with the exact visible proposal. The successful decision saves one attributed instruction for the Worker. A stale proposal fails without a success message. Retries keep the same decision identity and cannot duplicate the decision or its instruction.

The conversation system delivers that instruction and shows it in Full history as a system message. It names the authenticated User, Chief, or Manager and the proposal. A busy Worker receives it through the existing queue. Queue admission does not prove receipt. Undelivered notes survive stage changes and restart. An uncertain delivery stops automatic replay. The existing explicit Worker Restart authorizes recovery, including another attempt when the previous delivery remains uncertain.

Protected decision operations retain the exact command and its outcome with the Ticket transition. A lost response can be recovered without a second decision. A receipt proves the transition, not delivery of a note or guidance into a conversation. Edited approval text and send-back guidance must fit the canonical limit before any receipt stores them. Receipt lookup reveals only the caller’s own operations. Unknown and other actors’ operations return the same supported absence. A new operation can change the Ticket, so submission refuses an unavailable identity without exposing its saved contents.

The browser and iPhone use this same route. The browser saves unresolved decisions across closed tabs and checks their receipts when it opens again. An uncertain result retains the original decision and blocks a conflicting choice. A changed proposal requires another review. Its replacement card keeps the refusal visible beside the proposal.

An ask shows in the same card style as a proposal. A permission ask answers with one click on its options. A question shows its numbered options and a field for the owner's answer, and Send answer submits them. The ask itself is read from its conversation. The view's conversation record also carries the waiting ask in the review window, while it is still open and 4 KB or less, and the sequence of the newest message to the owner, so the phone can act offline on a card it never read. The phone's outbox can send each answer with an operation ID and resend it, because the server returns the saved fate for a repeat ([conversations](conversations.md#models-and-asks)). Answering removes only that part of the card. The agent then continues, and a second ask in the same turn shows in its place.

If the index is not yet trustworthy, Review says it is unavailable instead of showing an empty queue that might be wrong.

On iPhone, Review and its badge share a snapshot of saved outgoing work. Only changes to outgoing work refresh that snapshot. Hidden Review screens do no store work. The visible queue uses memory and preserves the current decision and reply rules. If a conversation is replaced, its open card becomes historical and read-only. Its displayed messages and draft stay intact.

## Skipped cards

Skip is a decision of one device, not of the server. The browser and the desktop app each keep a skipped list in their own storage, and the server never hears of it. A skip on one device leaves the card waiting on every other device. The iPhone keeps its own list in the same way.

Review then has two passes. Review shows the cards that are not skipped. "Review skipped" shows the skipped ones, so the owner goes back to them on purpose. The button stands in the top band of each card, with the count. In the skipped pass it reads "Review new". The final page offers both buttons when both kinds wait. Each pass keeps its order while the owner goes through it: a skip hides the card and moves nothing.

A skip ends when the card changes: a new proposal revision, a new message, or a new ask. The card is then new again. A second message in a conversation that was already unread does not change it, until it is read. A skip also ends when the card leaves the queue.

## Where it shows up

Review is a primary destination in the browser, with a count, and the root page on iPhone. The final page summarizes new and skipped items, shows active agents, and lets the owner message or open Chief and return to the new or the skipped cards. Its composer shares Chief’s draft. In the browser, replies use the same [send and queue behavior](conversations.md#how-a-send-works) as conversations: a review reply steers into the running turn, so a busy agent gets it at once and the card leaves when the conversation records it. A reply with attachments, a model change, a backend that cannot steer, a refused steer or an offline machine still waits for the turn to end, and the reply box says "Queued. It goes when Chief finishes its turn." while it waits. A reply is not drawn on the card while it goes: its send button spins, then shows a green tick when the conversation recorded it, a red cross when it was refused, or an amber question mark when it may not have arrived, and a refused or uncertain reply returns to the box. A reply that home accepted and has not delivered shows no mark and keeps its card open until its outcome comes. Skip and Open conversation let the owner move on. Panels checks for a lost response by message ID. Try Again stays tied to the original destination, and Edit returns a refused reply to the draft. An interrupted upload can return to the draft so the owner can attach the file again. The iPhone sends without a conversation ID, so the server delivers to Chief's current conversation, and a resend with the same ID lands once. Agents read the same queue with `panels review`. [Notifications](notifications.md) are a separate path. They tell the owner when a new item needs attention and link to the Ticket.

A file linked from a card opens over that card, inside Review. Under it stand the controls of what held the link: the note, Send it back and Approve for a proposal, or the reply for a message. They are the card's own controls and keep the same note. Close or Escape returns to the card. A decision, reply or Skip that finishes the card moves to the next card and takes the file with it. A decision that leaves part of the card, such as a decided proposal with messages still waiting, drops the file onto what remains. A decided card leaves at once, before the queue's refresh confirms it. A reload shows the card without the file. The iPhone app has its own file viewer and does not show this.

## How it moves

Review moves like one deck of cards. A card that is done or skipped leaves to the left, and the next card arrives from the right while it leaves, so the screen is never blank. The final screen arrives the same way, and so does the way back from the final screen: every move comes in from the right. A file is a layer on the other axis: it rises to open and drops to close, and a decision that finishes the card sends the file to the left with it. On a Ticket with a proposal and messages, a decided part leaves to the left and the rest of the card closes the gap. The leaving card is the real card, held where it stood until it has gone. With reduced motion every change is a short fade. The distances, durations and curves are tokens in `assets/tokens.css`.

The Ticket page follows the same rule. A file linked from its proposal opens with the note and decisions in place of the conversation. A file linked from anywhere else keeps the conversation under it.

## Decisions that shape it

- **Review is derived, not stored.** It cannot drift from the proposals and messages it shows.
- **Only what is addressed to the owner.** Supervisors handle what is addressed to them, so the owner's queue stays short. An ask is addressed to the owner because no agent can answer it.
- **Fail closed.** An unknown queue is shown as unknown, never as empty.

## Code

- `server/navigation/`: the index, and Review's membership and ordering (`views/review.ts`).
- `web/src/routes/ReviewRoute.svelte` and `web/src/components/Review*.svelte`: the pass and its cards. `ReviewAskCard.svelte` draws and answers an ask.
- `web/src/lib/review/deckMotion.ts`: the moves, and `web/src/components/ReviewDeck.svelte`, which holds the one thing on screen.
- `ios/Panels/Features/Review/`: the native pass and its saved-work snapshot.

_Last verified: 2026-11-25._
