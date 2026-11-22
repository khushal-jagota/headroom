# Saved data and interrupted connections

The phone keeps recently read Workspace, Review, Ticket, Manager, Chief, and
conversation display in one SwiftData database in Application Support. Workspace
and Review restore admitted navigation display, including reduced patches.
Conversation display retains bounded history. Saved display remains stale and
supplies neither stream coverage nor read credit. Fresh streams establish their
own authority. Navigation and conversation owners checkpoint during updates and
on background entry.

The current owner reference in the navigation view selects the conversation on
screen. A replacement or a cleared reference supersedes saved transcript and
outbox IDs, and clears the old display before the next transcript arrives.
Unsent drafts and attachments remain with their owner. Explicit history links
retain their requested conversation and remain read only when it is no longer current.

Saved responses can supply display while fresh reads run. New responses apply
immediately, including changes that move rows. A failed refresh keeps the content
on screen. A conversation read that cannot reach the server shows nothing and reads
again when the connection returns. With no saved response, rough blocks appear
after 300 milliseconds.

Successful reads publish before serial background cache writes. Bounded pending
work coalesces replacements by server and key. An identical response still refreshes
its saved date. Cache errors remain separate from saved-work errors. Drafts and
outgoing work keep their separate main-thread writer. A plain draft save does not
wake saved-work observers, because each key press saves the draft.

Responses are bounded to 96 entries and 16 MiB across servers. A response larger
than the budget is not cached. Drafts and outgoing work are separate records and
are never removed by this eviction. Everything is partitioned by server origin;
changing the server address cannot send one server's work to another server.

The store has one schema, in the `PanelsPhoneStore` folder. At launch the app
deletes the older `PanelsPhone` folder with its store and recordings, so drafts and
unsent work from builds before this one are dropped once.

Conversation text, Review edits, send-back guidance and Ideas text are saved as
they change. A Review draft belongs to the exact ticket, field, proposal time and
revision. A failed local save is shown in the app; a send cannot clear its composer
until the outgoing work and the composer clear have committed together.

## The outbox

Every message, approval, send-back, mark read, Review recording and ask answer goes
through one outbox. It saves the action first and shows it as done at once. It then
sends it while the app is in the foreground, and sends it again by itself until the
server answers. Activation, a new item and a network change wake it. After a failed
attempt an item waits 5 seconds, doubling up to 30; that delay lives only in memory,
so a relaunch or a regained network tries again at once. Outside the foreground
nothing is sent, except the one short attempt a
[notification reply](NOTIFICATIONS.md) gets.

Each conversation owner (Chief, a Manager, a Ticket), ticket decision, draft and read
point is a lane. One request runs per lane, so an owner's messages and answers go out
in the order they were saved, from any screen, and at most two run per server.

An item is waiting (it looks done), sent, or refused. A recording is the one item
that cannot look done: its words come from the server, so it shows "transcribing"
until they arrive. Only a refusal shows a mark:

- A refused message gets a red mark in its conversation. Tapping it shows the
  server's reason and Try Again, which sends the same words with a new ID in the
  same place. A refused photo message puts its draft back in the composer.
- A refused approval or send-back shows the server's note on its proposal card,
  with the saved edits and guidance.
- A recording that cannot be transcribed shows a red mark and offers Retry.

Sent items expire after a day, and so do refused decisions, reads and ask answers,
with their notes. Refused messages and recordings stay until the person acts on them.

Reviewer-only and ceiling-only changes, backlog and the other Ticket actions call
the server directly and do not enter the outbox.

## Messages

A message keeps its sender message ID, timestamp, owner, conversation and attachment
bytes. A lost connection or a lost response sends it again with the same ID, and the
server drops the repeat. This holds for a first message too: it has no conversation
ID, the server starts the conversation once, and a server test proves that for
Chief, Ticket and Manager. If that conversation is reset between attempts, the
repeat lands in the new one. Later messages that were never sent join the
conversation the first message created. A refused first message leaves no
conversation, so later messages start their own.

The server can hold a message, for example one that names a model, until the turn
ends. The conversation shows held messages above the composer with the server's
reason, Steer in and removal.

## Reads

Mark read, from a Review card or an open conversation, moves the read point on
screen at once and joins the outbox. Each conversation keeps one waiting read, at
the highest point. A Review message card leaves at once and stays hidden until the
server's read point catches up; a newer message brings it back.

## Decisions

An approval or send-back freezes its command, operation ID and exact body bytes
once, with the proposal's field, revision and creation time. Before every submit it
checks its receipt. A receipt settles it: applied is done, refused restores the
proposal with the server's note. Only an explicit absence permits a submit, with the
same operation ID and frozen bytes. The server commits the decision and its receipt
in one transaction, or refuses it without changing the proposal. A receipt confirms
the recorded decision, not delivery to an agent's prompt. A send-back note draft
clears only after an applied receipt, and only if the draft has not changed since.

## Ask answers

An answer to an agent's ask is an outbox kind with an entry point
(`Outbox.enqueueAskAnswer`); no screen calls it yet. After a refusal or a lost
response, the phone looks up the answer's receipt first. A receipt means it landed.
A lost response with no receipt sends the answer again with the same operation ID.
A refusal with no receipt means the ask stopped waiting: in one transaction the
answer becomes one ordinary message, `Answer to "<question>": <chosen answer>`,
whose ID is derived from the answer's operation ID, so it cannot be sent twice.

## Review recordings

A Review recording is saved to a file with its outbox item before recording
starts. Stopping or leaving the composer keeps the audio. The outbox transcribes it;
the words and the finished item commit together before the file is deleted.
Explicit discard removes the audio. Conversation dictation uses the separate Speech
path.

## Goal pages

Goal pages retain accepted readings and unfinished text or manual commands while
the app runs. These edits use the Goal and measure APIs directly, outside the
outbox, and never replay by themselves. A failed manual write keeps its exact
request identity and payload for an explicit retry. Conflicts load the accepted
reading and require discarding the old edit before a new write. A failed page edit
keeps its captured revision; Retry save cannot overwrite a newer server page.
Messages from goal pages go through their holder's conversation.

## Verification

Run `ios/scripts/test-offline.sh` for host tests. They cover cache eviction against
saved work, server separation, the shared composer clear, each kind landing once
across a lost connection, a lost response and a relaunch, mark read coalescing, the
ask answer receipt rule and the refused-message mark.

`PANELS_OFFLINE_OUTPUT=/absolute/evidence/path ios/scripts/test-offline-simulator.py`
runs the UI-test target against one localhost stub that can drop connections and
responses, then relaunches the app. It proves what host tests cannot: real controls,
process termination and the restored screen after relaunch. It runs on an already
booted lane under its lock, uses a separate test bundle, and never calls the live
Panels server.
