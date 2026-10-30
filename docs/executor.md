# The Mac executor

The executor runs a Ticket's backend child on the Mac while the VPS keeps the
Panels record. The Mac opens one connection to the VPS. Chief, supervisors, and
VPS Tickets keep their local children. A Mac Ticket starts only after the
connection and its reconciliation are ready. Codex, Claude, and Hermes are admitted
for Mac work. Admission states intended support. Missing installation or login
prerequisites still refuse runtime startup. Native provider qualification and
outage-answer qualification remain separate, outstanding checks.
Hermes exposes permission callbacks but no agent-question callback. Admission does
not add capabilities that an adapter lacks.

```text
Mac: ordinary Worker files + backend child
                  |
          one durable outbound journal
                  |
           Mac-initiated app WebSocket
                  |
VPS: canonical writers + first receipts + complete files + accepted answers
                  |
       reconnect inventory and recorded answers
                  |
Mac: the original live callbacks, if they still exist
```

Code paths: `src/planner/executor/`, `src/planner/runtime/`,
`src/planner/tickets/locale_configuration.py`.

The wire protocol is version 14, the journal format is version 2, and the SQLite
outbox schema is version 2. Each accepts only its current version. Unsupported
versions refuse before work or mutation; no conversion runs.

Migration `mac_vps_execution` installs the final schema directly after staging's
`video_upload_ledger`. Any database that ran this branch's earlier migrations must
be rebuilt. Existing staging Tickets, conversations, native usage, catalog evidence,
and video reservations retain their data.

## Native skills and artifact paths

Each new Mac Worker start receives the current canonical skills from the VPS database.
The Mac exposes their exact text through the selected provider's skills home before the backend starts.
Codex uses its resolved home. Hermes uses the same home as its native launch.
Claude uses inherited `CLAUDE_CONFIG_DIR`, or the account's ordinary `.claude` directory.
Packaged defaults never replace unavailable canonical skills.

The executor stages one complete local copy before it changes account skill links.
It repairs stale or broken canonical entries and preserves unrelated account skills and external link targets.
The normal account home, provider settings, permissions, and credentials stay unchanged.
A failed bootstrap refuses the backend start.

A new start, including a resumed provider session, reads current database rows.
Transport retries keep the snapshot that belongs to the original start command.
Adoption of a surviving child does not repeat bootstrap or restart that child.
Later starts can update the account's shared skill paths.

Each composed native Worker delivery directs the Worker to read `panels/SKILL.md` from
`PLAN_NATIVE_SKILLS_DIR` and resolve subsequent skill layers at that root.
This variable names the immutable skill generation for its start, even if later
starts update the shared account links. Retained conversations receive this context too.
Exact native catalog commands retain their raw command route and omit composed context.

Native delivery context directs artifact writes through `PLAN_TICKET_FILES_DIR`.
This instruction overrides VPS artifact destinations in the skills without changing their text.
Ticket fields retain their served `/files/tickets/<ticket-id>/<relative-path>` links.

The complete encoded start command must fit the existing one-megabyte frame limit.
Oversized or invalid snapshots refuse startup without truncation or another transport.
Both peers require this bootstrap.

Code paths: `src/planner/executor/skills.py`, `src/planner/executor/remote.py`,
`src/planner/executor/runtime.py`, `src/planner/executor/vps.py`.

## The journal is transport, not a second Panels

The Mac saves its authored work before transmission. Each submission keeps its
identity and position within its conversation. The VPS commits the write and its
first receipt together. If a receipt disappears, replay returns that first result.
Separate conversations retain separate order. The one retry timer drains existing
work and does not create Tickets or other application work.

A CLI write can outlast its connection. The client keeps its original request ID as
the submission ID after any possible send, including partial-send failure, timeout,
EOF, or an invalid reply. Acceptance stays unconfirmed. The CLI exits 3 instead of
reporting a connection refusal. A failure before send keeps its ordinary error.
Reads still require the VPS and never use the journal as a fallback view.

Preparation and receipt observation share one 30-second response deadline. The
client carries that local monotonic deadline over IPC and allows five seconds for
response delivery. Each socket operation uses the remaining transport budget.
A larger margin does not restart the response budget after preparation. Admitted
publication retains its source ownership until physical snapshot and journal work
settle, even after a response deadline or caller disconnect.

