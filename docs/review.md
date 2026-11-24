# Review

Review is Khushal's queue: one pass through everything that is waiting on him, oldest first, so he never has to open each Ticket or conversation to find it.

## What it holds

- **Proposals parked for Khushal.** A proposal whose holder is Chief or a Manager is that agent's to decide, in its own conversation. It is not in this queue. See [Ticket lifecycle](tickets.md).
- **Unread messages to Khushal** from any agent conversation: a Worker, a Manager, or Chief.
- **Asks waiting for his answer.** A running turn can stop on a question or a permission ask that only he can answer. The ask stays until it is answered or the turn ends.

Items about one Ticket share a card. A Manager's or Chief's ask is a card of its own. A card is as old as its oldest item, and an ask is as old as the moment it was raised. Messages and asks drop out when their Ticket is Done or backlogged, or when their Manager is [settled](managers.md). Proposals stay until they are decided.

## How it works

Review reads other records. Membership and order come from the navigation index, which tracks attention without a read of every conversation. Actions use the ordinary writers: decisions use the proposal resolver, replies use Send Message, read marks update the conversation, and answers use the conversation's own answer routes. Each action clears only what that card showed. The card on screen stays that card until the person acts or it leaves. The next card is the oldest one still waiting at that moment, and that choice stays when the queue refreshes.

The proposal on a card is the same approval surface as on the Ticket page. Both keep one note box beside Approve and Send it back. The note goes with either explicit decision. The box takes pictures and files as the conversation composer does: the + button, paste, and drop. They go with the decision and reach the Worker with the note. Text entry never chooses a proposal decision. Both decisions use the displayed proposal revision, so a replacement proposal cannot receive the old decision. Skipping a card defers it for this visit only.

Approve and Send back submit the complete note with the exact visible proposal. The successful decision saves one attributed instruction for the Worker. A stale proposal fails without a success message. Retries keep the same decision identity and cannot duplicate the decision or its instruction.

The conversation system delivers that instruction and shows it in Full history as a system message. It names the authenticated User, Chief, or Manager and the proposal. A busy Worker receives it through the existing queue. Queue admission does not prove receipt. Undelivered notes survive stage changes and restart. An uncertain delivery stops automatic replay. The existing explicit Worker Restart authorizes recovery, including another attempt when the previous delivery remains uncertain.

Protected decision operations retain the exact command and its outcome with the Ticket transition. A lost response can be recovered without a second decision. A receipt proves the transition, not delivery of a note or guidance into a conversation. Edited approval text and send-back guidance must fit the canonical limit before any receipt stores them. Receipt lookup reveals only the caller’s own operations. Unknown and other actors’ operations return the same supported absence. A new operation can change the Ticket, so submission refuses an unavailable identity without exposing its saved contents.

The browser and iPhone use this same route. The browser saves unresolved decisions across closed tabs and checks their receipts when it opens again. An uncertain result retains the original decision and blocks a conflicting choice. A changed proposal requires another review. Its replacement card keeps the refusal visible beside the proposal.

An ask shows in the same card style as a proposal. A permission ask answers with one click on its options. A question shows its numbered options and a field for Khushal's own words, and Send answer submits them. The ask itself is read from its conversation. The phone's outbox can send each answer with an operation ID and resend it, because the server returns the saved fate for a repeat; no phone screen answers asks yet ([conversations](conversations.md#models-and-asks)). Answering removes only that part of the card. The agent then continues, and a second ask in the same turn shows in its place.

If the index is not yet trustworthy, Review says it is unavailable instead of showing an empty queue that might be wrong.

On iPhone, Review and its badge share a snapshot of saved outgoing work. Only changes to outgoing work refresh that snapshot. Hidden Review screens do no store work. The visible queue uses memory and preserves the current decision and reply rules. If a conversation is replaced, its open card becomes historical and read-only. Its displayed messages and draft stay intact. The iPhone queue does not show asks yet. Its badge counts them.

## Where it shows up

Review is a primary destination in the browser, with a count, and the root page on iPhone. The final page lets Khushal message Chief and revisit skipped items. Its composer shares Chief’s draft. In the browser each send stays tied to its original destination, and Check delivery reads the existing attempt without another send. The iPhone sends without a conversation ID, so the server delivers to Chief's current conversation, and a resend with the same ID lands once. [Notifications](notifications.md) are a separate path. They tell Khushal that something newly needs him, and link to the Ticket.

A file linked from a card opens over that card, inside Review. Under it stand the controls of what held the link: the note, Send it back and Approve for a proposal, or the reply for a message. They are the card's own controls and keep the same note. Close or Escape returns to the card. A decision, reply or Skip that finishes the card moves to the next card and takes the file with it. A decision that leaves part of the card, such as a decided proposal with messages still waiting, drops the file onto what remains. A decided card leaves at once, before the queue's refresh confirms it. A reload shows the card without the file. The iPhone app has its own file viewer and does not show this.

## How it moves

Review moves like one deck of cards. A card that is done or skipped leaves to the left, and the next card arrives from the right while it leaves, so the screen is never blank. The final screen arrives the same way, and so does the way back from the final screen: every move comes in from the right. A file is a layer on the other axis: it rises to open and drops to close, and a decision that finishes the card sends the file to the left with it. On a Ticket with a proposal and messages, a decided part leaves to the left and the rest of the card closes the gap. The leaving card is the real card, held where it stood until it has gone. With reduced motion every change is a short fade. The distances, durations and curves are tokens in `assets/tokens.css`.

The Ticket page follows the same rule. A file linked from its proposal opens with the note and decisions in place of the conversation. A file linked from anywhere else keeps the conversation under it.

## Decisions that shape it

- **Review is derived, not stored.** It cannot drift from the proposals and messages it shows.
- **Only what is addressed to Khushal.** Supervisors handle what is addressed to them, so the owner's queue stays short. An ask is addressed to him because no agent can answer it.
- **Fail closed.** An unknown queue is shown as unknown, never as empty.

## Code

- `server/navigation/`: the index, and Review's membership and ordering (`views/review.ts`).
- `web/src/routes/ReviewRoute.svelte` and `web/src/components/Review*.svelte`: the pass and its cards. `ReviewAskCard.svelte` draws and answers an ask.
- `web/src/lib/review/deckMotion.ts`: the moves, and `web/src/components/ReviewDeck.svelte`, which holds the one thing on screen.
- `ios/Panels/Features/Review/`: the native pass and its saved-work snapshot.

_Last verified: 2026-11-22._
