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
VPS: canonical writers + first receipts + complete files + conversation answers
                  |
       reconnect inventory and process-held answers
                  |
Mac: the original live callbacks, if they still exist
```

Code paths: `src/planner/executor/`, `src/planner/runtime/`,
`src/planner/tickets/locale_configuration.py`.

The wire protocol is version 16. The SQLite outbox format is version 5.
The handshake checks both versions; the Mac also checks its local SQLite format on open.
Unsupported versions refuse before work or mutation; no conversion runs.
The application revision is a nonempty diagnostic label, not a compatibility version.

Migration `mac_vps_execution` installs the final schema directly after staging's
`video_upload_ledger`. Any database that ran this branch's earlier migrations must
be rebuilt. Existing staging Tickets, conversations, native usage,
and video reservations retain their data.

## Modules and their rules

Each module owns one decision. Conversation knows the backend contract and has
no executor imports. Mac and VPS implementations never import each other; their
messages meet through the shared protocol and Link. Only the server composition
may know both sides. A unit test keeps these import boundaries in place.

| Module | Responsibility and one rule |
| --- | --- |
| Conversation | Own the transcript, turns, questions and reply credit. Backend delivery facts enter through its contract. |
| Link | Carry messages and verified file bytes for one connection generation. Never decide what a command or queued write means. |
| Mac runner | Prepare inputs and skills, run providers and prove stops. One in-memory command record prevents a second provider call for the same command. |
| Mac outbox | Keep authored work and captured file bytes until confirmed. Never interpret a Panels write. |
| Agent gateway | Authenticate the local agent's CLI, relay reads and queue writes. Unconfirmed writes keep their original submission identity. |
| Mac service | Compose the Mac modules and keep the Link dialled. Run and queue decisions belong to its children. |
| VPS runs | Retain run identity, command fate, reconnect and stop proof. Never infer termination from a missing inventory entry. |
| VPS inbox | Apply each queued item once, in conversation order, with its receipt. A permanent refusal advances the queue. |
| VPS transport | Admit connections and route messages and file slots. Run control and inbox saving do not own sockets. |
| Catalog relay | Carry machine model lists and keep the Mac's last answer on the VPS. Never launch a Worker. |

Each machine reads its own models through one backend snapshot service. The Mac
answers `catalog_request` with the same snapshot the VPS uses locally: installed
version, sign-in state, models, efforts, defaults and any diagnosis. All three
backends use this path, including Hermes. An empty Hermes list is a valid answer.
The read sends no prompt and has no Worker or journal authority.

The VPS keeps one snapshot per backend for its configured Mac in
`machine_backend_snapshots`. This copy survives either process restarting and stays
readable while the Mac is offline. A new Mac process does not lose the machine's
list just because its process identity changed. The upgrade also removes the unused
model-resolution tables from staging.

On reconnect the VPS asks for fresh snapshots. A picker read serves the kept copy
while the Mac checks whether Claude or Codex changed version, or whether its last
read is twelve hours old. Refresh waits for a new read. Hermes uses the same triggers
except the version check. These checks happen on demand; no timer runs provider
reads. A failed or slow read leaves the last copy available and does not close the
shared execution link. A partial answer keeps the previous complete model list and
its defaults. Unchanged answers do not write another copy. The link request is
bounded to 35 seconds.

Lists belong to a machine, not a project folder. Project settings still apply when
the agent runs; they do not change the machine's picker list. The Ticket model-list
route takes only the Ticket ID, authorizes access, and selects that Ticket's machine.

The remote child implements the same backend contract as local children. It holds
saved answer commands in memory and checks admission before emission. Detaching
releases the VPS handle and leaves its Mac agent running; stopping requires the
final ordered stop receipt. Local children stop when detached.

The inbox joins the conversation's write transaction to record replay position and
receipt. The append is shared with local conversation rows and keeps activity,
compaction, attention, failure wakes and turn/request state in that transaction.
Only a newly accepted effect applies its live conversation change; replay and refusal
keep their receipt without applying it again. A stale event is accepted with
`applied: false` and changes no conversation rows or live output. Agent writes use canonical writers. Before sending an
agent-authored message, the inbox records `dispatching`; a repeat without a known
outcome returns uncertainty and sends nothing. There is no separate message ledger.

Code paths: `src/planner/conversation/`, `src/planner/executor/link.py`, `link_files.py`,
`protocol.py`, `mac_runner.py`, `mac_outbox.py`, `mac_agent_gateway.py`, `mac_service.py`,
`vps_runs.py`, `vps_remote_child.py`, `vps_inbox.py`, `vps_agent_writes.py`,
`vps_transport.py`, `app_transport.py`, `catalog_relay.py`, `src/planner/core/server.py`.

## How a Mac run starts

The VPS readiness loop claims a ready Ticket and starts or reuses its conversation.
The selected destination supplies the Mac backend factory; Chief and Manager
conversations keep local children. The VPS saves the exact Mac child assignment
and run before emitting its start command, and requires a reconciled, ready link.
The Mac runner downloads and verifies the command's inputs and requires a Ticket
identity for every start. It installs the current canonical skills, then localizes
the workspace and builds the native environment once before starting the selected provider.
Provider locations are resolved afresh for each start.
Before native invocation, it checks that the retained child can take work.
The direct command result reports admission;
recorded agent output takes the ordered outbox path independently.

Code paths: `src/planner/runtime/worker_step_readiness_loop.py`,
`src/planner/executor/vps_runs.py`, `vps_remote_child.py`, `mac_runner.py`.

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
A short link outage keeps the existing child and does not repeat bootstrap.
A VPS restart ends the old conversation turn and requires an exact-child stop before a fresh start.
Later starts can update the account's shared skill paths.

The Mac runner adds the host instructions immediately before handing a prompt or
steer to the provider adapter. They are not VPS conversation context or sender text.
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

Code paths: `src/planner/executor/skills.py`, `src/planner/executor/vps_remote_child.py`,
`src/planner/executor/mac_runner.py`, `src/planner/executor/vps_runs.py`.

## The outbox keeps authored work until confirmation

The Mac saves its authored work before transmission. Each submission keeps its
identity and a rising sender sequence. The VPS commits the write and its receipt
together. It retains the latest sequence and receipt for each sender and conversation.
The Mac sends one item per conversation at a time, so a lost receipt returns the
stored result. An older sequence gets an acknowledgment without another effect.
Permanent assignment or payload failures get a refused receipt, so the next item
in that conversation can proceed. A lost connection, missing file bytes, or a busy
database leaves the item pending for retry.
Separate conversations retain separate order. Saving a backend event, callback
application, stop proof, or CLI write commits before waking the sender. Up to four
conversations send at once; one slow upload does not block the others.

Prompt and steer calls use the Mac's direct result. An event reported before the
prompt returns can finish admission without waiting for any queued output.
Stop and retirement require the final ordered stop receipt.
Direct stop replies and reconnect reports cannot finish a successful stop. If the
link drops before its queued `child_stopped` item arrives, the Mac sends that item
after reconnect. If the VPS commits its receipt but acceptance is cancelled before
settling the caller, replay of the stored `recorded: true` result settles it.

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
| `unknown` | Response delivery failed, or no tracked task or retained journal entry proves the state. | 3 |

Unconfirmed output carries the original submission identity. Structured uncertainty
also carries the IPC request identity. The existing initial queued output retains
its submission ID, disposition, and acceptance fields. A retry has a separate IPC
request ID and retains its target submission ID. An exact observed receipt returns
the original result or refusal. Confirmed agent writes retain that receipt only
while the run remains alive. Stopping the run or reopening the outbox removes
confirmed rows. An unknown result never proves non-delivery.

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
The [conversation system](conversations.md) owns its queue
policy and restart outcomes. The native operation retains submission identity,
original sender and recipient, and delivery outcome, without saving held content.
The current inbox receipt remains immutable; the operation result records
later delivery, discard, or restart interruption. Neither restart nor submission
replay re-sends lost held content.

The outbox has one SQLite database with a sender row and an ordered items table,
plus a folder of immutable file blobs. There are no entry folders, envelope copies,
metadata files, identity ledger, or per-conversation counters. Adding an existing
submission ID compares its content: identical content returns the original item;
changed content is refused. Events get a stored UUID; callbacks and stop proofs
keep their derived IDs. Indexed queries select each conversation's next item.
No database transaction spans network I/O.

SQLite uses WAL, `synchronous=FULL`, and `fullfsync=ON`. On macOS, regular-file
persistence also requests `F_FULLFSYNC` after `fsync`. Directory entries are synced
after publication or removal. Actual power-loss durability still requires a real
Mac and storage that honor these flush requests.

Every permanent refusal is a receipt. A refused queued Panels write also appears
as a failed Panels tool row in its source transcript. Telling the running agent
about that later refusal remains a follow-up; no database row is a prompt.
A Mac-authored message records `dispatching` before calling its recipient. If a
repeat finds that state without an outcome, it records `uncertain` and sends nothing.

Code paths: `src/planner/executor/mac_outbox.py`, `store.py`, `refusals.py`,
`vps_agent_writes.py`, `mutations.py`, `ipc_client.py`, `src/planner/cli/http.py`.

SQLite work for remote acceptance runs on worker threads. Each operation opens,
commits or rolls back, and closes its own connection on that thread. A busy
SQLite writer can delay acceptance without stopping the VPS event loop or
control messages. Writes for the same conversation remain ordered, and cancelling
an acceptance keeps its lock until the database operation has settled. A domain
change and its first receipt still commit together after checking the connection
generation. Backend events use the conversation write worker for that transaction.

Text deltas, thinking signals, and tool progress use one-way live frames. They go
straight to the conversation live tail, without an outbox item or a VPS receipt
row. A lost link drops them. Completed messages and all other recorded events
still use the durable outbox and replay after reconnect.

## Files precede their references

Workers use ordinary file tools in their Ticket directory. Capture runs after
agent steps and before each agent Panels write. It walks the directory with
`os.scandir` and compares size and modified time with the last captured index.
Matching files are never opened. Files modified within two seconds of a check
are hashed again at the next check, catching same-size changes within one clock
tick. Changed files are read once, hashed while copied, and flushed into immutable
content-addressed blobs. Captures of one Ticket take turns. The queued item, its
file references and the updated index commit together after the bytes are durable.
Deleted files are not sent. Entries that are not ordinary files, have unsafe
names, cannot be read, or change during the read are skipped with a warning.
They are tried again at the next capture. An unreadable directory is skipped
the same way. A file reaches the VPS only after a stable, readable capture;
one skipped entry does not prevent the worker's messages or Panels writes.
Failures to save captured bytes or queued output still block stop proof.
Tool details are limited to 65,536 characters before they are saved or sent.

The sender asks the VPS to accept the original item. The VPS replies with any
missing files; the Mac uploads those blobs with `put_file` and sends the same
item again. The VPS verifies ownership, path, length and hash, and keeps uploads
in a private staging directory. Acceptance renames files into their Ticket folder
before applying the effect. After a crash between rename and database commit,
matching target bytes allow the same item to finish without another upload.
Sequence and receipt checks prevent an older retry from replacing newer bytes.
A file linked by an accepted agent write is therefore durable on the VPS first,
without separate link parsing. Pending items retain their exact original bytes.
Uploads use the queued item's file references to find those bytes, without reading
the outbox database again.
If an upload descriptor is malformed or its upload is refused, the Mac replies
with an error for that request. The VPS records a refused receipt for the item.
The queue advances and the shared connection stays open for other conversations.

A file still waiting for confirmation stays attached to later items that refer
to the same unchanged file, so refusal of the first item cannot lose the bytes.
Confirmation releases blobs no waiting item references. Temporary and unreferenced
blobs are swept when the outbox opens. The size-and-time index does not retain
file bytes after confirmation. Incompatible outbox formats require explicit
operator disposition; they are never converted silently.

Generation loss closes admission before cleanup waits. Filesystem work can outlive logical failure or cancellation.
The transfer retains its files, descriptors, and capacity until that work settles. Cleanup never deletes a resource that admitted work still uses.
Ordinary account access stays intact. Accepted bytes remain protected by receiver verification and atomic publication.

Stored conversation input files are hashed in place, then copied once into the
transfer's private storage. The copy checks length and hash before it is offered
to the Mac. Both machines install received files through the same checked copy,
atomic rename and directory flush.

File manifests name the scope, owner, relative path, byte count and SHA-256 hash.
Transfers do not use a media type or report download progress.

Snapshots and hashes use bounded streams. One submission per conversation can
proceed at a time. Separate conversations can progress while a large transfer
waits. The outbox retains blobs until its item has a confirmed receipt.
The outbox holds no transaction across network I/O.

Files and database rows have different commit boundaries. Complete orphan bytes
after a crash can finish their original item. Partial bytes stay outside
served paths. For inputs the Mac runner pulls with `get_file` during its
`preparing` phase. Only files named in that exact pending command can be fetched.
The provider is called after verification and installation. Withdrawing a preparing
command proves it cannot call the provider; stop waits for its physical cleanup.
There are no input progress, completion or cancellation frames.

The Mac resolves a checkout below the VPS account home relative to its own account
home. The directory must exist. Workers receive local artifact paths and executor
capabilities after host-specific role values are replaced.

Code paths: `src/planner/executor/mac_outbox.py`, `files.py`, `vps_inbox.py`, `vps_agent_writes.py`.

## A lost connection supplies no answer

The Mac service owns its children independently of the socket. A short link loss
within the same VPS process keeps the current run. Reconnect checks the exact
children, turns, and callbacks before replaying queued events and saved answers.
Starts and prompts stay bound to their original Mac process. A new connection to
that process does not by itself refuse them. A resent prompt fetches its attached
files on the current connection before the provider receives it.

A VPS restart interrupts every old Mac run and closes its callbacks. It does not
reattach the turn. Child assignment and retained run identity commit together
before the first command can be emitted, so startup always knows which child to
stop. Reconnect inventory triggers an exact-child stop and records its outcome.
Until termination is proved, that conversation remains fenced against fresh work.
The next send then starts a fresh run. Unresolved delivery stays unknown and is
never resent. An absent child in inventory alone does not prove termination.

A new Mac service process interrupts the old incarnation's turns, closes their
assignments and callbacks, and clears their fences before the new connection is
accepted. The next send starts a fresh run. Unknown delivery stays unknown and is
never resent. This relies on launchd's default cleanup of a job's process group
when the job exits; the first real Mac/VPS run must verify that provider processes
actually die on service restart or reboot. The scripted tests do not prove that.

After readiness, the outbox continuously sends waiting items. Each receipt frees
one conversation to send its next item. New commits wake the sender immediately.

Each Mac control request has one 30-second deadline covering send admission,
transmission, and its response. A timeout closes that request's connection and
leaves its operation unconfirmed; it does not mean refusal. Unexpected VPS request
failures also close their original connection. Ordinary reconnect and replay
recover durable receipts using the original submission identity and content hash.
Neither failure retries a native operation in place or invents its outcome.

An accepted answer and callback application are different transcript facts. Panels
records the permission choice or complete question answer map before transmission.
The live Mac child proxy holds its delivery command in VPS memory. Reconnect can
give it only to its original surviving callback in the same VPS process.
An unanswered callback keeps its wait.
A missing child does not receive a replacement answer, a refusal, or a made-up
turn ending.

Each connection asks the Mac once for pending command IDs and its live children.
The Mac holds command state in memory for its process lifetime. An unknown ID can
be sent again with exactly the same ID and content. A preparing or invoking command
keeps its existing waiter. A finished command returns its first result. Its command
record also holds any required callback application or stop proof until publication
succeeds. Retrying that publication never repeats the provider call. Ordinary command
results use direct replies and this reconnect answer; they do not enter
the outbox or need result acknowledgments. No periodic inventory pass rewrites
question rows. A Mac restart ends its runs and leaves possible delivery uncertain.

Saved answers use their answer ID as their only command ID. Reconnect checks the
original child, turn and callback before sending one. Permission and question callbacks
share one inventory of exact callback references. Native callback application
remains a separate durable output fact. An answer is never redirected to a replacement.
Connection readiness precedes ordinary outbox draining; ongoing output cannot
hold the ready acknowledgment behind an ever-growing replay prefix.

Conversation shutdown fences its initial command before it waits for input cleanup or a native result.
Immediate dispatch and reconnect replay check the same fence, including after awaited input preparation.
The endpoint distinguishes a child start from prompt transmission. A successful start does not prove prompt acceptance.
Only complete evidence of no prompt transmission permits suppression. A possible partial write retains uncertainty.
Cancellation preserves the original native outcome and command identity. It does not create a synthetic native result.

An ordinary stop uses its own recorded command identity against the original child reference.
The Mac fences that exact child before cleanup and waits for admitted native work and its final publications.
The Mac puts a final `child_stopped` item after the exact child's admitted output.
Accepting that item resolves the stop waiter and proves that earlier output arrived.
A direct stop result or reconnect answer cannot replace that receipt.
A refused required native publication keeps cleanup uncertain for that exact child across retries and restarts.
Ordinary domain refusals do not affect cleanup evidence.

Confirmed retirement closes the exact assignment and preserves its retirement fact in one transaction.
Late command preparation and delayed inventory cannot revive that identity.
A surviving VPS process keeps its existing idle and active child proxies across link loss.
A restarted VPS uses saved run identities only to stop the old children.
The next explicit prompt starts a fresh child with the retained provider session cursor.
An explicit new stop waits for earlier admitted commands and source publications even
when the child is already absent. It queues its proof behind that output without
waiting for earlier receipts. The VPS confirms the stop only when it accepts the
ordered proof. Lost output keeps the stop uncertain.
An old uncertain command always retains its first outcome. A failed cleanup keeps quarantine and blocks replacement.
Claude cleanup retains the exact SDK client until disconnect succeeds.
The adapter also retains the SDK process object and requires its exit before transport cleanup succeeds.
An exception or cancellation preserves uncertainty and the cleanup identity, even if the SDK clears its own process field.
An explicit later stop checks the same retained process. It cannot infer exit from an empty SDK field.
These rules govern ordinary stop and executor quiescence through the same adapter contract.
Late stop proofs require the exact pending stop command for the original child and incarnation.
That historical evidence grants no renewed execution, callback, event, or CLI authority.
If the original command evidence is absent, delivery remains uncertain.
The [retained initial-prompt procedure](initial-mac-prompt-cancellation.md) replaces only the isolated verification app.

The owner can save an answer while the Mac is offline if the conversation still
has that request pending. The browser submits its public conversation request ID.
The conversation validates the choice or complete answer map and returns an
`AnswerFate`: `applied`, `saved`, or `refused`. Local callback success is `applied`;
a remote answer held by the live child proxy is `saved`. The UI says "Answer saved
on VPS; delivery to Mac is pending." A separate `answer_applied` transcript event
proves callback application and changes the display to "Answer applied on Mac."
A restored link alone cannot prove delivery.

Delivery waits for the exact original child, turn, request kind, and callback on
a ready connection. A replacement child never inherits the saved command. A VPS
restart loses the delivery command and closes the old run; the recorded choice
remains, with application unconfirmed, and is never replayed. Cancellation or a
completed turn also prevents new delivery. An acknowledgment of an answer sent
before closure can still record its effect, without reopening the closed request.
A missing acknowledgment remains uncertainty, even if the answer might have arrived.

Panels records callback application separately from tool execution. The clock
exchange gives bounds for cross-host timing. Exact action timing requires
correlated backend evidence. It stays unknown when that evidence is absent;
callback acceptance is not proof that a tool ran.

Code paths: `src/planner/executor/mac_runner.py`, `vps_remote_child.py`, `vps_runs.py`,
`src/planner/conversation/system.py`, `api.py`, `storage.py`.

## Replacement keeps code apart from data

The operator owns installation and service control. Replaceable source and the
Python environment stay separate from persistent executor data. The lightweight executor entry
path does not start a Mac Panels server or create a Panels database. A protocol
or journal-format mismatch refuses work before replay.

Opening an outbox checks its SQLite format version before changing it.
An incompatible outbox needs explicit operator disposition before this version can run. This check
does not grant authority to replace a process. An update or rollback still needs
fresh identity-bound quiescence. Active work, callbacks, uncertain operations,
unfinished native calls, or unaccepted entries prevent readiness. Quiescence
closes admission for new commands and CLI writes first. It accounts for source
writes already admitted, including publication that continues after caller
cancellation. Once native work and callbacks are idle, it stops idle producers
before it waits for the journal to drain. Final native events remain admitted
and every unaccepted entry still blocks exit. Quiescence rechecks the process
identity after awaited shutdown work.

Each command has one typed set of inputs: start, write prompt, steer, cancel turn,
answer permission, answer user input, or stop. JSON is decoded at the command boundary;
a command cannot carry another operation's inputs or unknown settings. Start owns
workspace localization, canonical skill installation, and native home setup.

Panels checks explicit model disables when it dispatches a prompt. Commands carry the
chosen model and effort directly; providers accept or refuse them. The Mac checks
that the child can still take work before invoking it. A stopped child takes no new
work. A prompt already past that check can reach the provider before the stop ends it.
A model disabled after dispatch can still run that turn. A cut input download refuses
the prompt before provider invocation; a separate send can try again after reconnect.
Omitted effort keeps the running value. An explicit clear requests the provider default.
Native message writes carry their receipt participation through cancellation and the
SQLite append, so another send cannot inherit it.

Commands for another executor incarnation receive a definitive refusal before
native execution. After admission closes, new starts and prompts also receive
refusals before native execution. A prompt waits for its child's current operation,
then checks admission again. These outcomes retain their in-memory result and duplicate protection. They do not
create journal entries. Unaccepted agent output still prevents exit.

A missing child or an unknown native effect remains uncertain. Receiving a result
does not resolve that uncertainty. For maintenance exit, an uncertain outcome stops blocking only after
a successful stop command for its exact executor incarnation, child, and conversation.
That exact child must also be absent from the live registry. A failed stop, a missing
child alone, or a stop for another reference does not qualify. All other work and
journal entries must still settle before exit.

Error text alone cannot prove that dispatch never occurred. Historical uncertain
outcomes retain their original result and unknown effects for the Mac process lifetime.
Duplicate commands retain their first result without another native call. An executor
restart preserves agent output and its receipts but creates a fresh identity without
old child or command authority.

A committed controlled exit returns zero. The LaunchAgent keeps successful intentional exits stopped. The operator must
check process evidence before service removal. An old receipt cannot authorize
action against a replacement process. Missing or changed process evidence refuses cutover.
An incompatible rollback preserves the journal and refuses mutation.

Code paths: `src/planner/executor/entrypoint.py`, `cli.py`, `mac_outbox.py`,
`configuration.py`, `mac_service.py`.

## Installation compatibility

The operator compares candidate protocol and journal constants with fresh installed
Mac evidence and constants from the intended VPS package before installation changes.
The VPS health API reports the Mac identity. It supplies no independent VPS version evidence.
Missing or incompatible evidence defers the operation. The handshake remains the final protocol guard.

The outbox checks SQLite `user_version` on open and refuses incompatible formats.
The [Mac operator runbook](mac-executor-installation.md) supplies the concrete checks.
`python -m planner.environments.mac_executor` prints the GUI LaunchAgent plist from
explicit inputs. It shares the executor connection's HTTPS origin validation and
performs no installation or service actions.
No automatic two-host update or general migration mechanism exists.

## Fresh installation and retained journals

The installation path uses outbox format 5. There is no reader or converter for
earlier draft formats. Preserve incompatible state for diagnosis; do not convert it
as part of installation.

## Browser previews

VPS application previews use staging's generation registry and the commands
`panels preview prepare`, `register`, `show`, and `stop`. The returned link is
`/dev/tickets/<ticket-id>/<generation>/`. The application serves that prefix itself;
Headroom does not rewrite its paths. See [Previews](previews.md) for registration,
trusted ingress, process identity, transport limits, and expiry.

All preview commands refuse for a Ticket set to run on the Mac. Existing generation
links also check the Ticket's current locale before connecting. They never forward
to the Mac or fall back to a VPS application. No Mac preview forwarding exists.

## App service prerequisites and boundaries

The native launcher requires the application revision, an HTTPS app origin, and a logical executor ID.
The Mac uses one WebSocket for commands and receipts, and HTTP for files.
Control messages are bounded JSON, one per binary WebSocket message. Only the local Unix-socket IPC adds a length prefix, because it has no message boundaries.
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
The Mac retains its native children during app restarts and short link outages,
and reconnects through one command-and-child ask, readiness, and outbox draining.
After a VPS restart, that ask causes every saved old run to be stopped before fresh work,
including a child absent from the Mac's inventory.
There is no fallback transport. Lost replies leave native effects unconfirmed rather than repeating them.

Both ends use the same Link in `src/planner/executor/link.py`. It matches replies,
serializes sends, bounds messages, and fails waiting requests
when that connection is lost. A request's deadline includes waiting to send. A durable
request timeout closes only its own connection; a catalog read can time out without
closing shared work. A closed Link cannot send or dispatch old frames through a
replacement connection. Item validation returns a refusal while leaving other work
connected. Unreadable messages and protocol mismatches close the connection.

File bytes use `link_files.py`, with bounded HTTP streaming and length and hash
checks. Reservations belong to one connection generation. The domain code still
checks which child may send or receive each file, and installs files before accepting
dependent work. `protocol.py` holds the shared message shapes and codecs; execution
identity types, turn tokens, and Ticket locales live in `execution_identity.py`,
which imports neither conversation nor Ticket code. The Link does not own children,
commands, the outbox, or database writes.

Mac and VPS executor timestamps use the same wall-clock millisecond helper in
`executor/clock.py`. Shared payload helpers in `protocol.py` encode objects and
read a queued item's child. They preserve the existing child lookup rules at each
boundary. `worker_identity.py` reads the Worker Ticket identity used by routing,
run assignment, and canonical skill loading. Inbox persistence reuses the store's
JSON encoding and validation errors.

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

## Machine display reads

`GET /api/executor/health` includes `last_contact_at_ms` and
`last_contact_source: "vps_received_valid_executor_frame"`. This time is the VPS
receipt time of a strictly decoded frame on the current established executor
session. A validated initial handshake supplies the first observation. Later
frames advance it before domain dispatch, including operations that receive a
refusal. An old writer or generation cannot advance it. The database keeps the
connection identity, generation and state; it stores no observation or loss time.

Contact proves receipt only. It proves neither current activity nor answer
delivery. After disconnect, the last contact remains historical. Before the first
observation, or after a VPS restart, it is null. A failed health read leaves the
current contact unknown. Contact and generation do not decide whether the VPS can save an answer.
There is no timer-based cutoff that turns historical receipt into current activity.

Conversation pending snapshots determine which public request IDs can be answered.
These controls remain available during a link outage while the request is current.
Saved and applied status comes from the selected conversation rows, with separate
application proof. Health and contact do not grant callback authority.

_Last verified: 2026-10-31._