Use `panels executor retry SUBMISSION_ID` to inspect the original submission.
Add `--json` for structured output. This command requires the original child's
capability. It only observes existing work. It never captures artifacts, creates a
replacement write, or requeues an acknowledged entry. The ordinary replay loop owns
transport retries. Do not repeat the original write after an uncertain result.

| Output | Evidence | Exit |
| --- | --- | --- |
| `preparing` | The source task still owns preparation or publication. | 3 |
| `queued` | The original envelope is durable and pending. | 3 |
| `acknowledged` | A VPS receipt arrived, but its disposition and result are unavailable locally. | 3 |
| `unknown` | Response delivery failed, or no tracked task or retained journal entry proves the state. | 3 |

Unconfirmed output carries the original submission identity. Structured uncertainty
also carries the IPC request identity. The existing initial queued output retains
its submission ID, disposition, and acceptance fields. A retry has a separate IPC
request ID and retains its target submission ID. An exact observed receipt returns
the original result or refusal. An acknowledged entry alone does not prove success.

After an executor restart, an old capability can receive a canonical refusal. A
missing entry does not prove that the write never reached the executor or VPS.
Preserve the reported identity and the existing spool. Use the ordinary operator
inspection and reconciliation procedure before any deliberate replacement write.
Do not delete the journal or change stored outcomes to force another attempt.
Local capability and admission refusals use the canonical code, message, and detail
error envelope in both human and JSON CLI output.

Only registered operations reach canonical writers. Worker authority comes from
the VPS child assignment, not headers supplied by a Mac process. Unsupported
commands fail explicitly. This boundary does not grant a Worker owner authority.

