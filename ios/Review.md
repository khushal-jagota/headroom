# Review on iOS

Review is the root at launch and opens on its summary screen. Each card has one
purpose: a proposal, one conversation's unread run, or an agent's ask.

## The queue

The queue and badge come from the navigation view's review window, which the phone
follows over the view's events stream; desktop builds its queue from the same
window. The server admits one entry per Ticket (its proposal, unread conversations
and ask) and one per Manager or Chief conversation (its unread run and ask). The
phone splits each entry into cards in window order: the proposal, then each unread
run, then the ask. The badge still counts entries, as the server does. A saved view
keeps no runtime facts, so there an ask shows only where a window member has
nothing else waiting.

The shell owns Review's session and keeps it current wherever Khushal is. The card on
screen is always in the latest window: when the window drops it, because it was
read, decided or answered elsewhere, the next card shows. A later window that adds
older work leaves the shown card in place. Only three things keep a card the window
no longer holds:

- its own exit, while the card fades after Khushal handled it here;
- a refused decision, which comes back to the front with the server's reason and
  the saved note, even when its proposal now waits for someone else. Outside the
  window it comes back only while the view shows that exact proposal still waiting.
  Each refusal comes forward once; Skip moves on;
- a proposal that moved to another reviewer while on screen. It stays as a notice
  that says who reviews it now, its actions are off, and Skip or the swipe moves on.
  A move Khushal makes from the card's own Reviewer sheet takes the card away.

A card handled here stays hidden until its saved work or the window hides it, so it
never shows again before then; a refusal brings it back. A refusal or a move that
happens while Khushal is elsewhere just waits for the summary screen.

Skip is per card and local, like the web. When every card of one Ticket or one
conversation is skipped, its key goes to the server in `review_skipped_keys`. The
return button sends the skipped history as `review_priority_keys` and offers every
card again, new work first and the skipped entries last, as the web returns. Skips
last for one app run.

## Summary screen

The summary shows at launch, after the last card, after a visit to another Sidebar
destination, and on a return to the app when the card that was open left the window
meanwhile. Otherwise leaving and returning to the app keeps Khushal on the same card,
and a page or artifact opened from a card returns to it. Offline, nothing fresh
arrives, so he stays where he was.

Its counts come from the phone's own cards in the loaded window; entries beyond the
window count as new. It reads, in two tones at one upper-centre baseline:

| State | Lines | Button |
| --- | --- | --- |
| New and skipped | `3 to review.` / `1 new, 2 skipped.` | Back to review |
| Skipped only | `2 skipped.` / `Nothing new.` | Review skipped |
| New only | `2 new.` / `Ready to review.` | Back to review |
| Clear | `All clear.` / `Nothing to review.` | none |

The button uses the quiet charcoal fill, never the accent. A plain line, `4 agents
working right now`, sits above the composer when agents are working; without it
nothing moves. Open Chief sits in the top bar. Message Chief is anchored at the
bottom and shares Chief's draft text and attachments. A message is saved in the
outbox and clears only its unchanged draft in the same save, so newer edits survive.
It is sent without a conversation ID: the server delivers it to Chief's current
conversation or starts one, and a resend with the same ID lands there once. Only a
refusal shows; a refused photo message puts its draft back. Pull down to refresh
works on the summary and on every card.

## Cards

Every card has its title and recap, its content, and its controls docked at the
bottom. The top bar holds Open ticket for a Ticket's card, or Open conversation for
a Manager's or the Chief's. Review uses the system font throughout and two button
tiers: the quiet secondary fill and the accent fill with dark ink. Failures show on
the card itself; there are no toasts, banners or connection notes.

Swipe left past 30% of the screen to skip, or use Skip. The swipe never approves.
The card follows a leftward finger drag; releasing short of the threshold returns it
without bounce. Skip fades the card out over 120ms and the next card fades in over
200ms. Reduce Motion keeps direct finger tracking and uses 120ms fades. Writes give
light feedback on submission and success or error feedback on response. Skip uses
selection feedback; crossing the swipe threshold uses a light impact.

The next cards are read ahead, three at a time with the shown one: a proposal with its
worker manifest, a message with its unread run, an ask with its wording. Each read is
also saved on the phone. Offline, a message or ask card uses that saved copy, so Mark
read and an answer still save. A card this phone never read acts on the review window
instead. Each conversation in the window carries its newest message to Khushal and the
ask its turn waits on, if that ask is 4 KB or less. A message card then shows only its
title and recap. Its Mark read reads through that newest message, so a later message
stays unread. This is a deliberate exception to "Mark read acknowledges only the
displayed run": the card shows the run as the window last saw it. A run the card did
read always bounds Mark read. Reply waits for the run. An ask card shows the window's
ask and answers it through the outbox, but only in place of no read or a read made
before that ask was raised. The view's ask counts only while the view's last fresh
snapshot is under an hour old. A card in a view saved or kept offline longer than that
waits for the connection, as does a card whose ask is larger than 4 KB.
The server sends the ask only with the review window.

