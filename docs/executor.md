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

The wire protocol is version 8. Earlier peers refuse work before replay. The
journal format is version 2. Earlier journal formats refuse ordinary startup
and deployment before mutation. Wire compatibility and journal compatibility are
separate checks. Neither compatibility check converts stored work.

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
Protocol 8 requires this bootstrap on both peers. Journal format 2 stays unchanged.

Code paths: `src/planner/executor/skills.py`, `src/planner/executor/remote.py`,
`src/planner/executor/runtime.py`, `src/planner/executor/vps.py`.

## The journal is transport, not a second Panels

The Mac saves its authored work before transmission. Each submission keeps its
identity and position within its conversation. The VPS commits the write and its
first receipt together. If a receipt disappears, replay returns that first result.
Separate conversations retain separate order. The one retry timer drains existing
work and does not create Tickets or other application work.

A CLI write can outlast its connection. Its queued result means acceptance is
unconfirmed, including when the VPS already committed but the receipt was lost.
It never supplies a fabricated domain result. Application reads still require the
VPS. The journal is not a fallback view of a Ticket.

Only registered operations reach canonical writers. Worker authority comes from
the VPS child assignment, not headers supplied by a Mac process. Unsupported
commands fail explicitly. This boundary does not grant a Worker owner authority.

An accepted message can wait in the target conversation's held line. Its exact
content, original target, order, and first receipt share one VPS transaction.
Restart restores existing pending content before later admission. Promotion and
discard consume that state through the conversation lifecycle. Interrupted native
dispatch retains its bytes and exposes uncertainty instead of sending again.
This persistence cannot recover messages lost before the feature existed.

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

## Files precede their references

Workers use ordinary file tools in a resolved Ticket directory. Publication
captures immutable bytes. Transport consumes a `FileSnapshot` containing its
manifest and local path. Snapshot bytes remain immutable until an authoritative
receipt. A transfer pin retains them through receipt cleanup, completion, and
cancellation; no database transaction spans network I/O. The receiver checks the assigned child, owner, path,
length, and digest, then publishes a complete file from quarantine. A dependent
row cannot commit first. A replay of an old publication cannot overwrite a newer
file at the same URL.

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

Generation loss closes admission before cleanup waits. Cancellation settles admitted filesystem work, including receiver copies in threads, before slot release or directory removal.
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

## Explicit locale moves

The VPS serializes a move with Ticket sends, resets, and Worker admission. A Mac
source supplies fresh evidence for all VPS-assigned source children, including a
start that has not reached the Mac. The executor refuses active turns, callbacks,
unsent journal work, uncertain operations, and unfinished publication. Historical
handshake inventory alone cannot authorize a move.

After idle admission, the executor fences the old child identities against future
commands and IPC writes, then retires those children. A receipt names the exact
incarnation and connection generation. The VPS checks that identity again through
the canonical write. An absent conversation still requires current source proof.
New destination children receive fresh identities, including a later return to Mac.
Failure after retirement begins reports retired or uncertain source state separately
from an admission refusal.

## Installation compatibility

The operator compares candidate protocol and journal constants with fresh installed
Mac evidence and constants from the intended VPS package before installation changes.
The VPS health API reports the Mac identity. It supplies no independent VPS version evidence.
Missing or incompatible evidence defers the operation. The handshake remains the final protocol guard.

Read-only spool inspection establishes compatibility without migration or recovery.
The [Mac operator runbook](mac-executor-installation.md) supplies the concrete checks.
No automatic two-host update or general migration mechanism exists.

## Fresh installation and offline legacy conversion

The production installation path is a fresh format-2 journal. No previous executor
installation on the target Mac is required. The Mac runbook requires compatible
protocol and journal evidence before installation changes, then read-only spool inspection before startup.
The handshake refuses incompatible protocols before work. The VPS workflow performs no Mac compatibility check.

An existing format-1 journal has one supported conversion boundary: stop every
process that can open it, disable automatic restarts, and run the explicit offline
converter under its exclusive journal lock. Preserve an untouched backup of the
stopped source before conversion, then run:

```sh
panels executor convert-spool --root /absolute/path/to/executor-root
```

