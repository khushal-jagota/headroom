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

The wire protocol is version 4. Earlier peers refuse work before replay. The
journal format remains version 1 because its stored envelopes retain the same
interpretation. Wire compatibility and journal compatibility are separate checks.

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

Code paths: `src/planner/executor/journal.py`, `store.py`, `mutations.py`,
`ipc_client.py`, `src/planner/cli/http.py`.

## Files precede their references

Workers use ordinary file tools in a resolved Ticket directory. Publication
captures immutable bytes. The receiver checks the assigned child, owner, path,
length, and digest, then publishes a complete file from quarantine. A dependent
row cannot commit first. A replay of an old publication cannot overwrite a newer
file at the same URL.

Both transfer directions use a distinct typed chunk acknowledgment. The sender
checks its publication identity, offset, and byte count before the next chunk.
An empty, malformed, or mismatched acknowledgment stops the transfer. A chunk
acknowledgment confirms the chunk write only. Full length and digest checks and
complete publication acceptance still precede any dependent row or child command.
An invalid input chunk or completion acknowledgment closes the link and retains
the original command and caller for reconnect. It does not report failure and later dispatch abandoned
work. A rejected completion acknowledgment does not authorize native dispatch, even
when the receiver already holds the complete input file.

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
outcomes. Filesystem receipt cleanup and registry confirmation remain separate.
Neither acknowledgment repeats the native operation.

The VPS exposes per-ask evidence: uncertain, reachable, child gone, or callback
gone, alongside the stored answer and its application. A restored link alone
cannot enable an answer. Loss invalidates the previous evidence. New answers
require current evidence; an answer accepted just before a drop remains durable.

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
after 30 seconds. Each direction sends at most one 64 KiB chunk before credit
returns. Each peer admits at most 32 streams. The VPS admits at most 32 listeners.
Capacity waits occur outside the
shared executor reader and writer lock. These limits live in
`src/planner/previews/configuration.py`.

The proxy preserves the requested port in `Host` and preview-local redirects.
It strips Panels credentials and hop-by-hop headers before the preview receives a
request. Hosted access still passes through the existing trusted ingress. Lease,
remote connection, and stream failures return the existing unavailable response.
Protocol 3 peers refuse the protocol 4 handshake. The spool format stays at 1.

Verification uses isolated Linux subprocesses as synthetic VPS and Mac peers, plus
a real browser. That proves the software boundary and does not prove physical Mac
access, installed routes, provider credentials, or deployed behavior.

## Evidence boundaries

Scripted process tests cover link loss, replay, VPS restart, permission callbacks,
file ordering, and identity-fenced exit. They do not establish installed-account
credentials, unattended reboot or pre-unlock operation, or real provider action
timing. Those remain explicit requirements before a live success claim.

Deployment and installation belong to the resident work. See
[runtime environments](environments.md) and [deployment](deployment.md).

_Last verified: 2026-10-17._

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
current contact unknown. A cached generation cannot authorize an answer.
There is no timer-based cutoff that turns historical receipt into current activity.

Conversation reads include nullable `executor_context`, with the actual remote
child reference and current turn token. Pending ask controls require the exact
child, turn, request kind, request ID, and current connection generation.
`answer_available` remains the server's per-ask admission read. One callback's
evidence cannot authorize another callback, including a reused ID on an old child.