### Proposal card

The read-only proposal card, shared with the Ticket page, shows the stage on the left
and the reviewer (`You ⌄`) on the right; the reviewer opens the shared Reviewer
sheet. Below it the dock holds the note for the worker with its Attach button and
microphone, then Skip, Return and Approve in one equal-width row. Attach uses the
shared Attach sheet, paste and attachment tray; pictures are prepared as a message's
are, and kept beside the note under the same draft identity. Nothing in it is editable: Khushal returns
a proposal with a note. Return is absent for briefs; with no existing worker
conversation it is off and says why. An empty Return focuses the note. Links resolve
against the server address; a managed file opens the in-app viewer and other links
open in the system (`ReviewArtifacts.swift`). Only linked photos and videos also show
inline. While a proposal loads, its card keeps the previous card's height.

The viewer docks the card's own controls below the file: a proposal's note and
decision row, or a message's reply and Mark read, on the card's decision, so a note
typed there is the card's draft. A decision, Return, Skip, reply or Mark read there
follows the card's order: the card is held at the save, a sheet closes first, every
stacked viewer closes, then the queue moves on. When the card stops being the shown
one, as when another card's refusal comes to the front, the viewers close.

Approve opens the shared approval sheet: a standard grouped Form with Cancel, a
title and Approve. Approved until is a checkmark list and Then reviews is a native
segmented control. Nothing is written until Approve is pressed. When the next stage
is Done, Approve takes one tap. The Ticket's saved workflow supplies the available
gates; Review does not assume coding stages. After approval succeeds, the card's exit
waits for the sheet's dismissal.

A decision is saved before its card leaves the screen, with no receipt or status
row. It freezes its canonical command, with the note's pictures and files, its
operation identity and proposal identity once, so every resend is the same bytes.
An applied receipt clears the note and its pictures; a refusal keeps both.
It first looks up the original operation receipt, even if the Ticket already
advanced. Only an explicit receipt absence permits a POST with the same ID and exact
frozen bytes. The server commits the decision and receipt together. A lost response
is followed by another receipt lookup, so it cannot make a second decision. The
receipt confirms the decision, not delivery to an agent's prompt. Approval carries
the exact revision that was read; if the proposal changes, Review keeps the unsent
note and requires a reload. Proposal identities include the known revision, and saved
decisions mask only that exact revision. A newer proposal on the same Ticket does not
inherit an older refusal.

The microphone records AAC locally after permission, then calls the existing
conversation voice-transcription endpoint. Words append to the note. The recording
is saved on disk first, and the outbox transcribes it, including after reconnect or
relaunch. Meanwhile the note reads `saved`; it reads `transcribing`, with a spinner,
only while the request is in flight. A failed transcription keeps the audio and
offers Retry. Explicit discard removes it.

### Message card

A message card shows its `Message` header, then the complete unread run as markdown,
attached images and files, and linked photos and videos inline. The dock holds the
`Reply…` composer with its inline send, then Skip and Mark read. Reply uses the same
composer, attachments and voice controls as Conversation; its draft is separate from
the conversation's and survives Skip and relaunch. A reply is saved in the outbox with
its conversation ID, message identity and the last displayed message sequence, and
counts as handled at once. It acknowledges only that sequence: a later message stays
unread and returns to Review. A lost connection or response resends it with the same
identity, and the server drops the repeat. A refused reply returns to its card with
its words and attachments, and Try Again sends the corrected words under a new
identity with the original read boundary. A refused reply or Mark read brings its
card to the front once per refusal, with its reason; only the conversation's latest
reply or read counts, and a later read point settles it. A reply that gave up may
already be on the server, so Try Again sends it again as it was, under its own identity.

Mark read acknowledges only the displayed run, joins the outbox, and the card leaves
at once until the server's read point catches up. A card read ahead before its
conversation's latest event moved reads its run again as it is shown, with the
read-ahead run on screen meanwhile. An open message card follows the owner's current
conversation: after a replacement it says the conversation is historical, keeps only
Skip, and keeps its draft and displayed run. Open conversation pushes the conversation
page on top of Review; Back returns to the card.

### Ask card