The root is the persistent executor root, not its `spool` subdirectory. The command
requires the executor to be stopped and acquires `spool/lock` exclusively. It is
not part of ordinary installation, update, rollback, or startup. The converter
validates the old envelopes, sequences, submission identities including acknowledged
work, and immutable snapshot bytes before publishing format 2.
Its local publication fence preserves the old journal when conversion is interrupted;
a failed conversion never authorizes ordinary startup against partially converted
state. Inspect the published format-2 journal before starting a compatible executor.
Keep the stopped source backup for diagnosis, separate from the active journal.

Automatic or in-place upgrades across both hosts are unsupported. Rollback across
conversion is also unsupported: an old executable must not open the converted
journal. These restrictions do not prevent the fresh format-2 installation. The
offline converter supplies no two-host cutover orchestration or durable deployment
phase machinery.

## Browser previews

A preview uses `/dev/tickets/<ticket-id>/<port>/` on either locale. The server reads
that Ticket's current locale before each request. VPS previews use VPS loopback.
Mac previews use a temporary VPS loopback listener. Its byte streams cross the
app service through separate Mac-initiated WebSockets and reach only `127.0.0.1` on the Mac.
The URL carries no host name and accepts no host parameter.

Concurrent requests for one Ticket and port share a leased listener. The final
lease closes it. Link loss, replacement, cancellation, and shutdown close its
streams. A new request can acquire a new listener after the connection is ready.
If a stream fails, concurrent requests on the same leased listener can also return
unavailable. The final request releases that listener.
Preview traffic stays outside the journal, replay, and conversation commands.
A failed request never moves to a VPS service at the same requested port.

The remote open and local connect deadlines are one second. Idle streams expire after 30 seconds.
Each peer admits at most 32 streams. The VPS admits at most 32 listeners. Pending stream connections count against capacity before tasks start.
Server-issued lease and stream IDs bind each connection to the current executor generation and fixed Mac loopback port.
Each binary message carries at most 64 KiB. Awaited sends and bounded receive buffers supply backpressure.
Separate connections keep stalled preview bytes away from control receipts. The final lease closes pending and active streams.
These limits live in `src/planner/previews/configuration.py`.

The proxy preserves the requested port in `Host` and preview-local redirects.
It strips Panels credentials and hop-by-hop headers before the preview receives a
request. Hosted access still passes through the existing trusted ingress. Lease,
remote connection, and stream failures return the existing unavailable response.
Earlier peers refuse the protocol 8 handshake before replay.

Verification uses isolated Linux subprocesses as synthetic VPS and Mac peers, plus
a real browser. That proves the software boundary and does not prove physical Mac
access, installed routes, provider credentials, or deployed behavior.

## App service prerequisites and boundaries

The native launcher requires the application revision, an HTTPS app origin, and a logical executor ID.
The Mac uses one WebSocket for commands and receipts, HTTP for files, and separate WebSockets for preview streams.
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

Protocol 8 requires matching Mac and VPS packages before replay. Journal format 2 stays unchanged.
The Mac retains its native children during app restarts and reconnects through inventory, reconciliation, replay, and readiness.
There is no fallback transport. Lost replies leave native effects unconfirmed rather than repeating them.

Connection diagnostics omit credentials, addresses, paths, exception messages, and remote payloads.
Active transfers prevent quiescence. The Manager coordinates compatible peers through the existing identity-bound application procedure.
A coding candidate grants no permission for a live route or peer change.

The private VPS capture remains disabled without its retained-case configuration.
Its local observation ID differs from the wire request ID. Valid UUIDs normalize
to one representation for correlation. Wire kinds and exception classes use fixed
allowlists. The capture excludes payloads, exception messages, and partial read bytes.

A VPS request timeout records whether its deadline expired or an inner call raised
TimeoutError. The record retains the original writer's generation after replacement
and session cleanup. A local close record describes an attempt at the existing close
boundary. It does not prove that the close succeeded. Each generation retains separate
tokens for its first observed failure and its first local close attempt. Wrapper
reports preserve the original failure token and phase.

An EOF records observed transport termination. A preceding local close token links
that termination to local evidence. Without that token, its physical cause stays
unknown. A timeout or lost response never establishes operation refusal. Durable
acceptance remains separate from response delivery.

Capture limits remain 256 records, 256 KiB, 2048 bytes per record, and 180 seconds.
Receipt settlement retains its 15-second limit. Private metadata has explicit bounds
and clears at capture termination. Saturation produces honest truncation. Existing
finalization can precede a later timeout callback. Missing later evidence does not
prove that no failure occurred. Observer failure never controls executor behavior.

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
