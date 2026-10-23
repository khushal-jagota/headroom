# Saved data and interrupted connections

The phone keeps recently read Workspace, Review, ticket, orchestrator, Chief and
conversation responses in one SwiftData database in Application Support. A screen
restores its saved response before starting a fresh read. New responses apply
immediately, including changes that move rows. A failed refresh keeps the last
successful response. A screen with no saved response shows still, rough blocks
only after 300 milliseconds. The blocks never replace existing content.

Responses are bounded to 96 entries and 16 MiB across servers. A response larger
than the budget is not cached. Drafts and outgoing work are separate records and
are never removed by this eviction. Everything is partitioned by server origin;
changing the server address cannot send one server's work to another server.

Conversation text, Review edits, send-back guidance and Ideas text are saved as
they change. A Review draft belongs to the exact ticket, field, proposal time and
revision. A failed local save is shown in the app; a send cannot clear its composer
until the outgoing message and composer clear have committed together.

## Sending

Messages appear immediately with a clock and “Waiting for connection”. They keep
their original sender identity, timestamp, owner, conversation and attachment
bytes. The outbox processes work oldest first. Foreground entry, network-path
recovery and the foreground retry loop prompt a drain. URLSession and the existing
change and conversation streams remain the network interfaces. A network path is
only a reason to try; request results determine delivery.

For an existing conversation, the drainer asks the receipt endpoint about the exact
sender identity before replaying a request. A receipt settles the local message
even when it is outside the visible transcript. A timeout or interrupted response
retains the message for reconciliation. A confirmed refusal says “Not sent · tap
to retry”; the explicit retry creates a new server identity because the refused
identity already has a final receipt. The old payload is retained until its
replacement has been saved.

A message that created a conversation but lost the response has no known
conversation ID to query. It remains “Delivery uncertain” rather than being
silently sent into another conversation. Messages accepted into the server's
queue are retained locally and checked for receipts, but are not automatically
re-admitted if the server loses that queue. Server queue durability is outside
this app change. The app does not claim exactly-once delivery across a server
crash or across an unknown newly created conversation.

## Decisions and recordings

Approval and send-back first enter the local outbox. The ask leaves Review and a
pending row shows its state. Approvals send the original expected proposal
revision. Send-backs also send the saved proposal revision. Both fetch the proposal
immediately before posting and compare field, creation time and revision.
A mismatch or a server refusal of the revision becomes “This changed,
review again”; saved words remain readable and selectable in the pending row.

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
Only explicitly discarding a recording removes pending audio.

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