An ask card reads its wording from the conversation, as the web's ask card does
(`pending_user_input` or `pending_permission_ask`). A worker question lists its
options as choices and docks Skip and Send answer. When the question takes words, the
app's shared composer is docked above them, with attachments off; its send answers
too, and words replace a one-answer choice. A permission ask shows its request and docks Skip, Decline and Approve,
which answer with the ask's own reject and allow options. The answer is saved through
`Outbox.enqueueAskAnswer` and the card leaves at once, and stays hidden while the
answer waits to be sent, even after a relaunch; the outbox checks the answer's
receipt and, if the ask is gone, sends it as an ordinary message. An ask answered
elsewhere just leaves.

## Saved work

Review and its badge share a server-specific snapshot of saved decision, mark read
and reply summaries. An outgoing fact change or removal refreshes that indexed query;
queue evaluation reads memory. Draft edits, cache writes and unchanged outgoing saves
do not refresh it. A different server replaces it. Review pauses store reads while its
root is hidden or the app is not active. On return it restores selected reply status
and saved voice drafts.

Deployment status lives in the Panels server row at the top of Settings, which shows
it without opening a page. Deployed expires at the server's valid-until time; expired
progress becomes Reconnecting. A failed or rolled-back deployment stays Problem.

## Focused verification

Host tests run with `ios/scripts/test-offline.sh` and use no server, browser or
Simulator.

- `ReviewQueueTests` checks the queue model: the shown card follows the window and
  only its exit holds it; the summary after a visit and after the open card left while
  away, but not on a plain return or offline; per-card skip, the entry keys, the four
  summary states and the return order; a refusal coming to the front once, including
  one that lands during an exit; the reviewer-move notice; and refusal cards built
  from each proposal's latest saved decision.
- `NavigationViewTests` checks the split against desktop's fixtures: canonical
  addresses with no fallback to a former owner, window order, the native identities,
  exclusion of backlogged, done, settled-owner and agent-held work, asks from live and
  saved views, the read bound and ask that a saved view keeps for cards never read, and
  a request that sends every field with the history keys.
- `ReviewMessageTests` covers a complete 65-message run, bounded Mark read,
  refusal, relaunch and retry with attachments, a reply that gave up keeping its
  identity on Try Again, Mark read and an ask answer saved from the phone's saved copy
  offline, the same from the window for a card never read, historical send prevention
  and recovery sender attribution.
- `ReviewDecisionTests` covers approval with the visible revision, next gate and
  holder; changed proposals; send-back guidance; a refused decision bringing back its
  note and attachments; voice; and deployment expiry.
- `OutboxRecoveryTests` (in `OfflineTests.swift`) checks that a decision's
  attachments are frozen before sending, resent as the same bytes after a lost
  request, and land once after a lost reply, and that a recording counts as
  transcribing only while its request is in flight.

Simulator checks reuse the offline stub (`ios/scripts/test-offline-simulator.py`) on
an already booted lane under its lock; `PANELS_OFFLINE_DEVICE` names the lane's UDID.
The permanent `ReviewMessagesTests/testSplitCardsSkipAndReturn` proves what model
tests cannot: on the real SwiftUI host the app opens on the summary, the proposal and
its Ticket's message are separate cards, Skip is per card, an artifact opened from a
card docks its reply with the card's draft and returns to it, and Review skipped
brings every card back. Its writes change the fixture, so it runs alone on a fresh
stub with `PANELS_REVIEW_MESSAGES=1`, `PANELS_REVIEW_SPLIT=1` and
`PANELS_OFFLINE_TEST=ReviewMessagesTests/testSplitCardsSkipAndReturn`.
`ReviewMessagesTests/testUnreadReplyRefusalQueueAndHistoricalNavigation` reaches
Review through the Sidebar and its summary, replies through a refusal and follows the
conversation route. `ReviewQueueScreensTests/testCardsNeverReadActOffline` runs alone
with `PANELS_REVIEW_QUEUE=1` and `--review`. The stub fails every card's conversation
read, then drops every connection and adds a message. Mark read, a question and a
permission are each saved from the window and land once on reconnect, and Mark read
stops before the added message.

The one-off walk `ReviewQueueScreensTests/testCardsAndSummaryStates` runs with
`PANELS_REVIEW_QUEUE=1` and `--review`. It visits every card kind and the four
summary states, answers a permission ask, and has the stub capture each screen with
`simctl`. It is a review tool, outside the permanent suite. Simulator evidence does not
establish physical-device haptic feel, frame rate, the microphone or a live
transcription provider.

See [saved data and interrupted connections](Offline.md) for the outbox.
