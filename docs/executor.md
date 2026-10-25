# The Mac executor

The executor runs a Ticket's backend child on the Mac while the VPS keeps the
Panels record. The Mac opens one connection to the VPS. Chief, supervisors, and
VPS Tickets keep their local children. A Mac Ticket starts only after the
connection and its reconciliation are ready. Codex has accepted Mac session
evidence; other Mac backends remain unavailable at creation and selection.

```text
Mac: ordinary Worker files + backend child
                  |
          one durable outbound journal
                  |
           Mac-initiated SSH connection
                  |
VPS: canonical writers + first receipts + complete files + accepted answers
                  |
       reconnect inventory and recorded answers
                  |
Mac: the original live callbacks, if they still exist
```

Code paths: `src/planner/executor/`, `src/planner/runtime/`,
`src/planner/tickets/locale_configuration.py`.

The wire protocol is version 5. Earlier peers refuse work before replay. The
journal format is version 2. Earlier journal formats refuse ordinary startup
and deployment before mutation. Wire compatibility and journal compatibility are
separate checks. Neither compatibility check converts stored work.

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

Both directions use standard pipelined SFTP through a restricted endpoint. Each
session receives temporary credentials and a pinned host key. An opaque slot
permits only its issued upload or download. No SFTP path grants access to ordinary
account files or the served destination. Four transfers and four open handles
share a session. Each transfer permits sixteen requests of at most 64 KiB.

The VPS keeps each session's SFTP socket and preview sockets together under a
private, owned `~/.cache/panels/ssh` directory in the server user's account home.
It does not use the service's private `/tmp`, which the outer Tailscale SSH
daemon cannot see. Symlink paths, an unsafe directory, or paths exceeding Unix
socket limits refuse setup. Session cleanup removes the whole temporary root.

Completion closes upload handles before a separate copy and digest check seals
the bytes. Panels then validates publication and commits the first receipt.
A successful SFTP write is no proof of publication or a native effect. Lost input
completion retains the original command and caller for reconciliation.

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

An accepted answer and callback application are different durable facts. Panels
saves the permission choice or complete question answer map before transmission.
Reconciliation can give an old answer to its original surviving callback.
An unanswered callback keeps its wait.
A missing child does not receive a replacement answer, a refusal, or a made-up
turn ending.

After reconnect, the VPS acknowledges accepted retained command outcomes through
the existing command acknowledgment path. Later registry inventories omit those
outcomes. Journal receipt cleanup and registry confirmation remain separate.
Neither acknowledgment repeats the native operation.

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

The resident owns installation and service control. Replaceable application code
and persistent executor data have separate roots. The lightweight executor entry
path does not start a Mac Panels server or create a Panels database. A protocol
or journal-format mismatch refuses work before replay.

Read-only journal inspection establishes compatibility, not authority to replace
a process. Inspection errors refuse preflight. An update or rollback still needs
fresh identity-bound quiescence. Active work, callbacks, uncertain operations,
unfinished native calls, or unaccepted entries prevent readiness. Quiescence
rechecks the process identity after awaited shutdown work.

A committed controlled exit returns zero. The resident's successful-exit restart
policy and process observations must prevent a replacement process from being
stopped under an old receipt. Missing or changed process evidence refuses cutover.
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

## Unsupported upgrade transitions

Resident preflight compares the exact requested candidate protocol and journal
versions with read-only installed evidence from both hosts. It runs before the VPS
lifecycle write or any Mac staging, runtime preparation, or service mutation. A
protocol mismatch refuses even when the journal format stays unchanged. Unknown
installed evidence refuses instead of counting as first installation.

A confirmed first installation and a supported same-protocol update keep their
existing paths and safety checks. Current deployment tooling stays separate from
the exact candidate source, so an earlier supported revision remains a candidate.
A Mac inspection uses the pinned SSH identity and does not execute installed code.
Direct deployment paths repeat the relevant checks before their own mutations.
No migration or general retarget mechanism is part of this refusal policy.

## Fresh installation and offline legacy conversion

The production installation path is a fresh format-2 journal. No existing resident
on the target Mac is required. Ordinary deployment continues to refuse incompatible
journal or protocol versions before changing either installation.

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
existing Mac-initiated executor connection and reach only `127.0.0.1` on the Mac.
The URL carries no host name and accepts no host parameter.

Concurrent requests for one Ticket and port share a leased listener. The final
lease closes it. Link loss, replacement, cancellation, and shutdown close its
streams. A new request can acquire a new listener after the connection is ready.
If a stream fails, concurrent requests on the same leased listener can also return
unavailable. The final request releases that listener.
Preview traffic stays outside the journal, replay, and conversation commands.
A failed request never moves to a VPS service at the same requested port.

The remote open and local connect deadlines are one second. Idle streams expire
after 30 seconds. SSH channels supply byte flow control. Each peer admits at most
32 streams. The VPS admits at most 32 listeners. Private Unix reverse listeners
reach only fixed Mac loopback ports. The VPS proxy retains its temporary loopback
listener for browser access. The final lease closes listeners and active channels.
These limits live in `src/planner/previews/configuration.py`.

