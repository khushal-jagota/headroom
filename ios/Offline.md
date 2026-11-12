# Saved data and interrupted connections

The phone keeps recently read Workspace, Review, Ticket, Manager, Chief, and
conversation display in one SwiftData database in Application Support. Workspace
and Review restore admitted navigation display, including reduced patches.
Conversation display retains bounded history. Saved display remains stale and
supplies neither stream coverage nor read credit. Fresh streams establish their
own authority. Navigation and conversation owners checkpoint during updates and
on background entry.

Saved responses can supply display while fresh reads run. New responses apply immediately,
including changes that move rows. If the server cannot be reached, a Tailscale
reminder covers the current screen with Try again. The underlying routes keep
unfinished Goal and holder edits. Covered conversations earn no read credit.
The cover stops Review voice capture and preserves its recording for recovery.
Conversation dictation follows the existing cancellation behavior: the cover discards
the in-progress dictation session. Text already committed to the composer remains saved.
Retry or return to the app reads again. Other failed refreshes keep successful
content visible. With no saved response, rough blocks appear after 300 milliseconds.

Successful reads publish before serial background cache writes. Bounded pending
work coalesces replacements by server and key. An identical response still refreshes
its saved date. Cache reads use a fresh database context for completed saves.
Cache errors remain separate from durable-save errors. Drafts and outgoing messages
keep their separate main-thread writer. Outgoing work raises the saved-work signal.
A plain draft save does not, because each key press saves the draft. A draft saved
together with outgoing work, such as a voice transcript, still raises it.

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
error surface, with an origin-scoped per-item discard confirmation. Original bytes
remain until that action. Legacy waiting or sending work has unknown provenance and remains
uncertain rather than receiving permission to POST.

This phone-store migration runs forward. Opening the upgraded store with an older
app is not a supported recovery route, and older-binary readability is not proved.
If the store cannot open or backfill cannot commit, the app exposes a storage error
and keeps the store instead of deleting it or creating an empty replacement.

Responses are bounded to 96 entries and 16 MiB across servers. A response larger
than the budget is not cached. Drafts and outgoing work are separate records and
are never removed by this eviction. Each foreground pass first clears a due batch
of at most 64 outgoing metadata rows through the expiry index. Activation, enqueue,
explicit recovery and network changes wake the foreground drainer. A three-second
retry timer exists only while network/build admission allows runnable work or an
addressable receipt to reconcile. Unknown-destination uncertain messages, uncertain
voice and bodyless legacy decisions alone park without a repeating timer. Enqueue
also wakes an already-running pass, so a new lane can use its free server slot
while another request is stalled. Limits remain one request per lane and two per
server. Sent and changed work expires 24 hours after completion; retired failed
originals also expire after 24 hours. Uncertainty gets seven days from entry into
that state (legacy uncertainty starts at migration), and repeated polls do not
extend it. Scheduled failed work and pending work have no age limit.
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
before the POST. Proven predispatch failures (not connected, cannot connect/find
host, DNS lookup failure) restore waiting and unattempted eligibility. Cancellation,
connection loss, timeout or an ambiguous in-flight response preserve uncertainty;
they cannot authorize blind replay. Responses that confirm started or injected
delivery mark the message “Sent” as before.

For an existing conversation, attempted or uncertain work only checks its exact
receipt. An absent receipt never grants permission to POST again. Server-queued
work also remains receipt-only, but its confirmed acceptance releases the next
submission without a wait for the eventual prompt receipt. A timeout, HTTP 408,
or interrupted response retains uncertainty. A receipt settles its own operation
even outside the visible transcript.

A known terminal refusal releases later submissions in its known conversation.
An unresolved creation destination instead holds dependent work until an explicit
destination/new-start choice. A confirmed refusal permits an explicit retry with
a new identity. An uncertain
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
silently sent into another conversation. Its provisional owner chain stays visible
when navigation learns a different conversation ID; that navigation fact never
retargets the attempted operation or its followers. Message receipt polls use only
indexed identity/destination metadata, without decoding photo payloads. Conversation
sync scopes metadata to its destination plus active provisional work and reuses
already-rendered content; receipt/note changes and unrelated work never repeatedly
decode retained payload history. Messages accepted into the server's
queue are retained locally and checked for receipts, but are not automatically
re-admitted if the server loses that queue. Server queue durability is outside
this app change. The app does not claim exactly-once delivery across a server
crash or across an unknown newly created conversation.

## Decisions and recordings

New approvals and send-backs freeze one canonical decision command in the local
outbox before their ask leaves Review. Its operation ID, exact body bytes and
proposal field, revision and creation time remain stable. New rows do not also
retain duplicate proposal, approval or guidance bodies. Existing bodyless rows
remain legacy and uncertain. Reading or saving one never upgrades it.

A protected decision first checks its original operation receipt, before any
current-proposal read. An applied receipt settles that exact local operation. A
refused receipt restores the exact proposal with the server's note and saved draft.
Only a supported response with an explicit null receipt permits a POST with that
same operation ID and frozen body. The first POST persists attempted provenance
before the request. Restart recovery does not reset it to ordinary unattempted work.

Before a possible POST, an unsupported server restores the card with a compatible
server note and keeps its draft. Authentication/access lookup failures restore
normal review with an actionable sign-in/access note; invalid lookup responses
restore it with a refresh/review note. Transient lookup failures remain pending.
A definitive submit HTTP 4xx, including 429, restores the exact card/draft with its
refusal and never automatically reposts. HTTP 408 and ambiguous transport/5xx or
malformed attempted outcomes remain uncertain; 426 holds for the build gate. There is no fallback to legacy decision endpoints. A local save failure
exposes the storage error and preserves the operation instead of classifying the
server as unsupported.

The server commits the decision and its receipt in one transaction, or refuses it
without changing the proposal. Native receipt checks confirm the recorded decision
outcome. They do not prove delivery to an agent's prompt. Applied receipts and
terminal refusal notes belong only to their exact proposal identity.

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
