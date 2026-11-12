# Saved data and interrupted connections

The phone keeps recently read Workspace, Review, ticket, orchestrator, Chief and
conversation responses in one SwiftData database in Application Support. Workspace
and Review restore from the last saved navigation view, and from the last one that
carried Workspace, so a relaunch after Review still shows Workspace. Each view
stream's first snapshot is what is saved; the stream then catches the screen up. A screen
restores its saved response before starting a fresh read. New responses apply
immediately, including changes that move rows. If the server cannot be reached,
the app replaces the current screen with a Tailscale reminder and Try again,
keeping saved responses and drafts on the phone. Retry or returning to the app
reads again. Other failed refreshes keep the last successful response visible.
A screen with no saved response shows still, rough blocks only after 300 milliseconds. The blocks never replace existing content.

Successful cached reads are saved by a serial background writer before the read
finishes. An identical response still refreshes its saved date, so it keeps its
place in eviction order. Cache reads use a fresh database context to see the
latest completed save. Drafts and outgoing messages keep their separate main-thread
writer. Outgoing work raises the saved-work observation signal. A plain draft save
does not, because each key press saves the draft. A draft saved together with
outgoing work, such as a voice transcript, still raises it.

Review and its badge share an in-memory snapshot. A separate server-specific revision
refreshes it only after outgoing facts change or a record is removed. It holds
only decision summaries. Reply and voice composers query their selected conversation
or draft. Rendered decision membership and reply status read memory. An unchanged
outgoing save, draft edit, or cache write does not refresh this snapshot.

Outgoing payload bytes live once in LocalRecord. Indexed OutgoingMetadata holds
mutable delivery state, attempt provenance, destinations, lane order, dependencies,
proposal identity, audio ownership and expiry. State updates and receipts never
reencode the original intent. Both installed date schemas migrate without changing
existing keys, bytes, drafts, attachment or audio references. A completion marker
shares the backfill transaction. Later opens use that exact marker and a bounded
metadata error query. Undecodable work stays visible through the persistent storage
error surface. Legacy waiting or sending work has unknown provenance and remains
uncertain rather than receiving permission to POST.

Responses are bounded to 96 entries and 16 MiB across servers. A response larger
than the budget is not cached. Drafts and outgoing work are separate records and
are never removed by this eviction. Each foreground pass first clears a due batch
of at most 64 outgoing metadata rows through the expiry index. The foreground loop
runs a pass when it starts and every 3 seconds. Sent and changed work expires after
24 hours, and uncertain work after 7 days. Failed and pending work has no age limit.
Before an uncertain predecessor expires, its dependent message receives a durable
hold in the same transaction. The expired operation leaves no receipt or payload
shadow. A known destination requires Send this message. An unknown destination
requires an explicit destination or a new-conversation choice. Later dependent
messages follow that choice. Expired audio is unlinked after commit only when no
retained metadata record on any origin owns it.
Everything is partitioned by server origin;
changing the server address cannot send one server's work to another server.

Conversation text, Review edits, send-back guidance and Ideas text are saved as
they change. A Review draft belongs to the exact ticket, field, proposal time and
revision. A failed local save is shown in the app; a send cannot clear its composer
until the outgoing message and composer clear have committed together.

## Sending

Messages appear immediately with a clock and “Waiting for connection”. They keep
their original sender identity, timestamp, owner, conversation and attachment
bytes. The outbox preserves logical order within each conversation lane. Before
creation returns, queued messages share their exact owner/start lane. Decisions
use a ticket lane and voice uses a draft lane. At most two requests run per server,
and at most one per lane. A stalled lane leaves the other server slot available.
Foreground entry, network-path
recovery and the foreground retry loop prompt a drain. URLSession and the existing
change, navigation view and conversation streams remain the network interfaces. A network path is
only a reason to try; request results determine delivery.

A proven-unattempted message in “Waiting” goes straight to the send POST without
receipt or held-message reads. The drainer saves attempted provenance and “Sending”
before the POST. Responses that confirm
started or injected delivery mark the message “Sent” as before.