The proxy preserves the requested port in `Host` and preview-local redirects.
It strips Panels credentials and hop-by-hop headers before the preview receives a
request. Hosted access still passes through the existing trusted ingress. Lease,
remote connection, and stream failures return the existing unavailable response.
Protocol 4 peers refuse the protocol 5 handshake.

Verification uses isolated Linux subprocesses as synthetic VPS and Mac peers, plus
a real browser. That proves the software boundary and does not prove physical Mac
access, installed routes, provider credentials, or deployed behavior.

## SSH prerequisites and boundaries

The launcher requires an explicit authentication mode, host, port, username, and
absolute `known_hosts` path. `public_key` requires an absolute private key path.
`tailscale` forbids that key and requires port 22 plus an absolute executable
Tailscale CLI path. Neither mode reads ambient SSH configuration or uses agents,
passwords, adjacent certificates, proxies, or interactive authentication.
Missing or changed host keys refuse the connection. Encrypted public-key identities
without explicit unattended support refuse.

An explicit public-key launch uses these connection arguments:

```sh
panels executor run --ssh-auth-mode public_key \
  --ssh-host vps.example --ssh-port 2222 --ssh-user owner \
  --ssh-identity-file /absolute/executor-key \
  --ssh-known-hosts /absolute/known_hosts \
  --panels-server-url https://panels.example
```

For Tailscale, replace the mode with `tailscale`, set port 22, and replace
`--ssh-identity-file` with `--tailscale-binary /absolute/tailscale`.
The installed wrapper supplies its exact application revision.

Before each Tailscale connection, the explicit CLI reads `status --json`.
The local daemon must run with kernel networking. The configured IP must belong
to one visible, unexpired peer. A name must match that peer's exact DNS name.
Short aliases, system DNS, subnet routes, and ambiguous matches refuse.
The selected address must also lie in Tailscale's IPv4 or IPv6 range.
This range alone does not authenticate the peer.

The status read establishes only the local daemon's current peer association.
It does not prove reachability, packet routes, Tailscale SSH support, or permission.
This mode trusts the local CLI, daemon, coordination state, and correct kernel routes.
Tailscale enforces device authentication, network access, and SSH authorization at
the existing endpoint. The executor supplies no extra key challenge.
Tailscale check-mode challenges and denials remain connection failures.
The executor does not open a login URL or change endpoint policy.

The numeric connection retains the configured host's exact `known_hosts` identity.
A different pin for the numeric address cannot override a name's pin.
Unknown, changed, and revoked host keys refuse. Advertised peer keys never replace
operator pins. The inner restricted SFTP connection keeps its temporary key policy.

Connection diagnostics use fixed categories and omit addresses, paths, credentials,
and remote output. Each category appears at most once per process, except that the
first failure with context can add one line if its category was already reported.
That first contextual failure includes the fixed connection phase, reader or
session origin, and an allowlisted exception class (unknown types use `other`).
Later failures omit context, keeping the process total to at most eight lines.
Exception messages, remote payloads, and arbitrary class names are never included.

The actual connection path uses AsyncSSH 2.24.1. Connection and login deadlines
are ten seconds. Keepalives run every 15 seconds with three unanswered probes.
Graceful closure has five seconds before abort. Transport failure preserves
uncertain remote effects.

The host must support direct and reverse Unix SSH channels. The inner SSH
connection reaches only the current generation's restricted SFTP socket. Account
SFTP is never a fallback. Missing capabilities refuse work and grant no authority
for host configuration changes. Both connections share the outer TCP transport,
so underlying TCP contention remains.

The restricted service audits each inherited SFTP operation. Unapproved path,
metadata, filesystem, and link operations refuse. The standard read-only
`limits@openssh.com` response remains advisory. Its library and OS maxima grant no
additional Panels authority. Panels enforces its smaller bounds independently.

The optional `copy-data` extension passes through the ordinary authorized handle
hooks. Explicit copies up to 64 KiB can pass between a readable download and a
writable upload. Larger copies and zero-length-to-EOF requests need a larger
internal read and refuse at the 64 KiB hook. Stale, revoked, sealed, wrong-direction,
and out-of-range handles refuse. This safe refusal does not affect ordinary
pipelined SFTP read/write. No private API or larger bound enables this operation.

Generation loss revokes slots, handles, credentials, and preview leases. Active
transfers prevent quiescence. Complete sealed bytes, durable reservations, and
first receipts retain their existing recovery rules.

## Evidence boundaries

Scripted process tests cover link loss, replay, VPS restart, permission callbacks,
file ordering, and identity-fenced exit. They do not establish installed-account
credentials, unattended reboot or pre-unlock operation, or real provider action
timing. Those remain explicit requirements before a live success claim.

Deployment and installation belong to the resident work. See
[runtime environments](environments.md) and [deployment](deployment.md).

_Last verified: 2026-10-18._

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