An accepted message uses the same memory-only held line as a VPS-sent message.
The [conversation system](conversation-system.md#processes-honestly) owns its queue
policy and restart outcomes. The native operation retains submission identity,
original sender and recipient, and delivery outcome, without saving held content.
The first receipt remains immutable for deduplication; the operation result records
later delivery, discard, or restart interruption. Neither restart nor submission
replay re-sends lost held content.

The outbound journal uses SQLite for envelopes, conversation positions, snapshots,
and acknowledgment state. A transaction commits a submission and its snapshot references
together. Indexed queries select each conversation's first pending submission, count
waiting work, and check whether a conversation has waiting work without decoding
the entire queue. The transport scheduler owns in-flight admission; unrelated
conversations can progress while one waits.

SQLite uses a rollback journal (`journal_mode=DELETE`), `synchronous=EXTRA`, and
`fullfsync=ON`. On macOS, regular-file persistence also requests `F_FULLFSYNC`
after `fsync`; directory entries are synced after publication or removal. This
requires a local filesystem and storage that honor these flush requests. Linux
tests prove ordering and crash recovery, not physical Mac power-loss behavior.

Code paths: `src/planner/executor/journal.py`, `store.py`, `mutations.py`,
`ipc_client.py`, `src/planner/cli/http.py`.

SQLite work for remote acceptance runs on worker threads. Each operation opens,
commits or rolls back, and closes its own connection on that thread. A busy
SQLite writer can delay acceptance without stopping the VPS event loop or
control messages. Writes for the same conversation remain ordered, and cancelling
an acceptance keeps its lock until the database operation has settled. A domain
change and its first receipt still commit together after checking the connection
generation. Backend events use the conversation write worker for that transaction.

## Files precede their references

Workers use ordinary file tools in a resolved Ticket directory. Publication
captures immutable bytes. Transport consumes a `FileSnapshot` containing its
manifest and local path. Snapshot bytes remain immutable until an authoritative
receipt. Equal retained bytes share hard links between immutable spool entries.
At completed tool, reply, turn, and CLI boundaries, capture compares content hashes
with the latest retained manifest for the exact child and conversation. Unchanged
pending files reuse the original publication and retained snapshots. Unchanged
acknowledged files need no new capture or publication exchange. A return to earlier
content after a change creates a fresh publication identity.

Pins keep pending dependencies alive through capture, enqueue, transfer, and receipt
cleanup. Unsupported hard links use verified copies. A lost receipt retries the
original identity. Retained envelope JSON reconstructs comparison state after restart.
No watcher, timer, schema change, or Ticket-wide delivery gate is involved.
The receiver still checks the assigned child, owner, path, length, and digest.
New bytes receive publication acceptance before their dependent envelope.
Ticket URLs remain mutable. The existing per-conversation replay and per-path
reservations remain the ordering contract. A replacement child starts fresh comparison.

Both directions use HTTP through the app service. The Mac uploads artifact snapshots with PUT and downloads input snapshots with GET.
Input GET requests specify `Accept-Encoding: identity`, so the server sends the original snapshot bytes without compression.
The downloader disables automatic decompression and refuses any response with a nonempty `Content-Encoding`.
The control connection allocates generation-owned transfer IDs with a direction and manifest. The server resolves each ID to its own staging path.
These reservations track integrity and lifecycle. They do not restrict the account's ordinary filesystem access.

Four transfers can proceed concurrently. HTTP chunks retain the prior transport bound of 64 KiB. Local file copies retain their separate 256 KiB bound.
The receiver checks the manifest length and digest. Short, excess, or incomplete content cannot receive publication acceptance.
HTTP success proves byte transport only. The existing file completion and acceptance exchange still gates publication and native command dispatch.

The VPS uses a private directory for each connection generation. Upload slots admit one writer; download slots contain immutable snapshots.
The receiver makes a fresh verified copy before atomic publication and rechecks the generation, original child, manifest, and reservation.
Input downloads also pass through receiver quarantine and verification before the dependent native command.
Lost completion retains the original publication or command identity for reconciliation.

Input downloads use a separate progress contract. Ordinary control requests retain their 30-second deadline.
A download fails after 30 seconds without positive byte progress. Final verification and installation have a fixed 120-second deadline after download EOF.
Repeated progress or health frames cannot extend finalization. Inputs can exceed the ordinary control deadline while bytes continue to advance.

Cancellation fences command dispatch immediately. The VPS waits at most five seconds for the Mac to acknowledge transfer revocation.
A missing acknowledgment leaves remote revocation unconfirmed. Cancellation cannot undo native effects after a command crosses the wire.
Transfer failure leaves unrelated conversations connected. Genuine control connection failures retain their normal recovery path.

Generation loss closes admission before cleanup waits. Filesystem work can outlive logical failure or cancellation.
The transfer retains its files, descriptors, and capacity until that work settles. Cleanup never deletes a resource that admitted work still uses.
Ordinary account access stays intact. Accepted bytes remain protected by receiver verification and atomic publication.

Snapshots and hashes use bounded streams. One submission per conversation can
proceed at a time. Separate conversations can progress while a large transfer
waits. Snapshot pins retain bytes through receipt and release them after transfer.
The outbox holds no transaction across network I/O.

Files and database rows have different commit boundaries. Complete orphan bytes
after a crash can finish their original publication. Partial bytes stay outside
served paths. Input attachments travel in the opposite direction before the
backend operation that needs them.

The Mac resolves a checkout below the VPS account home relative to its own account
home. The directory must exist. Workers receive local artifact paths and executor
capabilities after host-specific role values are replaced.

Code paths: `src/planner/executor/files.py`, `publication.py`, `vps.py`.

## A lost connection supplies no answer

The Mac service owns its children independently of the socket. On reconnect,
Panels establishes the exact surviving children, turns, and callbacks before it
replays queued events. It also reconciles accepted submissions and recorded
answers. A VPS restart restores the authoritative turn information
needed to interpret those events.

Each replay pass drains only the pending sequence prefix captured at its start,
with one in-flight submission per conversation. New events stay durable for a
later pass. Both passes before readiness use their own prefix, so new child
output cannot extend either pass indefinitely. Each captured prefix still needs
its ordinary delivery and receipts; there is no wall-clock readiness bound. After readiness,
ordinary replay passes continue delivering the retained work in conversation order.

Each Mac control request has one 30-second deadline covering send admission,
transmission, and its response. A timeout closes that request's connection and
leaves its operation unconfirmed; it does not mean refusal. Unexpected VPS request
failures also close their original connection. Ordinary reconnect and replay
recover durable receipts using the original submission identity and content hash.
Neither failure retries a native operation in place or invents its outcome.

An accepted answer and callback application are different durable facts. Panels
saves the permission choice or complete question answer map before transmission.
Reconciliation can give an old answer to its original surviving callback.
An unanswered callback keeps its wait.
A missing child does not receive a replacement answer, a refusal, or a made-up
turn ending.

After reconnect, the VPS acknowledges accepted retained command outcomes through
the existing command acknowledgment path. Later registry inventories omit those
outcomes. Journal receipt cleanup and registry confirmation remain separate.
Neither acknowledgment repeats the native operation. An outcome from an older
executor incarnation must match the exact retained command, its first result, and
an accepted canonical envelope receipt. A refused native operation can still have
an accepted evidence receipt. Confirmation removes registry evidence; it grants
no child, callback, or waiting caller authority and does not close assignments.
The retained command metadata covers only the current turn. Missing metadata or
a missing matching receipt keeps the evidence unconfirmed and its uncertainty
intact. Journal cleanup remains separate; ordinary replay follows successful
inventory reconciliation.

Conversation shutdown fences its initial command before it waits for input cleanup or a native result.
Immediate dispatch and reconnect replay check the same fence, including after awaited input preparation.
The endpoint distinguishes a child start from prompt transmission. A successful start does not prove prompt acceptance.
Only complete evidence of no prompt transmission permits suppression. A possible partial write retains uncertainty.
Cancellation preserves the original native outcome and command identity. It does not create a synthetic native result.

An ordinary stop uses its own recorded command identity against the original child reference.
The Mac fences that exact child before cleanup and waits for admitted native work and its final publications.
The original stop outcome needs an accepted canonical journal receipt before the VPS confirms retirement.
Direct outcome frames and reconnect inventory cannot replace that receipt.
A refused required native publication keeps cleanup uncertain for that exact child across retries and restarts.
Ordinary domain refusals do not affect cleanup evidence.

Confirmed retirement closes the exact assignment and preserves its retirement fact in one transaction.
Later metadata and delayed inventory cannot revive that identity.
Live idle and active children still qualify for adoption through their open assignments.
The next explicit prompt starts a fresh child with the retained provider session cursor.
An explicit new stop can confirm an absent exact child only after all earlier work and journal receipts settle.
An old uncertain command always retains its first outcome. A failed cleanup keeps quarantine and blocks replacement.
Claude cleanup retains the exact SDK client until disconnect succeeds.
The adapter also retains the SDK process object and requires its exit before transport cleanup succeeds.
An exception or cancellation preserves uncertainty and the cleanup identity, even if the SDK clears its own process field.
An explicit later stop checks the same retained process. It cannot infer exit from an empty SDK field.
These rules govern ordinary stop and executor quiescence through the same adapter contract.
Late outcomes require exact retained command evidence for the original child and incarnation.
That historical evidence grants no renewed execution, callback, event, or CLI authority.
If the original command evidence is absent, delivery remains uncertain.
The [retained initial-prompt procedure](initial-mac-prompt-cancellation.md) replaces only the isolated verification app.

The VPS exposes per-ask evidence: uncertain, reachable, child gone, or callback
gone, alongside the stored answer and its application. A restored link alone
cannot prove delivery. During an outage, the owner can save an answer for the exact
original request while its persisted child and turn remain open and pending.
Reachability may be uncertain. The VPS validates the choice or complete answer map
and saves it transactionally; the first answer wins. An identical retry returns
the original receipt, including after closure. A conflicting retry fails.

The browser submits the exact child, turn, request kind, and request ID from its
pending snapshot as `executor_ask`. For remote submissions, `{landed: true}` means
durable acceptance on VPS. The UI says "Answer saved on VPS; delivery to Mac is
pending." Only matching application evidence says "Answer applied on Mac."
Delivery waits for fresh evidence of the original callback on a ready connection.
A restart restores the pending identity before the Mac reconnects. Cancellation,
a completed turn, or a replaced child/turn prevents new acceptance and delivery.
Cancellation persists that closure before waiting for the Mac. Saved answers to
closed callbacks remain unresolved; reconnect cannot reopen them. An application
acknowledgment for an answer transmitted before closure can still record its effect.

Panels records callback application separately from tool execution. The clock
exchange gives bounds for cross-host timing. Exact action timing requires
correlated backend evidence. It stays unknown when that evidence is absent;
callback acceptance is not proof that a tool ran.

Code paths: `src/planner/executor/runtime.py`, `store.py`, `logic/reconciliation.py`,
`src/planner/conversation/system.py`, `api.py`, `storage.py`.

## Replacement keeps code apart from data

The operator owns installation and service control. Replaceable source and the
Python environment stay separate from persistent executor data. The lightweight executor entry
path does not start a Mac Panels server or create a Panels database. A protocol
or journal-format mismatch refuses work before replay.

Read-only journal inspection establishes compatibility, not authority to replace
a process. Inspection errors refuse preflight. An update or rollback still needs
fresh identity-bound quiescence. Active work, callbacks, uncertain operations,
unfinished native calls, or unaccepted entries prevent readiness. Quiescence
closes admission for new commands and CLI writes first. It accounts for source
writes already admitted, including publication that continues after caller
cancellation. Once native work and callbacks are idle, it stops idle producers
before it waits for the journal to drain. Final native events remain admitted
and every unaccepted entry still blocks exit. Quiescence rechecks the process
identity after awaited shutdown work.

Commands for another executor incarnation receive a definitive refusal before
native execution. After admission closes, new starts and prompts also receive
refusals before native execution. A prompt waits for its child's current operation,
then checks admission again. These outcomes retain their ordinary journal entries,
receipts, and duplicate protection. Unaccepted entries still prevent exit.

A missing child or an unknown native effect remains uncertain. Confirmation of
an outcome and receipt of its journal entry do not resolve that uncertainty.
For maintenance exit, an acknowledged uncertain outcome stops blocking only after
a successful stop command for its exact executor incarnation, child, and conversation.
That exact child must also be absent from the live registry. A failed stop, a missing
child alone, or a stop for another reference does not qualify. All other work and
journal entries must still settle before exit.

Error text alone cannot prove that dispatch never occurred. Historical uncertain
outcomes retain their original bytes and unknown effects. Duplicate commands retain
their first result without another native call. An executor restart preserves the
journal and its receipts but creates a fresh identity without old child authority.

A committed controlled exit returns zero. The LaunchAgent keeps successful intentional exits stopped. The operator must
check process evidence before service removal. An old receipt cannot authorize
action against a replacement process. Missing or changed process evidence refuses cutover.
An incompatible rollback preserves the journal and refuses mutation.

Code paths: `src/planner/executor/entrypoint.py`, `cli.py`, `preflight.py`,
`configuration.py`, `mac.py`.

## Installation compatibility

The operator compares candidate protocol and journal constants with fresh installed
Mac evidence and constants from the intended VPS package before installation changes.
The VPS health API reports the Mac identity. It supplies no independent VPS version evidence.
Missing or incompatible evidence defers the operation. The handshake remains the final protocol guard.

Read-only spool inspection establishes compatibility without migration or recovery.
The [Mac operator runbook](mac-executor-installation.md) supplies the concrete checks.
No automatic two-host update or general migration mechanism exists.

## Fresh installation and retained journals

The installation path uses the current journal and outbox schema. Read-only
preflight refuses unsupported or corrupt journals without mutation. There is no
reader or converter for earlier draft formats.

## Browser previews

VPS application previews use `/dev/tickets/<ticket-id>/<port>/` and connect to VPS
loopback. The server reads the Ticket's current locale before each request.
For a Mac Ticket, the link shows "Previews are unavailable for Mac runs." with a
503 response. It does not connect to the Mac or fall back to a VPS application on
the same port, whether or not the Mac executor is connected.

The VPS proxy preserves the requested port in `Host` and preview-local redirects.
It strips Panels credentials and hop-by-hop headers before the application receives
a request. Hosted access passes through the existing trusted ingress. HTTP connect
and idle deadlines live in `src/planner/previews/configuration.py`.

## App service prerequisites and boundaries

The native launcher requires the application revision, an HTTPS app origin, and a logical executor ID.
The Mac uses one WebSocket for commands and receipts, and HTTP for files.
No open browser, SSH login, relay process, or SFTP subsystem participates.

```sh
panels executor run \
  --application-revision "$CANDIDATE_SHA" \
  --panels-server-url "$PANELS_SERVER_URL" \
  --executor-id "$EXECUTOR_ID"
```

The origin has no credentials, path, query, or fragment. The client checks the server certificate and hostname.
It does not follow redirects or use ambient proxies, cookies, or a separate application login.

Tailscale Serve terminates HTTPS and replaces supplied identity headers with its authenticated user's identity.
The existing allowed login authenticates native admission. Every native route also requires the configured logical executor ID.
Missing, duplicate, or disallowed identity fails admission. Tagged devices supply no user login and cannot enter this boundary.
The native routes reject browser Origin headers. Ordinary app access retains its existing behavior.
The backend listens on loopback, within the existing trusted-host boundary.

The executor ID selects routing. It is not a password or hardware attestation.
Any permitted untagged device under the allowed owner login can assert it. No new credential, device enrollment, or policy change exists.
Auxiliary routes also check the current incarnation, generation, and reserved transfer or stream ID.
Domain writes still require the original assigned child's authority. Native authentication grants no Worker owner authority.

Mac and VPS packages must use the current protocol before replay.
The Mac retains its native children during app restarts and reconnects through inventory, reconciliation, replay, and readiness.
There is no fallback transport. Lost replies leave native effects unconfirmed rather than repeating them.

Connection diagnostics omit credentials, addresses, paths, exception messages, and remote payloads.
Active transfers prevent quiescence. The Manager coordinates compatible peers through the existing identity-bound application procedure.
A coding candidate grants no permission for a live route or peer change.

The private incident observer is outside normal product runtime.
Its [source and recovery references](/files/tickets/t_1ymbjrdm/artifacts/diagnostic-source-archive.json)
remain available with the historical evidence. Old private capture configuration
does not activate the observer in this build.

A timeout or lost response does not establish refusal. Durable acceptance remains
separate from response delivery. An EOF alone does not establish a physical cause.

Conversation request identities and common turn outcomes use shared typed rules.
Transport callbacks and receipt transactions keep their own execution boundaries.
Remote conversation persistence uses the existing conversation write worker.
Connections open and close in that worker. Ordered loop finalization follows the
committed result. Cancellation retains the operation through settlement.
Reconciliation recovers the same typed result for acceptance, replay, or refusal.

## Evidence boundaries

Scripted process tests cover link loss, replay, VPS restart, permission callbacks,
file ordering, and identity-fenced exit. They do not establish installed-account
credentials, unattended reboot or pre-unlock operation, or real provider action
timing. Those remain explicit requirements before a live success claim.

Installation and updates use the [Mac operator runbook](mac-executor-installation.md).
See [runtime environments](environments.md) and [VPS deployment](deployment.md) for their separate operating boundaries.

_Updated for the candidate transport. Live application remains separate._

## Machine display reads

`GET /api/executor/health` includes `last_contact_at_ms` and
`last_contact_source: "vps_received_valid_executor_frame"`. This time is the VPS
receipt time of a strictly decoded frame on the current established executor
session. A validated initial handshake supplies the first observation. Later
frames advance it before domain dispatch, including operations that receive a
refusal. An old writer or generation cannot advance it. `observed_at_ms` keeps
its original meaning: the handshake observation time.

Contact proves receipt only. It proves neither current activity nor answer
delivery. After disconnect, the last contact remains historical. Before the first
observation, or after a VPS restart, it is null. A failed health read leaves the
current contact unknown. Contact and generation do not decide whether the VPS can save an answer.
There is no timer-based cutoff that turns historical receipt into current activity.

Conversation reads include nullable `executor_context`, with the actual remote
child reference and current turn token. Pending ask controls require the exact
child, turn, request kind, request ID, and pending snapshot membership.
`answer_available` means the VPS can accept the answer, even while disconnected. One callback's
evidence cannot authorize another callback, including a reused ID on an old child.