For an existing conversation, attempted or uncertain work only checks its exact
receipt. An absent receipt never grants permission to POST again. Server-queued
work also remains receipt-only, but its confirmed acceptance releases the next
submission without a wait for the eventual prompt receipt. A timeout, HTTP 408,
or interrupted response retains uncertainty. A receipt settles its own operation
even outside the visible transcript.

A confirmed refusal permits an explicit retry with a new identity. An uncertain
resend requires confirmation that delivery can duplicate. The replacement occupies
the original logical position, and the dependency transfer commits atomically.
The original retains its independent receipt outcome until expiry. It cannot
retarget the replacement's successors after a late response. An unknown creation
replacement stays held until its explicit destination choice.

When a creation returns, only unattempted dependent messages receive its destination.
Their backend field is creation-only. Model and nullable effort changes compare
with the immediately preceding captured choice, so an explicit change and a later
revert both survive. Existing-conversation messages omit unchanged settings.

A message that created a conversation but lost the response has no known
conversation ID to query. It remains “Delivery uncertain” rather than being
silently sent into another conversation. Messages accepted into the server's
queue are retained locally and checked for receipts, but are not automatically
re-admitted if the server loses that queue. Server queue durability is outside
this app change. The app does not claim exactly-once delivery across a server
crash or across an unknown newly created conversation.

## Decisions and recordings

Approval and send-back first enter the local outbox. The ask leaves Review, and
Review shows no row for the saved decision. Approvals send the original expected proposal
revision. Send-backs also send the saved proposal revision. Both fetch the proposal
immediately before posting and compare field, creation time and revision.
A mismatch or a server refusal of the revision marks the decision as changed,
and the ask returns to the queue.

The server checks the expected revision in the same transaction as either decision,
so a change between the final read and the POST is refused. Neither decision endpoint
has an operation receipt. A response lost after a decision starts is held for review,
never automatically replayed.

Review microphone recordings use files in Application Support, with an owning
outbox record saved before recording starts. Stopping or leaving the composer
keeps the audio. Returning online transcribes saved audio; the returned words and
completed operation commit together before the file is deleted. A failed
transcription retains the audio. An interrupted recording is offered for recovery
when its composer opens; a damaged recording can still fail transcription.
Explicit discard removes pending audio. Uncertain audio follows the seven-day
retention window and the committed ownership check above.

Automatic draining runs while the app is in the foreground. Suspended or
force-quit apps cannot promise an immediate send; foreground relaunch restores
and drains saved work. Conversation speech still uses the existing Speech path;
the disk-backed audio queue described here is the Review recording path.

## Verification

Run `ios/scripts/test-offline.sh` for host tests. It reuses the conversation tests
and ticket fixture. The added tests cover cache eviction versus durable work,
server separation, atomic composer clearing, relaunch order and identity, receipt
reconciliation after a lost response, changed proposals for both decisions,
ambiguous decision responses, and atomic transcription/draft persistence. Each
protects data loss or duplicate writes that the previous in-memory tests could
not establish. No new browser host is needed.

`PANELS_OFFLINE_OUTPUT=/absolute/evidence/path ios/scripts/test-offline-simulator.py`
uses the existing UI-test target and one localhost stub. It takes
`/tmp/panels-PanelsLaneA.lock`, requires the lane already booted, and uses only
`app.panels.ios.pios-offline`. The connected boundary is real controls, on-disk AAC
recording, process termination and restored UI, which host model tests cannot
prove. It captures cold loading, offline actions, and relaunch/reconnect clips.
It never calls the live Panels server or boots, shuts down or erases a simulator.

Review replies keep the displayed read boundary in the same persisted message as
the sender identity and attachments. Reconnect, queue acceptance and explicit retry
preserve that boundary. Review drafts are scoped to the conversation separately
from the full conversation composer. The draft clears after accepted delivery; a
refusal or uncertain response keeps it available.

Goal pages retain accepted readings and unfinished text or manual commands while
the app runs. These edits use the Goal and measure APIs directly, outside the
message outbox. There is no automatic replay after reconnect or relaunch. A failed
manual write keeps its exact request identity and payload for an explicit retry.
Conflicts load the accepted reading and require discarding the old edit before a
new write. A failed page edit keeps its captured revision; Retry save cannot
overwrite a newer server page. Messages from goal pages use their holder’s durable message outbox. Retired
goal-owned queued messages retain their words and never replay.
