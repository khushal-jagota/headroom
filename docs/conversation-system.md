# The conversation system

This is how Panels talks to an AI agent. One conversation = one agent
process (hermes, codex, or claude) working in a folder, plus a permanent notebook
of everything that happened in it. The rest of Panels can start it, send a message,
interrupt or kill activity, manage held messages, and ask about live work that needs
the user. Those operations form the whole boundary.

It serves every screen that shows a conversation: a Ticket's and the Chief of
Staff's. Worker orchestration uses the same system. There is no second path.

```
  caller (pane, loop)                the conversation system                agent CLIs
  ───────────────────                ───────────────────────                ──────────
  start / send / interrupt   ──▶   one core: queue, turns, asks,   ──▶   hermes (ACP)
  kill / held / live state          notebook, janitor                     codex (app-server)
                                          │                               claude (Agent SDK)
  read: events after N  ◀──   conversations + conversation_events
        + live tail                 (SQLite, written once)
```

## Diagnostic history expiry

Diagnostic expiry remains an explicit offline operator action. Normal startup
installs the empty omission table and a read index; it does not expire history.
The index lets expanded history find earlier messages without scanning hidden tool
calls or addressed messages. Omission records keep
the original event positions. Retained events and live replay keep those same positions.

When omissions exist, conversation status and legacy event pages report
`diagnostics_expired`. The shared transcript says “Some diagnostic history expired.”
That notice comes from omission records, never from a gap in event numbers. Page
edges describe retained history, so a gap alone does not offer another empty page.

An opened diagnostic detail whose source expired returns the named
`diagnostic_expired` error with HTTP 410. The reader says “This diagnostic detail
expired.” Unknown details still return 404; changed body references still return
409; invalid references still return 422. Chunked and downloaded bodies use the
same distinction. A retained source of the wrong kind is not classified as expired.

The omission table is additive metadata. The selected-view projection version stays
at 1, and saved event positions and cursors remain valid. Replacement snapshots,
resets, and runtime changes discard previously held pages and detail output before
fresh canonical reads. They advance a content generation counter to invalidate cached output;
they do not change how transcript content is generated.

## Durable lifecycle facts

The notebook keeps completed receipts. A separate conversation-owned source keeps
version 1 facts about turns, external attempts, and requests. These facts contain
identities and outcomes, without message content, process handles, or callbacks.
Turn numbers come from a database transaction. Durable occurrence identifiers also distinguish turns across a deployment rollback.
Public request IDs include that durable turn identity. A live runtime maps them to
the provider's request IDs. An old answer cannot address a later request that reuses
the same provider ID.

Panels commits intent before a prompt, steer, answer, or cancellation reaches the
backend. Intent proves no delivery. A prompt outcome says `started` only when the
adapter reports a successful wire write. A steer says `injected` only when the adapter
reports admission to its target turn. Unknown external outcomes remain `unknown`.
The public ordinary-send API still maps `PromptWriteFailed` to refusal. That mapping
can conceal admission uncertainty and requires a separate approved receipt change.
The durable source does not change that public mapping or claim proven non-admission.

Related lifecycle facts and notebook receipts share their existing SQLite transaction.
Read positions, attention, notices, model changes, and skill bindings keep their existing
rules and commit boundaries. A synchronous transaction participant receives the same
connection and typed candidate facts before commit. It can write related records within
that transaction. It cannot commit, await, or call a provider. Any failure rolls back
all those writes. Settled memory and live-tail publication follow commit.

An answer first reserves its request durably. Concurrent answers cannot repeat the
callback. After a successful external write, a failed database settlement retains the
reservation and retries only the database operation after explicit reconciliation.
Caller cancellation does not cancel that owned settlement. A proven callback write
failure restores the pending request only after its failure facts commit.

One runtime holds an exclusive lock beside the canonical database. A second runtime
fails before schema changes or reconciliation and cannot invalidate the first runtime's live work.
The application acquires this lock in its lifespan thread before migrations. The
conversation system then accepts that exact handle. Startup failures release the
handle through its current owner. Shutdown stops local writers before release.
Startup converts lost turns and requests to unavailable facts and incomplete attempts
to unknown facts before admissions, subscriptions, the janitor, or worker loops.
It creates no provider ending, answer, or missing-reply marker. Historical notebook
rows remain unchanged. Old databases have no recoverable live identities, so migration
adds no invented lifecycle facts. An absent fact does not establish capability.

Lifecycle reconciliation restores no queue content or callbacks. It sends no prompt or answer.
Deployment recovery runs separately after runtime admission, as described below.
The source contract lives in `conversation/lifecycle.py`. Its transaction and terminal-transition rules live beside the conversation store.

## Sessions that a deploy ends

A deploy leaves a durable account of the sessions that it interrupts. The account stays outside the application backup and restore set. A rollback therefore keeps evidence from both the old application and its replacement. Missing evidence means that the account is unavailable. It never means that no sessions were interrupted.

After runtime admission, Panels resumes each eligible interrupted conversation through the ordinary send path. This includes Worker, Manager, Chief, and unowned conversations. Both interruption displays and their read API are removed.

Recovery checks current ownership before dispatch. Worker recovery also checks the current claim, stage, ceiling, backlog, blockers, and pending proposal. A changed link or claim cannot authorize recovery of its replacement. Recovery never raises a ceiling or accepts a proposal.

The recovery prompt explains that the prior turn ended and external actions can remain uncertain. The agent must inspect current work before it repeats an action. Panels never replays an unknown prompt or permission answer.

A durable delivery intent prevents another restart from sending the same uncertain recovery again. A queued receipt does not count as resumption. Only a started turn with the notice in its actual prompt counts. Refusal or uncertain delivery receives no automatic retry.

A genuine Worker delivery failure leaves its exact claim errored. The existing Errored group and Ticket attention carry that failure. Other conversations use their existing transcript rows: “not delivered” or “delivery uncertain · do not resend.” In the Chief conversation, Khushal sees that existing one-line row in his chat with Chief. Recovery adds no other owner-facing surface.

An authorized supervisor or Khushal can still use Restart Worker or the existing composer. Those actions retain their ordinary admission rules.

## Exercising permission requests

Real work always runs with full access. There is no selectable ask posture on a
Ticket, Worker type, creation input, or owner control.

An opt-in test starts conversations directly through the real conversation HTTP
routes and overrides the native backend mappings inside that test process only.
Codex uses untrusted approval, the user reviewer, and workspace write on both the
thread and turn. Claude uses default permission mode with the existing callback.
Hermes uses default ACP edit approval with YOLO disabled. The exercise observes
and asserts the settings sent to each real backend; an allowed action alone does
not prove that the override worked.

Separate allow and deny cases request a harmless unique file action. Each waits
with the real backend request open and checks repeatedly that no answer or file
has appeared, then answers through Panels and checks the actual file outcome.
The request stays visible in conversation text and reaches the existing
`awaiting_answer` attention state. It has no conversation or work item attention
dot: those dots mean an unread reply. The earlier inherited claim that an ask had
its own white mark was false; visible request and attention state coverage replace
that check.

The real-provider permission host uses the production application lifespan, including
Signal initialization, current navigation installation and shutdown. It retains the
explicit native test-only permission mapping. Enable it with
`PANELS_REAL_PERMISSION_TESTS=1`, an absolute `PANELS_PERMISSION_EVIDENCE_ROOT`,
and an explicit fresh `PANELS_PROVIDER_RUNTIME_ROOT`. Select only the commissioned
allow case per backend. It records native request/wait/answer/file outcomes and
canonical start, pending and end attention. Calls use real accounts and model usage.
The commissioned route uses the owner's normal authenticated native profiles, including
normal login refresh, locks and fresh vendor session metadata. It never copies or links
credentials or opens an existing conversation. Missing native authentication or model
access stops that backend with its observed outcome. No connection-drop recovery is claimed.

## The notebook

Every conversation owns an append-only run of numbered rows in the database —
its notebook. A row is a finished durable fact: a prompt that was actually delivered
(the message itself, who sent it, how, and — when the sender minted them — the
name the sender gave the message and the moment it was sent), an explicit addressed
message, a silence marker, a tool call
starting, a tool call finishing, a permission ask or agent question request, its answer, a model change, a
discarded held message, a turn ending (completed, failed, or interrupted). Rows
are written once and never edited. Backend prose, including its streaming text, is live
runtime output only — it is never stored, and the agent's private reasoning is
dropped entirely, not stored and not shown.

Late backend prose, deltas, tool activity, asks, usage, and extra endings remain
live-turn facts and are discarded after the turn ends.

The durable event record remains authoritative. The browser reads indexed server views,
not the complete event history. Each view supplies recent bounded pages, compact status,
and one selected tail. Older pages remain available through the existing transcript and
fold controls. Source references preserve access to complete large bodies.

A fresh conversation pane opens through the Focus lens. Focus shows the
owner's prompts, explicit messages addressed to the owner, permission requests, agent questions, and
the answers that settle those requests. Historical owner prompts without principals use
their established owner label, so they remain readable without a record migration.
Failed turns show the adapter's concise failure summary. A historical failed row with no
summary says `turn failed`. Stopped turns and missing explicit replies remain visible as
compact system rows. Complete turn boundaries still
settle the Focus thread and rest line when runtime rows are hidden. A turn with a hidden
opening prompt has no Focus turn head.

Each backend turns its structured failure evidence into that summary before it crosses the
conversation boundary. An HTTP 401 says that authentication was rejected. Panels does not
infer that a credential expired. Provider detail is collapsed to one bounded line and
common credential values are redacted. Standard error remains in operator logs only. The
core supplies an honest unknown-reason fallback if an adapter supplies no usable detail.

Full shows the complete runtime notebook, every held prompt, all live agent text, and
every tool call without a Focus fold. The header toggle and the unmodified `f` key switch
the lens without replacing the conversation. Editable controls keep the key. The browser
keeps an explicit choice across conversation switches and New within the mounted pane.
A fresh pane starts in Focus again.

Focus requests only visible content and compact current status. Full requests its history
only after explicit selection. A Full request starts a temporary file for current-turn
output. That file survives hidden views, rest, lens switches, and the final reader's exit.
The canonical turn end or runtime reset removes it. Startup removes files from stale runtime
identities. Open body responses keep their immutable file version through replacement.

Rest and hidden panes release transcript bodies and native previews. They retain compact
status and logical UI state. A conversation switch invalidates the old requests and tail.
A late response from the previous conversation cannot change the new view. Page reads pause
stream admission, then replay from unchanged coverage across every retained scope. A closed
fold cancels its pending page. Page failures also resume the tail.

While an active lens is open, the owner read position advances through the newest delivered
row. Runtime-only rows can clear an unread mark because each lens displays every result that
needs the owner's attention.

Stream progress alone does not earn read credit. The browser keeps a separate safe read
position, established when an admitted newest timeline page survives retention. Initial
entry keeps that baseline even when placement takes the person to earlier unread text.
Replacing newest with history, refusing a new timeline row, or losing the newest edge
freezes further credit. Hidden rows and status updates cannot restore that evidence.
Older rows can leave retention without stopping credit while the newest edge remains held.
Expanded turn and work-group pages cannot establish conversation-wide read credit.

Latest, ordinary paging that reaches newest, or a fresh newest snapshot can establish new
delivery evidence. A page read earns credit only through the stream position captured
when the queued read actually starts; replay checks subsequent writes separately. Failed,
canceled, and obsolete reads earn nothing. This prevents future false credit. It does not
lower an existing saved read position or restore unread state cleared before the repair.

An open conversation is handed each new row directly, so it never has to be told to
come and look. That is why most rows are written quietly: a historical agent-message row, a tool
call starting or finishing, a plan, a token count, and a compaction are shown only
inside the conversation. Writing a runtime-only row does not send every other open screen
back for a fresh copy of itself. A working agent writes dozens of those rows a minute, and
announcing each one sends every open tab back for everything it is showing.

The rows anything else reads still announce themselves the ordinary way: a delivered,
refused, or discarded prompt, a permission ask and its answer, an agent question and
its answer, a model change, an explicit owner message, and a turn ending. Other screens
read those facts, so they receive the ordinary change announcement. A kind added later
announces itself unless somebody
establishes that nothing outside the conversation shows it.

## Work and messages on other screens

Each Worker, Manager, and Chief exposes the same `conversation_signal` from its own
conversation. `working` means that its runtime is running. `message_for_owner` means that
an explicit `message_to_owner` row is newer than the owner read position. Both can be true
at once. A conversation with neither fact exposes two false values.

The three boolean runtime checks — whether a turn is running, a permission ask is
pending, or user input is pending — inspect existing live state without reading a stored
record or waiting for a conversation lock. A conversation absent from this runtime has
no live work or pending answers. Opening or sending still loads its stored record when
needed. These checks still reject reads when the runtime has shut down or Signal is
unavailable.

Each conversation shows one indicator: an unread message dot takes precedence over the
working spinner. Reading the latest message reveals the spinner if work is still running.
This presentation does not change either fact or the shell's actual-work count.

A turn ending stops work and creates no message mark. Pending approvals, permission asks,
Ticket stages, and child activity do not define either mark. Child totals remain separate
from the Manager's own conversation signal. Existing answer, approval, error, and
notification facts remain available for their own purposes.

Reading or sending a prompt advances only that conversation's established read position.
A later explicit message remains unread when an earlier position is acknowledged.

The runtime publishes the ordinary change signal after work starts and after its settled
end. This publication follows the runtime state transition, so a query cannot retain an
answer from before the prompt or ending commit settled. The event stream carries no
payload. Mounted queries fetch the canonical conversation signal again.

The shell's working count sums that same working fact across Worker, Manager, and
Chief conversations. Each conversation counts once, even when multiple records refer to
it. Settled Managers and backlogged Workers still count when their conversations run.
Worker-step claims do not define this count. Its existing query uses the same change
stream refresh as the individual marks.

## What a message is

A message is not a piece of text. It is a run of pieces, in the order they were
put in, and there are three kinds of piece: written words, a picture, and an
attached document or data file.

There is deliberately no sound. A voice note becomes words by speech-to-text
before anything reaches a message, so nothing here ever sees one.

The microphone appears in every composer and proposal revision box when the
browser can record audio. A coarse pointer changes only the fresh, empty composer:
that state shows voice as the first option. A computer and every other supported
state keep the ordinary text box and offer the microphone beside it.

Every clip uses one conversation-independent transcription route. The route accepts
fresh audio only. It does not create, resolve, link, or store a conversation, and it
does not keep an audio file. The browser retains the clip after a failure and sends
the same bytes again on retry.

The transcript returns to the same editable draft as typed text. Sending a first
dictated message uses the normal first-message path. That send remains the only
operation that creates and links its conversation.

Nearly every message is one piece of written words, and that stays as simple as
it sounds. A message that is only words is stored exactly the way it was before a
message could be anything else, so every conversation already in the notebook
reads unchanged and an ordinary row never grows.

Attachment bytes do not go in the row. A notebook is read in full every time somebody
opens a conversation, so large bytes there would be read again every time. The bytes
stay in a managed file under their conversation, and the row names that file.

The same managed value serves the notebook, browser, and backend. Images retain their
native routes. Hermes receives an ACP resource link for a document or data file. Codex
and Claude receive explicit attachment context with the managed local path.

A message's pictures total at most 3 MiB of raw bytes and each is a structurally
valid PNG, JPEG, GIF, or WebP. That raw limit leaves room for base64 expansion in
both adapter payloads and the browser's outgoing-message recall. A picture is no
more than 8,000 pixels on either side or 25 megapixels. Plain and Adam7 PNGs use
the same bounded scanline validation. The composer turns away unsupported stated
types and files that would exceed the raw envelope before reading them. Messages
that are still optimistic share that 3 MiB budget across the whole tab, including
copies remembered under other conversations; the budget is released when the
notebook catches up or a refusal restores the draft. The server remains
authoritative: it validates the completed bytes and the aggregate before keeping
any file or prompt, and records the media type those bytes prove, not the type the
browser claimed.

Documents and data files have a separate 10 MiB raw-byte limit per message. Panels
accepts PDF, UTF-8 plain text, Markdown, CSV, TSV, JSON, and JSONL. The server derives
the type from the safe file name and validates the content before it keeps any bytes.
Archives, executables, unknown formats, and audio are refused.

Those files last as long as the notebook does, which is forever. Nothing in
Panels deletes a conversation. Reset stops the active conversation and clears its active
pointer, but its Ticket history association remains. Ticket deletion removes the
associations and leaves each conversation record behind. A file removed by either would
turn an attachment somebody sent into a file nobody can retrieve, while the row still
says that the attachment was sent.

A finished tool call keeps the readable text that its backend reports. Claude can
report a result as a list of text and non-text blocks. Panels joins its text blocks
and keeps no finish detail when the list contains no text. Some older Claude rows
contain image-only block lists with base64 bytes. Public event reads omit the detail
from those recognized rows, but the append-only notebook rows stay unchanged.

## What a turn cost, and where the thread was cut

Two more kinds of row, both of them facts the backends were already reporting and
Panels was already throwing away.

**What it cost.** Each backend counts differently and only one of them knows
about money, so every number is optional and an absent one means the backend did
not say — never zero. Hermes gives the turn's own input, output and cached counts
on the answer that ends it, and states a cost on its context update when it
states one at all; a cost in a currency this record has no field for is left
alone rather than converted at a rate nobody supplied. Codex gives running totals
for the thread and no money. Claude gives running totals and `total_cost_usd`,
which is the one number claude itself calls the cost. The pane draws it as the
quietest line on the page.

**Where the thread was cut.** A backend that summarises what came before and
drops it leaves a transcript whose earlier context has silently gone. All three
do it, and each says so: codex sends it
as an item, claude as a system message, and hermes inside its own metadata on a
session update — the ACP protocol has no word for compaction at all, so hermes'
own `_meta` is the only place it appears. The thread draws it as a seam, in the
same stylesheet the pane that came before drew the same thing with.

Panels protects an idle thread before its one-hour backend cache boundary. The
five-minute maintenance sweep starts `/compact` from 50 minutes through less than
60 minutes without ordinary agent activity. The run normally starts between 50 and
55 minutes after the latest durable agent output or turn ending. At 60 minutes the
cache window has passed, so Panels does not compact that conversation automatically.
A user prompt does not start this clock. A restart reads the durable activity marker
but ignores historical conversations older than the useful cache window.

The maintenance turn uses each backend's existing command path. Codex calls
`thread/compact/start`. Claude sends `/compact` through its query. Hermes sends
the `/compress` command that the ACP session advertises. The Hermes adapter waits
for the session's first complete command list and uses its latest list. It refuses
the maintenance write when the list does not arrive or does not contain `compress`.

Panels accepts success only after Codex reports a completed
`contextCompaction` item or Claude reports `compact_boundary`. For a Hermes
maintenance turn, the adapter requires the complete two-line host response with
message and token counts. It confirms success only when the after-message count is
lower. Missing or malformed output, failure text, extra messages, and equal or
higher counts do not confirm compaction. Hermes session provenance still records
compaction during ordinary turns, but it cannot confirm this maintenance turn.

Panels keeps two sequence positions. The confirmed-compaction position records the
ordinary activity protected by a proven boundary. The maintenance-attempt position
records the ordinary activity for which a maintenance turn reached a terminal result.

A confirmed boundary advances both positions. A completed maintenance turn without a
boundary records `not_compacted` and advances only the attempt position. The next sweep
does not retry the same activity. Later ordinary agent activity advances past that
position and can become eligible after 50 minutes. A failed or interrupted maintenance
turn advances neither position, so a later sweep can retry it.

The maintenance turn does not count as ordinary activity. If a message arrives after
the deadline, the conversation lock reserves compaction first and holds the message
behind it. The message proceeds once after confirmed success, refusal, failure, or a
completed turn with no compaction confirmation.

## Sending

The top-level `panels send-message` command is the plain-text command-line door into this
same send operation. It accepts the owner, Chief, Ticket, or Manager principal. An
employee-to-owner send appends an addressed `message_to_owner` row to the employee's
current conversation without invoking a backend. An employee without a current
conversation cannot send to the owner. Employee recipients use their normal current
conversation path, creating it when any send needs one. Every mode starts a turn when the
recipient is idle. Agent keys remain a private resolution detail. The command adds no
second transport, queue, or conversation record.

Existing ordinary Managers can address each other through this same
Send Message path. Both Managers must exist. This grants no authority over the
recipient's record or Workers. Worker messages and help-recipient overrides retain
their existing chain boundary.

Every employee-authored send carries canonical sender and recipient principals derived
from the authenticated request. Panels records that address on every durable outcome,
including held, refused, uncertain, and discarded prompts. Browser-supplied display
labels are not authority.

Automatic recap requests and saved-goal startup messages name Panels as their source.
They carry no sender principal and no explicit reply requirement. Full shows these
messages under the Panels label. Focus excludes them. The authenticated caller still
controls authorization and launch choices. These automatic actions cannot settle a
separate reply that the caller owes. HTTP and CLI callers cannot select system
attribution. Earlier conversation rows keep their recorded source and visibility.

Panels also derives an exact Send Message target from each authenticated sender. It adds
that trusted reply requirement only to the backend wire prompt. When a genuine
requirement exists, it starts the entire prompt with nothing before it. Every
sender-authored byte follows its authenticated sender label. Identical words anywhere
else remain ordinary sender content.
The durable prompt keeps the sender's original content. An ordinary new turn, an
ordinary accepted steer, and a batch from the held line carry the requirement.
Unaddressed loop and maintenance prompts do not. The agent role requires one explicit
send to each addressed sender before the turn ends.
Panels classifies that accepted send as the reply, so its recipient receives no counter-
reply requirement and agent conversations cannot form an acknowledgement loop.

The owner can also ask an agent for a fresh recap. That ask is one route taking a Ticket,
manager, or Chief principal. It empties the recap being replaced and steers the same
fixed sentence into that agent's conversation: "Owner has requested a recap. Remember to
keep it short and top level." Only the owner may ask, and the owner is the only principal
it cannot be addressed to, because the owner keeps no recap. The answer to it is the recap
the agent writes rather than words it sends back. This system message carries no reply
requirement and leaves no missing-reply marker.

Automatic manager startup sends the exact saved goal as a queued Panels message.
Its answer is the work the manager does. It requires no explicit reply to its
creator, so startup produces no `explicit_reply_missing` signal in Focus when the
manager sends no reply. No replacement signal detects a clean delivery followed by
silent inaction. Work indicators, failure rows, and delivery outcomes do not cover that gap.

An actual native slash command uses its backend's exact control route, so its wire form
does not carry the reply requirement. The adapter reports that fact with its delivery
result, so Panels creates no reply debt or missing-reply marker for that command.
Slash-like text that is not a live native command remains ordinary prose. It carries the
wire requirement as any other addressed prompt does. Automatic compaction is a separate
system-only control operation and creates no reply debt.

Backend prose is runtime output only. Finishing a turn does not turn that prose into a
message for the person who prompted it. Only Send Message creates an explicit addressed
reply. A turn remembers each distinct principal whose addressed prompt it admitted,
including steers and a batch drained from the held queue. Immediately before the turn's
ending row, Panels records one `explicit_reply_missing` system marker for each of those
principals who did not receive an accepted Send Message. Repeated prompts from one
principal produce one marker; legacy and automatic runtime prompts have no principal and
produce none. The markers and ending are one ordered transaction.

When a failed turn belongs to a Ticket's current conversation, that transaction also
snapshots the Ticket's current ceiling holder. The existing owner attention flag handles
an owner-held ceiling. A Chief, Manager, or Ticket holder receives one durable Panels
notice through that principal's canonical conversation door. The failed event's
conversation and sequence identify the notice, so replay cannot duplicate it. Historical,
unattached, Chief, and Manager conversation failures create no Ticket notice. A turn
started by a failure notice cannot create another failure notice.

The composer accepts pictures and supported files from its pickers, the clipboard, or
a drop. Attachments wait beside the draft and can be removed one at a time. They can
travel with words or form the whole message. There is no separate upload conversation
or attachment record.

The command accepts `--mode steer|queue|send_now`. Steer is the default for command sends.
Steer asks the current turn to admit the message. Queue holds behind active work. Send now
interrupts current work and starts the message first. The Send Message API accepts the
same three values and defaults an omitted value to `steer`.

The browser composer defaults to steer as well. Its mode control also exposes queue and send now.
Enter and the send arrow use the selected mode. Every selected mode starts an idle turn.
Attachments and run changes cannot steer, so they enter the queue with a visible reason.
A confirmed steer refusal does the same. An uncertain steer remains terminal and never
enters the queue, because a retry can deliver the same message twice.

A lower-level raw send route remains for testing conversation mechanics in isolation. It
rejects every conversation associated with a Ticket, the Chief, or a Manager
Manager. It therefore is not an employee conversation door and does not participate in
principal addressing. No shipped screen uses it: Ticket, Chief, and Manager composers
all use the addressed Send Message operation.

The conversation owns each backend exchange until its receipt settles. If its caller
cancels, the exchange continues once. Normal sends, steering, queue promotion, and queued
batches use that ownership. Repeating the same sender message ID waits for the existing
outcome instead of another provider write. Accepted work keeps its reservation and sender
obligations while its receipt waits for storage. Receipt failure does not stop the child.
The event pump waits for prompt receipts before it records backend results. A steering
barrier also protects the target turn's ending and reply attribution.

ConversationStore retries only SQLite BUSY errors from completed, rolled-back storage
attempts. Each attempt uses the configured busy wait. A short asynchronous pause precedes
the next storage attempt. A cancelled caller does not start another thread beside an
unfinished attempt. The same policy covers receipts, explicit replies, events, endings,
and provider cursors. It never repeats a provider call. Other storage errors retain the
exact pending operation and accepted ownership. Operator inspection through
`recording_failures` exposes the actual error. `resume_recording` resumes persistence
explicitly after repair. This in-memory seam does not survive process loss.

Prompt/model/attention/skill receipts remain one atomic transaction. Internal proposal
review notices receive final admission after model and session preparation, immediately
before prompt composition or steering. The notice domain checks current revision and
effective reviewer in the same SQLite write transaction that records any discard. This
commit orders admission against canonical proposal writes; no database transaction spans
backend I/O. Admission wins if resolution commits later.

Admission uses durable conversation and sender message identity, never notice wording or
sender labels. Each queued message is checked before survivors are combined. Reply
obligations and automatic-notice classification are composed from survivors only. A
replacement session gets a fresh check before its write. If no message survives, no turn
starts, and a newly prepared replacement child is released without changing the durable
model or effort.

A suppressed delivery is an intentional pre-wire outcome, with its reason on the exact
`prompt_discarded` receipt. Its attempt ends as `suppressed`; it is neither accepted work
nor a failed backend write. Repeating the sender identity returns that same suppressed
outcome without another write or a new explicit reply acknowledgment. The notice audit
also preserves each obsolete source's original and current review facts and reason in
`manager_wake_suppressions`, so operators can count what stopped and inspect why.

Recovery settles exact durable receipts first. Known-held work receives admission on its
next departure. A crash after admission without a receipt remains uncertain; historical
dispatching or uncertain work is never reclassified as safely unsent or replayed merely
because its proposal has since changed.

After commit, local state uses the returned rows.
A live-tail publication error does not invalidate the receipt or append it again.

Historical uncertainty remains in the notebook. Reviewed, message-specific provider
evidence can settle it through a later prompt receipt without another provider write.
The latest explicit sequence determines a sender message ID's current fate. A receipt's
optional `reconciles_sequence` points to the exact earlier uncertain row, including
messages without a sender-minted ID. The transcript hides only that superseded warning.
It does not infer delivery from repeated text, another session cursor, or later work.
Unknown outcomes retain the existing recovery action. A recovered Worker clears its
error through canonical proposal progress.

When the agent frees, everything waiting goes to it as one prompt rather than one
turn each. The messages keep their order and each keeps its sender's name in front
of its own words, so an agent handed one run of text can still tell who said what.
Nothing else is summarised or reworded. The adapter adds the first sender's name,
and the held-line combiner adds each later sender's name exactly once. The record is
not collapsed with the prompt: each message still gets its own row with its original
content, because a row names one sender's message id and that id is how a sender
recognises its own message when the record hands it back. A message that asks to run
on a different model starts the next turn instead of joining this one, because a turn
runs on one model and the messages in front of it never named that one. Slash-like prose
can join the same batch. Only a live adapter decides whether delivered content uses a
native command route. If a live command is first in a multi-message batch, the adapter
delivers the complete batch as an ordinary prompt. It does not discard later messages.

A waiting message that cannot be delivered at all is written down as discarded, and
the line carries on to the next one. One message nobody can deliver does not take the
rest of the line with it. If a message fails after its text already reached the agent,
the line stops there instead: the agent is working now, and a second message would be
sent into a turn that is already running.

The composer shows the held line in one inset tray above its recessed input on desktop
and phone. Messages stack inside that tray. Each row stays on one line and can discard
the message or make it run next. When the server reports steering support, a row can also
steer its text into the running turn. The server snapshot is the shared answer, so a
second tab or device shows the same held line and its queue reason. A tab merges its
immediate copy with that snapshot by the sender's message id rather than drawing it twice.

The queue actions and the input action row use the same order, labels, and button treatment
on desktop and phone. Width changes the available text space, not the control design.

The answer to a send is the fate of that delivery, and fate means it happened:
started (the text reached a live agent), queued at a position, injected into the exact
captured turn's owned work, refused with a named reason, or uncertain after a steering
attempt may have crossed the backend boundary. Uncertain is terminal: Panels records it
and does not retry it. The only refusals are genuine impossibilities — no
such conversation, the agent would not start, its session would not load, or the
write failed. Confirmed steer refusals enter the queue.
A busy agent is never a refusal. A message with nothing in it is not a refusal
either — it is not a message, and it is turned away where it is sent. How a turn later ends is never
part of the answer — endings are notebook rows.

An addressed owner prompt advances the durable owner read position only through the
rows that existed when the send entered the conversation. That admission position stays
with immediate writes, held messages, steer outcomes, fallback delivery, and promotions.
Rows that arrive while a write or steer is in flight remain unread.

Codex steering targets the captured native turn through its steering request. A changed
turn is refused. A lost response after a possible write stays uncertain, and Stop remains
available. Panels never retargets or retries that guidance.

Hermes steering uses a small Panels-owned ACP extension. The original prompt and steer
carry the same turn token. Under Hermes' session lock, the extension redirects only that
live turn and returns a structured admission result. It never uses Hermes' later-work
queue. Standard prompt responses end turns, and uncorrelated agent prose never counts as
admission. Hermes steering accepts text only. Panels refuses images, files, and mixed
content before any steering write.

A held message has one server-owned line id. The browser sender id stays beside
it when the browser supplied one, which is how the optimistic copy matches the
shared snapshot. The server supplies an id and send instant when the original
sender supplied neither, so every held row still has an order and actions.

Discard takes one held message out of the line and writes it down as discarded.
Send now stops the running turn, records that interruption honestly, and runs
the selected message ahead of the line. A refusal still records the selected
message, then the remaining FIFO line continues. Steer consumes the selected
text into the captured running turn and does not apply model choices that waited with
that message. Being told that the message is gone is an ordinary answer because
automatic delivery may win the same race.

Claude gives every steered message a UUID. Its queue receipt admits the message to work
owned by the captured Panels turn. Claude can fold that work into its current model loop
or run a native continuation. Panels does not classify those paths. It keeps their
messages, tools, asks, results, and running usage under one turn token until Claude marks
each UUID on a correlated result. That result supplies the exact terminal receipt.

A steered command that Claude has taken up is waited out for as long as the work takes,
because its ending arrives with the work. A steered command that nothing is running is
waited out only briefly. If Claude admits a command and then goes quiet about it, Panels
stops waiting after fifteen seconds and ends the turn on the result it is holding, rather
than leaving the turn open with no ending at all.

Claude Stop sends one interrupt that also cancels queued UUIDs. Panels waits for the
provider receipt, verifies that no owned UUID remains queued, and waits for the active
command result. If any proof is absent, Panels discards the child. The next prompt resumes
the stored session without replay of the steer.

A send may also carry a model or reasoning-effort change. The change rides the
message: browsing a picker does nothing, the change lands when the message is
delivered, a waiting message applies it when it runs, and a refused delivery
changes nothing. Codex and hermes take the change in place; claude is restarted
under the same conversation with its memory carried over.

### Current models within a variant

A model choice selects a variant, such as Sol or Opus. Before each turn, Panels
resolves that variant against the backend's current catalogue. Codex uses the newest
enabled version within the selected variant. Claude keeps its native aliases, including
their context suffixes. Hermes keeps its provider-qualified choices. A provider hint
cannot select a different variant. Panels preserves an explicit reasoning effort and
refuses the turn if the resolved model does not support it.

Catalogue failure does not stop a variant with a last known-good resolution. Panels
uses that resolution and reports that the catalogue is unavailable. Without a last
known-good resolution, it refuses the turn with a clear error. The shared picker
keeps that catalogue warning visible. Its closed face shows the readable resolved model
and its applicable effort, for example “Opus 5.5 medium.” Context qualifiers remain
visible. Models without effort show only their name. If resolution is unavailable, the
face uses the available name without an invented version. Each option in the open
list shows the same resolved model name on one line, without an alias or detail row.
The effort has its own selection step. The selected value remains unchanged.

Ticket creation also tries to resolve its model. If no usable catalogue or cached
resolution exists, creation preserves the requested model and reasoning effort. It
reports unavailable resolution without writing a successful cache entry. Missing
evidence does not block a fresh installation. Available evidence still rejects an
unknown or disabled selection, or an unsupported effort. This exception applies to
creation only. A later turn still requires a successful resolution.

This rule covers Workers, Chief, ordinary Managers, new conversations, reused
conversations and queued messages. Steering retains the active turn's model. Saved legacy
versions resolve within their own variant. New managers use the managed launch
defaults instead of a model pinned by a database trigger.

An explicit correction action previews saved choices and applies selected corrections.
It works for idle Workers and user-owned Stages without a restart or a message.
It preserves backend and reasoning choices. Active turns finish on their current model.
The next turn resolves the corrected model within the same conversation.
The action does not approve work, change a Stage, clear a claim, or start a turn.
Both preview and apply resolve models outside a write transaction. Each accepted
correction acquires a short writer transaction, checks current authority again, and
compares the saved selection before its update. No resolver await crosses that
transaction. Failed targets retain their individual errors in the batch.
See [model correction commands](cli.md#correct-saved-models).

Code: `src/planner/conversation/`, `src/planner/runtime/`.

Hermes new and loaded sessions also return their exact current model through extension
metadata. Panels omits the legacy model request only when that value exactly matches the
requested model. A different model or absent metadata keeps the existing model route.

Claude uses that same restart when its message stream fails for good. The failed turn
stays failed. The next message stops the broken child, resumes one replacement from the
stored Claude session, and sends only that next message. Panels never retries a prompt
whose write failed because it cannot know whether Claude received it. If the resume or
replacement write fails, the refused follow-up remains in the conversation record.

Before there is a conversation the pickers still have to show something, and what
they show is what starting one here right now would run on. That is the owner's own
answer — a Ticket's worker from its Worker type and whatever that Ticket last ran
on, the Chief from its managed settings — and the panel asks the server for it,
which works it out with the same code that will create the conversation. So the
backend and the model on screen before anybody types are the ones a first message
gets. Showing them is not choosing them: a picker nobody touched still sends
nothing, and the message that makes the conversation is created on those values
because they are what the server resolves again when it arrives.

The browser gives each send its own identity and timestamp before the network answers.
Messages drawn in the conversation retain one display slot through record admission.
A receipt can release persistence before the matching row body arrives. The visible
copy remains until that body replaces its contents. Explicit composer-stack and
server-held messages still belong above the input.

A durable refusal or uncertain outcome stays on the same bubble. A transport
rejection without a recorded outcome returns the draft through the existing recovery path.

Queue changes also travel on the conversation's live connection as a contentless
wake. Every open binder then reads the shared snapshot again. The wake carries no
second copy of queue state, so FIFO order and action availability still have one
server answer.

A tab keeps the messages it is still holding, so reloading the page cannot take
somebody's words away before anything has a record of them. They come back saying
nothing at all, because at that moment nothing is known: the record is read a
breath later, and almost always it turns out to have the message, which takes the
copy off the screen the ordinary way. Only a message the record does not have once
it has been read says that nobody ever said whether it arrived — and it says that
because it is true, not because the page has just started.

A send waits three minutes for an answer. Past that nothing is coming, and the
copy says so. Three minutes is long on purpose: the biggest message this system
takes is around eighteen megabytes once its files and pictures are encoded, which
is minutes of uploading on a poor connection before the server can answer at all.
Waiting too long costs almost nothing, because a message the record turns out to
have disappears from the screen as soon as the record is read again. Waiting too
little would put a frightening sentence on a message that was about to turn out
fine.

An unanswered send stays where it first appeared and says “no answer came”.
Its bubble offers Stop showing this message and Send again at opposite ends.
A resend uses a new transport identity but retains the display slot. If the resend
fails before a durable outcome, the old copy returns. A recorded refusal or uncertain
outcome replaces it in place. Historical rows still follow server chronology after reload.

A dismissed local bubble fades for 160ms, then its space closes over 240ms.
History eviction does not use this exit effect.

None of that is a guess about whether the message arrived, and the question stays
open until the record answers it. Most of the time the record already has the
answer and this browser simply missed it, so a conversation holding a send nobody
answered for reads the record again the moment the server is reachable. If the row
is there, the copy goes, the ordinary way.

Acceptance also decides where the thread sits. The accepted message
settles 32px below the top of the view with the rest of it kept for the answer, and
then nothing moves for as long as the answer fits in that space. Once the turn
outgrows the view, new streaming text advances one line per 120ms step when the
reader already follows newest. A multi-line chunk can take several steps, bounded
by the height of the arriving text. Layout changes and entry do not use this path. An accepted
send uses one precomputed destination over 240ms with linear timing. Fresh opens
and lens switches establish their position before reveal. Later layout changes
retain the bottom or held message without animated catch-up. Scrolling away is read from the
person — a wheel, a finger, a hand on the scrollbar — so nothing the thread does
to itself is mistaken for them; while they are reading elsewhere nothing moves
them, and the jump button is the way back.

Manual navigation also ends the accepted send’s right to reserve answer space.
The viewport keeps only the tail that supports the current visible line. Upward
navigation and reply growth reduce that tail. They cannot restore retired space.
The tail includes its gap and disappears completely at zero. The viewport retains
the send’s top inset until an anchor-preserving change fits the available range.

A visible newest line does not grant automatic follow after manual navigation.
Latest remains available while residual support exists. It admits actual newest
history, moves once, and drains support as each planned position fits. Arrival
restores ordinary follow. A fresh gesture or accepted send cancels the old move.
Genuine navigation to actual newest also restores follow after the tail reaches zero.
Layout notifications and native anchor corrections cannot grant follow.

Returning to a browser tab restores the line the person left and its offset in the
view, even if they were at the newest message. The existing logical anchor carries
that position. Content that arrived while the tab was hidden stays below it and
does not start live follow. The reader restores a bounded neighborhood around the
anchor before the viewport places it. If restoration fails, the reader retires the
saved target and admits the newest content before automatic bottom placement.
Visible live text keeps its normal line-by-line follow.

## Reading a turn

A turn is one thing in the thread, however much it took to produce. While it runs
it has a head that says how long it has been going, counting from the moment the
person pressed send rather than from the moment the record caught up — so a
reload part way through shows the real elapsed time instead of starting again
from zero, and a long wait for the agent to start is counted rather than lost.
Where the sender minted no such moment, or minted one its own row cannot be
reconciled with, the whole second the row was written in is counted from instead.

The dock places activity and the latest unread owner preview above its rounded input.
Activity uses the conversation typeface and shows no elapsed timer. Requests and delivery
failures keep their own controls and feedback. The input keeps its complete border,
recessed fill, spacing, and shape in both docked and full conversation.

Hover outside the input changes the conversation surface color. Hover inside the input
returns that surface to its resting color. Hover does not open the conversation.
The preview button opens it through a click, Enter, or Space. A primary click on the
surrounding surface also opens it when the press starts at rest and does not become a drag.
An outside press that dismisses full conversation cannot reopen it on the same click.
The input and its controls remain usable without opening the conversation.

The newest plan is conversation status, not thread history. In full conversation,
the checklist sits between the thread and composer while unfinished work runs.
The plan never appears under the turn that first stated it.

Full shows every message and tool call. Its turn and work groups expand
automatically. Focus shows the person's prompts, messages addressed to the owner,
requests and answers, and failure or stop rows. Messages addressed to the owner
never go behind a turn fold, including in the older transcript renderer.

A normally settled Focus turn keeps its head and its own line, such as
"Worked for 14s". It has no chevron or toggle because nothing remains behind it.
Focus reports zero folded messages and zero tool calls, even when the stored turn
contains tools. A turn that stops without an ending and has nothing behind it
keeps its stop row but has no head, as before.

The fold rules assume that every `message_to_owner` event names the owner as its
recipient. The body flag follows the event kind. SQL membership uses the
recipient-sensitive Focus classification. If another recipient becomes valid for
that event, both classifications must change together.

Each tool call is one line: what happened, and the single fact
that says which call it was — the command that ran, the path that was read, the
pattern that was searched for. The three agents do not agree on how they say
this, so the pane reads what each of them actually gives and says the same kind
of line either way. One titles every call with the tool's name and hands the
call's arguments over as a block of machine text, so its rows used to read
"Bash" and nothing more; the identifying fact is drawn out of those arguments
instead. Another writes the command itself as the title, wrapped in the shell it
was run through, so the wrapper is dropped and the command shown. The third
already writes a title describing the call, and that is left exactly as its agent
wrote it. Throughout, a command reads as the command rather than the invocation
that carried it, the fact is kept short enough that a row stays one line, and it
is dropped when the title already says it. What the tool printed stays behind the
row, where a directory listing cannot push the conversation off the screen.

Because it stays behind the row, opening a conversation does not carry it. A read
brings the first kilobyte of what each tool printed, and says which rows it
shortened. The rest of one row's output is fetched when somebody opens that row.
On the largest conversation that is the difference between nine megabytes and
four. The record keeps every character either way.

A row that was shortened is not read for the line the closed row shows. The start
of an output is not the output: an object cut in half no longer reads as one, and
a first line that was never the whole of one would become a summary of something
nobody printed. So a shortened row's line is drawn from what the call was asked to
do, which is whole.

All of this is read from what the notebook already holds, so conversations
recorded before any of it existed read the same way as new ones.

### Scrolling back, and what is known about it

When the reader leaves a tab, the browser retains a location inside visible message text
and its line offset. A return restores that text after delayed Markdown presentation.
The address stays local to this browser. It includes the message piece, exact content
identity, logical text path, and character offset. Nested preview text supplies no address
for its parent message.

Restoration admits the target Markdown host and group through the existing bounded
presentation policy. Placeholder geometry cannot replace the saved destination. The
conversation waits for the actual character and relevant geometry before it reveals the
restored position. Each attempt gets five seconds while the tab is visible and the
conversation is open. History and text presentation share this budget. Hidden tabs and
the dock pause it. Later layout changes preserve a successfully restored character until
the reader navigates.

Saved-position restoration displays no notice, progress text, warning, or restoration controls.
Ordinary Latest navigation remains available when content lies below the current position.
Failure, unavailable text, or an exhausted budget
retires the saved target and automatically admits the newest conversation page.
The viewport then places the actual final content at the bottom, without a warning,
restoration Retry control, or focus change. Request capacity delays this operation
until a slot becomes available. A failed newest fetch preserves readable content,
real errors, and ordinary Latest access without an automatic retry loop.

Late results cannot reclaim a retired attempt. Explicit movement, navigation, reader
replacement, a newer Latest command, or an accepted send cancels automatic recovery.
Cancellation preserves shared ordinary reader requests. Changed, deleted, or expired
text produces an unavailable result. If settled text cannot fit its saved offset,
automatic recovery starts immediately. Successful text restoration retains its exact
character and line offset. Reader replacement and navigation release target demand.

Scrolling back asks for one page at a time. Before each page the browser closes its live
stream and re-opens it once the page has arrived. That looks wasteful and is not: a
conversation being written to while somebody reads backwards would otherwise keep
interrupting the read, and closing the stream for the length of one page is what stops
that. Expect the stream to be torn down and re-opened once per page of scroll-back.

Scrolling back a long way has been reported as feeling stuck, and the cause is not known.
Two things have been ruled out by measurement, so that nobody spends time on them again.
The reader keeps three hundred items and drops the other end as it goes; driven past that
limit it slides its window correctly, one page at a time, and it still does so while an
agent is writing into the conversation. The stream churn above is also not it.

Two facts for anybody measuring this next.

A test that drives scrolling directly can produce a state a person cannot. Asking for
another page while one is still arriving is harmless on its own, because a second request
for the same thing waits for the first, but it does close the stream again, and it can do
that while the previous re-open is still in flight. No hand moves that fast. A measurement
that scrolls by script and finds the stream thrashing may be measuring its own gesture.

And most conversations cannot reach the three hundred item limit in Focus at all, because
Focus leaves out ordinary agent output. Counted across every conversation on the live
machine, no Ticket conversation holds three hundred items in Focus: the largest holds 197
of its 1,108 rows, and one conversation of 6,847 rows holds ten. The limit is reachable in
Full on any long conversation, and in Focus only in the few very long conversations that
are not a Ticket's.

One known hole in testing this from outside: a Ticket page opens its conversation at rest,
and the Focus and Full controls are not reachable until it is opened, so a script cannot
simply land on a Ticket page and put a long conversation on screen in Full.

## Permissions

When an agent asks permission, the ask always shows and always waits — nothing
in this system ever answers for you, for any tool, after any amount of time. The
choices on the card are the backend's own choices (plus "cancel the turn", which
is ours). An "always allow" answer is passed to the agent's vendor, who does all
remembering — this system keeps no grant state at all. Ask and answer are both
notebook rows; an answer only lands on an ask that is still waiting on the live
turn, and an unanswered ask dies — visibly — when its turn ends.

Agent questions use a separate path. A request keeps every question in order, including
its short header, full text, choices, descriptions, whether several choices are allowed,
and whether a typed answer is allowed. The composer presents one question at a time and
sends the complete answer map only after all are answered. The answer row is written only
after the backend accepts that map. Refreshing the page rebuilds a still-pending request
from the notebook. Cancelling the turn withdraws it. A malformed question request is
shown as a failure and refused; it never turns into an approval prompt.

Claude receives answers under the full question text expected by `AskUserQuestion`.
Codex receives the exact `requestUserInput` answer object expected by app-server. Hermes
ACP has no agent-question request, so its existing permission flow is unchanged.

## Processes, honestly

One child process per conversation, started when a send or maintenance run needs it. The
agent's own session handle is stored on the conversation row, so a later send
can bring the same memory back. After a server restart nothing is running and
the system says so — there is no pretending, and no machinery that quietly
reattaches. An agent idle for half an hour is stopped silently (its memory is
already saved); the next send brings it back without anyone noticing. A resume
that did not actually restore the agent's memory is refused out loud — never
silently accepted as a fresh brain behind an old transcript. A failed turn
writes one error-log line with the ids and the tail of the process's stderr.

Held messages live in memory only: a server restart loses whatever was still
waiting in line (the notebook keeps what was delivered or discarded). Kill is
the loud version of stopping: it ends the running turn and throws away the
waiting line, writing a discard row for each thrown-away message, because text
someone handed over must never vanish without a trace. Pressing New in the pane
kills the old conversation before starting fresh. The old transcript remains part of the
Ticket's ordered conversation history. Only the active conversation accepts new messages.

## Backend cards

The system can describe each agent CLI as a card: is the binary installed and
what version, who is logged in, and which models it offers. An ordinary cold read
returns these facts without waiting for remote update discovery. An explicit refresh
adds Codex and Claude update advice and a one-click update when Panels can run one.
The update command comes from how the CLI was installed. Panels checks the card again
afterwards and reports the update as succeeded, unchanged, or failed. Logging in stays
in the terminal, and the card names the command.

### What a card costs, and the assumption that was wrong about it

Reading the cards was slow for a long time, 118 to 149 milliseconds every time, and
everyone who looked at it assumed the cards were being probed live. They were not. The
probe runs once per process and is then kept; after the first call it answers in well
under a millisecond, and the check for whether the kept answer had gone stale has never
once said yes.

The time went on describing the models. Each card lists the models its backend offers,
and building that list asked a question for every single model: is this model switched
off, what does the catalogue say about it, and is this catalogue the one we already had.
Each of those questions opened its own connection to the database. Opening a connection
here is not cheap, because the database has eighty tables and a new connection reads all
of their definitions before it can answer anything: the first question on a fresh
connection takes 2.39 milliseconds where the second takes 0.022. Describing 26 models
that way meant 78 connections and 492 questions, for one screen asking what backends
exist.

It now asks once for the whole set and sorts the models out in memory, so the cost is the
same whether a backend offers eight models or fifty. The lesson is worth more than the
fix: three separate per-model loops had accumulated here over time and nothing failed,
because every test of this route checks what the card says and no test checked what the
card cost. There is one now, and it fails if the cost starts following the model count
again.

### What a card can say about the codex login

`codex login status` answers a narrower question than it looks like it answers. It reads
the saved login file and says a login is there. It never asks the server. So it reported a
healthy account for twelve days while every codex turn failed, because the saved login's
rotating refresh token had already been spent and no refresh could work.

The codex card therefore reads the saved login as well as asking the CLI, and says one
of these:

- **Signed in.** The CLI reports a login, the saved file can be read, and its access
  token has not expired.
- **Not signed in.** There is no saved login. The codex card offers a sign-in Panels can
  run; every other backend names its own login command instead.
- **Cannot be confirmed.** The saved login is there and its access token has expired, or
  the file cannot be read. An expired token is ordinary — codex refreshes on demand — so
  this is not a fault and the card offers no action. It says that nothing has tested the
  login, because confirming it would mean spending a refresh.
- **Rejected.** A codex turn failed and codex's own login machinery said the saved login
  was refused. That is the only evidence here that was actually tested, so it outranks the
  file. It is remembered against the account and refresh stamp the turn ran on, and it
  stops applying as soon as the saved login reports a different one — a new sign-in, or
  any successful refresh. A turn that was cancelled clears nothing: pressing stop says
  nothing about the login. The record is in the database, because a refused login is still
  refused after a restart and Panels restarts on every deploy. It holds the account id and
  the login's own last-refresh stamp, and no token.
- **Nothing at all.** The CLI answered with no text to read and there was no saved login
  to fall back on. The card stays quiet rather than inventing a state.

A kept card follows its login. Cards are kept because probing costs subprocesses, so the
saved login is re-read on every reading of the card: a sign-in, a refresh, a rejection, or
an access token simply reaching its expiry all re-probe the card without anyone pressing
Refresh.

A failed codex turn is read the same way before its line is written. An explicit refusal
replaces codex's own text, which names whichever subsystem met the refusal first, with a
line saying the login was rejected and must be replaced. A bare `401` keeps codex's words
and adds only that the sign-in may be worth checking. Every other failure is left exactly
as codex put it.

Two things keep that reading narrow. A turn is graded on what the child said since the
previous turn ended, not on the whole of its standard error, so a refusal from an hour ago
cannot explain an unrelated failure now — and a refusal announced during the handshake,
before any turn existed, still belongs to the turn that fails on it. And a refusal only
counts when codex's own login machinery wrote it, which is read as codex's crate path in
the line: standard error carries everything the child and its tools write, and "github
auth: not logged in" is a true sentence about github that says nothing about codex. A
child that dies mid-turn is graded the same way, because a codex that announces a refused
refresh and then exits is the loudest version of this and used to be the one that went
unread.

### Signing codex back in from Panels

A dead codex login used to leave one option: open a terminal on the machine Panels runs on
and type `codex login`. The app server Panels already talks to can start the sign-in
itself, so the codex card offers it.

Pressing Sign in starts a short-lived `codex app-server` child against the real codex home
and asks it for a device-code login. It answers at once with a verification URL and a
one-time code, both shown on the card, so the browser step can be finished on a phone or
any other machine. There is a Cancel beside them.

**Why the app server and not the command line.** `codex login --device-auth` removes the
saved login within a second of starting, and ending the process does not bring it back —
so on that path the account is down from the moment you begin until the browser step
finishes. The app-server path left the saved login byte-identical across every operation
that was measured: the start, the whole pending window, and the cancel. That is narrower
than a promise of no outage. Nothing was measured about the instant a sign-in succeeds, so
Panels does not claim anything about it.

**What it warns you about, and when.** An account that still works, or one with turns in
flight, is told before the sign-in starts that it writes a new credential and that a
running turn may have to be retried. An account that already cannot authenticate, with
nothing running against it, has nothing to lose and gets no caveat.

Whether a turn is running is answered per conversation, and nothing lists a backend's
conversations, so the Backends page cannot tell. It says so rather than guessing: running
turns are **unknown** there, not absent. With the account known unusable and running turns
unknown, the caveat becomes conditional — it says that *if* a turn is running it may have
to be retried, and never claims one does. The caveat is suppressed only when the account is
known unusable **and** nothing is known to be running, which is both halves known rather
than one half unexamined.

**Opening the page finds what is already there.** Nothing about a sign-in is persisted, so
a page that has just opened or reconnected asks whether one is running and shows the same
URL and code if so. If the server no longer has a sign-in the page was watching, the page
says it ended rather than keeping a code nobody will honour on screen.

**Leaving stops everything the page is doing and nothing the server is doing.** Closing a
screen never cancels a sign-in, because the point of a device code is finishing it
elsewhere. Clearing the one armed timer is not enough on its own: a read already in flight
would come back and arm another. So leaving marks the page inactive, and every outstanding
read, poll, start and cancel checks that before it shows anything or schedules anything.
Entering again makes the page live and reconnects normally.

**Nothing is inferred.** A cancel answering `canceled`, a cancel answering `notFound`, a
deadline passing and the child exiting all say what Panels did. None of them is evidence
about the saved login, because the browser step can land in the same instant as a cancel.
So every way a sign-in can end does the same three things: settle the child, take in any
completion that already arrived, then re-read the saved login last.

**Success is a completion and a reading that agrees.** Codex reporting success is not
enough. The saved login must then be readable, hold a credential generation different from
the one the sign-in started on, carry no remembered rejection, and have an access token
that has not already expired. That last condition is what keeps the sign-in and the
identity card saying the same thing: the card calls an expired token `unknown` rather than
signed in, and a sign-in must not claim more than the card will. When any of them
disagrees the card says **unconfirmed** — not signed in, and not failed. No rejection is
ever cleared by hand: a rejection is scoped to its generation and lapses on its own, so
clearing one would be Panels asserting the thing it just failed to confirm.

A completion can arrive in the same instant as a cancel, delivered while the cancel is
already settling. So settling takes in what the child has already said before it reads the
login: the child is stopped, every delivered event belonging to this sign-in is taken in,
and only then is the credential read. A success that landed during a cancel, or during
shutdown, is reported as the sign-in it was rather than lost.

The same rule holds inside a single batch of events. A child exiting and a completion
arriving are delivered by two independent tasks, so both can land before anything looks —
and deciding on the first of them would settle the sign-in while the second was still
unread. So all the evidence in hand is taken in first, and only then is the terminal state
decided once. A completion is read before a child exiting whichever order they arrived in:
the exit says only that the child is gone, while a completion is codex's own account of how
the sign-in went. Throughout, a completion carrying another sign-in's id is dropped rather
than absorbed, and a reported success is never overwritten by a later reported failure.

**One sign-in at a time, and nothing kept.** There is at most one pending sign-in per
backend. Pressing Sign in twice hands back the one already running rather than minting a
second code, because a code you were shown that nobody will honour is worse than no code.
A disconnected page changes nothing and never cancels. A page holding an old session id is
refused rather than allowed to cancel whatever is current. A restart drops the pending
sign-in and says so, because a login id whose child is gone cannot be cancelled or
completed by anyone — storing it would store a handle to nothing.

**Every wait is Panels' own.** The command line prints that its one-time code expires in
fifteen minutes. That is a program's words, not a protocol guarantee, and no Panels
behaviour rests on it. Panels bounds the child's startup, the pending sign-in, the cancel
and the shutdown, and stops showing the code when its own limit passes. It does not claim
the code was still valid up to that point.

**The protocol shapes are pinned separately.** The login messages are vendored as their
own subset with their own pin header. Both the login and thread-and-turn subsets are
generated against `codex-cli 0.163.0`, matching the installed runtime. They retain separate
pins so a future change to either surface is deliberate. Their definitions remain
disjoint; generating both together refuses if they reach the same definition.

### One home and one credential per agent

Each real agent identity has one canonical home and one canonical credential file.
A rotating refresh token must have one owner: refreshing a copied login in a second
home can leave the original holding a spent token. Hardlinks are unsafe too: they
share a file only until a refresh replaces the canonical path by rename.

Synthetic homes with invented credentials are fine. Claude tests can copy settings
while linking the credential path to the canonical file. That link follows the current
credential rather than keeping a second copy. Copying a whole real home or a credential
first is still unsafe. For Hermes, linking only the credential is insufficient: its
auth lock is derived from the home path, so a second home separates lock identity even
when both credential paths reach the same file.

**The existing checks cover only part of this rule.** Before launching a Codex child,
Panels compares its home with the home selected by the server's own environment.
The same resolved home is allowed. For different homes, a shared refresh token or
API key causes refusal, even if account metadata differs. An absent or different
credential is allowed; an unreadable credential is allowed with a warning. The guard
covers Panels' app-server, usage and model catalogue children, not Hermes, Claude,
arbitrary shell launches, or copies elsewhere. It also refuses a different Codex home
whose credential is a symlink to the same login, so Codex tests use the real home.

The repository test inspects Python under `src`, `tests`, `scripts` and `ops`. It
catches supported copy and hardlink calls when a path names `auth.json` or
`LOGIN_FILE_NAME`, including import aliases and earlier simple assignments in the
same scope. Unparseable files fail. It does not trace helper results, attributes,
containers, assignments across scopes or branches, unpacked arguments, byte transfers,
or shell copies. It does not recognize Claude's credential filename. Whole-home
copies naming no credential also pass. A green run proves only that this bounded
tripwire found no supported pattern.

Matching a copied directory to an agent's later home still cannot establish whether
its credentials are real: external environment inputs decide what a template contains.
General coverage would require tracing helpers, branches, filesystem operations and
launch paths. The bounded checks do not provide that general guarantee; review
must enforce the invariant.

The commissioned steering and permission hosts use fresh synthetic Panels databases,
conversations, workspaces and runtime paths. Owner authorization permits normal canonical
HOME and native authenticated profiles, including login refresh/locks and newly created
provider session metadata. Existing native configuration is consumed in place, never
copied. No credentials, configuration, `.env` file or whole home is copied or linked.
The canonical home is retained and never treated as disposable fixture state.

Codex uses its normal CODEX_HOME. Claude uses its normal CLAUDE_CONFIG_DIR. Hermes uses
its normal HERMES_HOME and PLAN_HERMES_HOME together, preserving the one native auth and
refresh-lock identity, with its installed interpreter explicitly configured. No new login,
purchase or authentication workaround is authorized. Native provider caches/logs/temp
follow the production launcher; parent Panels process XDG/temp paths are synthetic.
Hermes requests a filtered environment for the ACP child. The installed ACP transport
merges a trimmed default environment that excludes `PYTEST_CURRENT_TEST`. The commissioned
observer checks the actual ACP spawn and refuses that child if the marker is present.
The model catalogue follows a separate production probe path that inherits the parent
environment, including the marker. The observer records that catalogue environment and
forwards it unchanged, so any native auth guard keeps its normal authority. A catalogue
failure is not evidence that an ACP child failed. Do not remove the marker or bypass a
native guard. Canonical home selection alone does not certify this execution prerequisite.
Native provider temporary paths are not all synthetic. Missing supported native
authentication or model access requires actual provider evidence, not an inference from a
generic catalogue failure.

Code paths: `src/planner/conversation/backends/codex_app_server/{client,login}.py`,
`tests/unit/test_no_codex_credential_copies.py`,
`tests/integration/test_conversation_real_provider_{steering,permissions}.py`.

The source tripwire `python scripts/check_agent_home_copies.py` reports copies whose
destination becomes a selected Hermes, Codex, or Claude home. `--json` produces counts
and findings; an accusation makes the command exit with status 1.

The root must contain all four searched directories: `src`, `tests`, `scripts`, and
`ops`. A missing directory produces an error with the resolved root and status 2,
without a clean finding summary.

Configuration copies of `config.yaml` and `.env` beside a link to the same source's canonical `auth.json` are
reported as dismissals, not silently excluded. External `.env` contents remain
unverified. Literal `settings.json` to the same filename is treated as configuration,
without a dismissal finding; this does not verify its external contents or exempt
arbitrary filenames.

This is a bounded Python AST check. It follows simple local aliases, literal filename
loops, imports, direct environment assignments and `setenv` calls within a lexical scope.
It evaluates proven literal filename equality branches and separates opposite `if`
branches. It does not infer relationships across functions.
It does not analyze shell commands, helper returns, object attributes, arbitrary containers,
conditional alias merges, byte transfers, or runtime paths. Synthetic credential writes
and skill symlinks are not copies. Reviewers must inspect those omitted routes themselves.
A clean result does not certify safety.

This standalone command is invoked explicitly rather than enforced in CI. The two
real-provider harnesses now contain no Hermes copy sites to dismiss. A clean scan
verifies only the supported source patterns; it does not certify credential safety,
external configuration contents, or the omitted copy routes.

Hermes is found from its configured Python environment, the same installation a
conversation launches, even when its executable is not on `PATH`. A packaged
Hermes-only inventory door asks `hermes_cli.inventory` for configured providers
and returns opaque `provider:model` choices, their provider details, and the
configured default. Normal first demand uses Hermes' cached inventory and probes
only the active custom endpoint; an explicit backend refresh forwards Hermes'
refresh and may probe every configured custom endpoint. The answer is kept in the
Panels process until that explicit refresh. It is not polled or copied onto
Tickets. Hermes still offers no Panels reasoning control.

Panels does not ask Hermes for update advice and does not offer a Hermes update action.
Hermes version and model catalogue discovery remain available.

Panels keeps the last successful allowance reading for each backend in its database.
Ordinary allowance reads use that stored answer without a provider request.
The Usage page's Refresh starts provider allowance reads and the separate local record
import in parallel. Allowance reads use ordinary backend snapshots. They do not force
model catalogues, versions, identities, or update advice to refresh.

Codex and Claude allowance reads start independently. A failure leaves that backend's
prior reading in place and preserves the other backend's new answer. The page first
shows an ordinary backend snapshot. Catalogue and supported update advice refresh in
the background, while cached reads remain available. The shared model picker's Refresh
reads provider allowances only.

Codex starts one short-lived app-server child and asks its native
`account/rateLimits/read` method. The request does not start a thread, run a model turn,
spend allowance, or scan rollout files. Panels accepts the account bucket and distinct
named model buckets, then always stops the child. Claude uses the CLI's existing OAuth
login for one bounded request to its usage endpoint. Panels refuses an expired access
token locally and never reads or uses the refresh token. Credentials never appear in a
result or log.

Both providers are translated into the same answer: the observed time and only the
valid rolling windows the provider returned, with percentage used and reset time. A
window also says whether it is five hours or seven days. A model-scoped window names the
stable model id from that provider's current catalogue. Hermes has no usage source here.

Unavailable, logged-out, transport, and changed-response cases are calm typed refresh
results. They do not affect another backend, invent missing windows, or turn ordinary
reads into retries. A logged-out backend keeps its stored reading but shows its login
command instead of old rings.

Panels also keeps one on or off choice for each backend model. A model is on until a
person turns it off on the Backends page. An off model remains in the full backend
catalogue, but no model picker offers it. Existing saved selections remain historical
facts and can still appear as the current value until a person chooses another model.
New Tickets refuse an off model after the explicit choice and Worker defaults resolve.
This includes manual, agent, external-work, and scheduled creation. Schedule validation
checks the choice, and each occurrence checks it again because a saved default can change.
A refused occurrence records failure without a Ticket.

New Chief conversations and Workers without a historical Ticket model also check their
resolved choice after overrides. An enabled override works even if the default is off.
Chief and Worker default saves refuse an off model. Each refusal names the model and
requires an enabled choice. Panels never silently substitutes another model.
Existing choices retain their selected variant. The current-model rule above governs
the concrete model used for each turn.

### Recorded local usage

Tokens and Cost describe local Codex and Claude records on this server. They include
CLI activity outside Panels. They do not describe a complete account bill or allowance
history. Hermes has no source for these historical charts.

Opening the page, changing its period, or receiving a change signal reads stored usage.
Only an explicit Refresh imports native files. Each request reads at most 256 MiB
and cooperatively yields during source reads or parsing after 15 seconds or 2,000 file
visits. Loading stored snapshots, merging records, serialization, and price refresh
occur outside that deadline. It is not a hard HTTP response timeout.
The next request resumes the stored
queue. Small slices alternate providers and files. Large files and large JSON lines
remain eligible. The streaming reader discards message and tool bodies as it reads.
Its checkpoints contain syntax and selected usage fields. Complete block checks detect
interior rewrites before a candidate replaces verified records. Source changes restart
that candidate and preserve earlier records. Native files remain read-only.

The queue covers source files modified within 90 days. Directory discovery has a
100,000-entry and request-time limit. An unreadable or bounded inventory reports partial
coverage. Progress describes this finite local read, not complete account history.
SQLite commits the cursor and data together, so a restart resumes the same work.
Append-only growth preserves the frozen prefix and its read progress. Every metadata
generation change restarts block verification. A file that changes before every request
needs a quiet verification window before its candidate enters the totals. Other files
continue fairly, and its earlier verified records remain visible with partial coverage.
A verified prefix can enter stored totals before its file finishes. An observed unread
tail stays in the queue, and its byte count extends the progress total. Files first
found during a pass wait for the next finite pass. Known pending files keep coverage
partial and `more_work` true until a later request reads them.
The same rule covers a completed member whose identity, size, or metadata changes in
the latest inventory. Progress counts that file once and defers its new version to the
next pass. Ready means that this latest bounded inventory has no known unvisited work.

Copied records count once across files. Codex counts each stable completed response
identity once. It keeps the response's original thread and session, even when another
file contains a copy. Repeated counter updates and cumulative turn or thread totals do
not add consumption. Old counter records need verified changes. Their producer history
remains uncertain.

Claude uses message and request identities. Overlapping or conflicting identities
remain excluded with partial coverage. Conversation events and allowance snapshots do
not become extra chart records.

Missing required token counts remain unknown. As in T3 Code, an omitted optional Codex
cache category means zero. An explicit null or invalid category remains unknown.
The producer reports cache categories separately within inclusive input. A missing
reasoning breakdown remains unknown and never adds another charge to output.
A valid zero remains zero. Token totals with an
unknown contributing value remain unknown. Cost follows T3 Code's price calculation.
It uses source-reported USD when present. Otherwise it prices uncached input, cache reads,
cache writes and output from the stored LiteLLM table. Reasoning remains part of output.
Published fast and ultrafast rates apply to the recorded service tier. A tier without
published rates uses its standard rates, as in T3 Code.

The full external price document and fetch time remain in SQLite. Summary reads never
fetch prices. Explicit Refresh can update the table, with T3's 60-second refresh floor,
24-hour ordinary cache lifetime, a 10-second request timeout and a 16 MiB response bound.
An unavailable source leaves the stored table available. Unknown model rates remain
unpriced. Exact names and unambiguous aliases match the table. Bare family names do not
select an invented generation.

Known cost subtotals remain visible with an Unpriced record count. The data contract
keeps table-calculated and source-reported USD separate. This value does not establish
actual account charges. Cost categories, cache savings and speed premiums use the same
price rules as T3 Code. A source-reported total scales the category split proportionally.

Input already includes cache tokens, and output includes reasoning tokens. Neither
subset is added twice. Charts show observed periods and leave unrecorded intervals as gaps.

_Code paths:_ `src/planner/conversation/usage/` owns the contract, native adapters,
bounded import, storage, and summaries. Its `api.py` exposes
`GET /api/conversation/usage/summary` for stored reads and
`POST /api/conversation/usage/refresh` for explicit imports. The refresh accepts no
browser-supplied source paths or credentials.

## The composer catalog

Each conversation keeps one typed catalog for commands, skills, apps, and plugins.
Each entry carries its visible text, exact insertion text, description, and optional
argument hint. A slash at the absolute start of the message offers commands. A dollar
offers skills. An at sign offers apps and plugins. Leading whitespace, an empty first
line, or a trigger on a later line does not open the menu. The composer narrows that
eligible list as text is typed.

### When the menu arrives

The catalog is read when the conversation opens and again each time a turn ends. It does
not arrive with the record. It used to, and it dominated it: the largest real catalog is
66,211 bytes for 155 entries, where everything else the record stores about a conversation
comes to about 620. The record is re-read every time the conversation gains a row, so
watching one agent work for seventy seconds sent the same closed menu 68 times, about
2.3 MB of it.

Turn end is the occasion because a backend only reports its commands a moment into its
first turn. Reading the menu lazily, when somebody first types a trigger, would show an
empty menu on a conversation that has a full one today.

The cost of reading it then and not more often: for the length of one turn the menu can be
out of date. A skill or command that appears while a turn is streaming is not offered until
that turn ends or the conversation is reopened. A turn is seconds to minutes, the menu is
only consulted when somebody opens it to type, and the alternative is re-sending all 66 KB
of it every time an agent writes a line.

The keyboard owns the highlight. It starts at the top of the list and moves with the
arrow keys. A row takes the highlight when the pointer really moves onto it. A menu that
opens or changes size under a pointer standing still does not move the highlight, because
the browser reports the pointer's resting place at those moments and no person chose
anything.

A choice replaces the active token with the entry's exact insertion text. That result is
still an ordinary draft. The backend adapter resolves a live catalog command from the
sender's original draft and keeps the exact slash command for native dispatch. Other
delivered content gets the sender's name at its start. Transcript rendering keeps the
original draft.

Hermes maps the command lists that it volunteers into slash command entries. Claude maps
the command list from its process handshake in the same way, so project commands still
follow the conversation folder. Their visible text is `/name`, and their insertion text
is `/name `. Their adapters retain the live command names so ordinary and steered command
dispatch keeps the slash token at the absolute start.

Codex publishes its native commands when a thread starts or resumes, then reads enabled
skills, callable installed apps, and enabled installed plugins in the background. Apps
come from thread-scoped `app/installed` runtime state, using the runtime name or exact ID.
Only enabled, callable apps appear. Optional `app/read` requests enrich their names and
descriptions in batches of at most 100 IDs. Metadata cannot grant callability. Panels
never reads the full app directory for this menu. Missing or failed metadata keeps the
runtime labels, and slow metadata cannot hold up a message.

Codex runs each of those commands through a call of its own, and every one of those
calls starts a turn. So a Codex command cannot join a turn that is already running. A
command steered into a running Codex turn is not sent as text: the adapter says it
cannot take it, and the message goes back to the held line and runs as its own turn when
the current one ends. The held row says it is waiting because a command runs as its own
turn. Hermes and Claude take a steered command inside the running turn, and keep their
behaviour.

App-change hints refresh installed state; skill-change hints refresh skills and plugins.
Each of these sources, plus optional app metadata, runs independently. Bursts merge over
50 milliseconds, and successful app refreshes wait at least one second before another.
A failed source retries after 5, 10, 20, 40, 80, 160, then 300 seconds, without another
hint. Further hints cannot shorten a failed source's wait. Failed skills, plugins, or
installed state remove that source's entries until recovery; failed metadata retains
runtime labels. Warnings contain only the source, error category, numeric error code
when present, and retry delay, capped at 240 characters.

Successful app metadata stays with that child for 15 minutes. An installed-state update
fetches missing or expired metadata; expiry alone starts no polling. Removing an app
also removes its metadata, and a late reply cannot restore its entry or invocation.
Unchanged entries and invocations produce no catalog event. Stopping the child cancels
and awaits its source requests and retries.

Codex resolves a selected token against that complete snapshot when the prompt starts.
Skills use Codex skill input. Apps and plugins use exact mention paths. Exact protocol
identities are removed when duplicated. Two different identities with the same token get
visible, stable aliases. A unique identity keeps its short canonical token.
Command, skill, and mention collisions cannot cross trigger types because their first
characters differ.

A dollar or at-sign token is reserved only at the absolute start of a message. Codex
refuses it when the current snapshot cannot resolve it. Unknown slash text remains prose.
Malformed forms of a known native command are refused. Skills, apps, and plugins keep
their structured input when a message also carries attachments or run value changes.
Native commands refuse those combinations because their protocol methods cannot carry
them.

`/compact` and `/review` use their native app-server methods. `/goal` and `/goal get` read
the thread goal. `/goal set <objective>` sets a nonempty trimmed objective to active.
`/goal clear` clears it and reports whether a goal existed. Goal calls do not invoke a
model. Panels writes one concise assistant result and one completed turn through the
normal notebook lifecycle. RPC, response validation, and ephemeral-thread failures refuse
the prompt. Direct RPC responses own these command results. Goal update and clear
notifications are therefore ignored rather than decoded into a second result.

The last catalog reported is kept on the conversation. The menu still works when no
child process runs. A conversation with no report yet offers nothing.

## Proving the three backends

The contract's proof runs every backend key against one scripted agent. That proves the
core three times over, not the three adapters. What each real agent CLI actually does is
proved by four opt-in exercises that make real model calls against the logins on this
host. They are off unless you switch them on, and nothing runs them on a push, a pull
request, or a schedule. A person runs them or nobody does.

The whole set takes about four minutes. Cost is not the reason to skip them.

Each exercise is its own `pytest` command, from the repository root with the tree's own
virtual environment:

| Switch on | Point it at | Cases |
| --- | --- | --- |
| `PANELS_REAL_CLAUDE_TESTS=1` | `tests/unit/test_conversation_claude_real_cli.py` and `tests/unit/test_conversation_claude_agent_sdk.py` | 8 |
| `PANELS_REAL_CODEX_TESTS=1` | `tests/unit/test_conversation_codex_real_cli.py` | 12 |
| `PANELS_REAL_HERMES_TESTS=1` | `tests/unit/test_conversation_hermes_acp.py` | 4 |
| `PANELS_REAL_SYSTEM_STEERING_TESTS=1` | `tests/integration/test_conversation_real_provider_steering.py` | 3 |

The first three drive one adapter directly. The fourth uses `create_app` and its actual
application lifespan against the real agent processes: Signal composition, navigation
installation, conversation API, janitor and shutdown. Test mode omits background Worker
scheduling. No conversation facade or transaction driver substitutes for production.
The permission integration host uses the same lifespan. Controlled tests retain the
failure, denial and uncertainty coverage.

Both integration hosts require a clean source tree, absolute evidence destinations and
an explicit fresh `PANELS_PROVIDER_RUNTIME_ROOT` containing their synthetic databases
and runtime paths. The steering evidence destination is `PANELS_STEERING_EVIDENCE_ROOT`.
The permission destination is `PANELS_PERMISSION_EVIDENCE_ROOT`. Before application
import, isolate Panels databases, logs, backups, locks, workspaces, XDG and parent temporary
paths. Normal HOME and native profile paths are the explicit owner-authorized exception.
Never open a production Panels database or an existing provider conversation. The former
home-template variables and credential-sharing helpers are removed. The supported native
routing and exact host/launcher receive independent review before provider calls. The bounded commission is one steering/stop/
resume scenario and one explicit-allow permission roundtrip per backend; it does not
commission additional paid denial or failure permutations.

### Historical adapter observations

The following observations describe earlier runs, not current-tree execution or a
current pass/fail count. Current integration results must identify their exact source,
normal native profile routing and fresh Panels state.

- **The steering exercise.** Direct `steer` starts a new ordinary turn when idle in
  current staging and the candidate. The no-running-turn refusal belongs to the separate
  held-prompt promotion API. The paid integration host omits its obsolete idle-refusal
  assertion; controlled conformance coverage owns idle normalization. Active steer
  admission proves the provider accepted the exact running-turn request, not that the
  model obeyed it. Requested and catalogue-resolved effective models remain distinct.
- **Two claude cases, and they are not reliably red.** Both plant a codeword and ask for it
  back, and the model reads that as an attempt to smuggle instructions past it — sometimes.
  One run fails both, the next fails one. A case that turns on whether a model feels like
  complying is worse than one that always fails, because it teaches you to ignore it. Worth
  repairing, by asking for something a model will agree to do.
- **Two hermes cases.** They ask for a long count, sleep two seconds, then cancel and expect
  an interrupted turn. The turn has already finished. Worth repairing, by timing the cancel
  off the first streamed token instead of the clock.
- **One codex case.** It changes the model mid-conversation to one a ChatGPT-account login
  cannot use, and codex says so plainly. Not worth repairing: a hardcoded vendor model name
  has to stay valid forever on whichever account is logged in, and the claim that carries
  weight — that a model sent with a turn reaches the model call — is proved by the case next
  to it, which sends a model that does not exist and reads the refusal back.

Earlier provider receipts show active-steer admission into a genuinely running turn.
They remain evidence about their exact historical source, not execution of the current
application lifespan with fresh synthetic homes.

## Code paths

- Contract and floor defaults: `src/planner/conversation/contracts.py` (the
  docstrings are the documentation of record).
- Core, notebook, storage: `src/planner/conversation/` (`system.py`,
  `events.py`, `storage.py`); their tables are part of the baseline revision under
  `src/planner/core/migrations/versions/`.
- What a message is made of, and where the files it carries are kept:
  `src/planner/conversation/message_content.py` and `message_files.py`. The
  files sit under the same managed root as ticket files, resolved by the same
  checks in `src/planner/files/logic/paths.py`.
- The three backends: `src/planner/conversation/backends/`.
- Reading side, live tail, backend cards: `src/planner/conversation/api.py`,
  `live_tail.py`, `snapshot.py`.
- The panes: `web/src/components/conversation/`, `web/src/lib/conversation/`,
  and the routes that mount them.
- The contract's proof: `tests/support/conversation_contract_conformance.py`,
  run against the real system in
  `tests/unit/test_conversation_conformance.py`. Every backend key is bound to the
  same scripted agent in `tests/support/conversation_system_under_test.py`.
- The real-agent exercises: the four files named above, plus
  `tests/support/conversation_claude_agent_sdk_bench.py` and
  `conversation_codex_app_server_bench.py`.

## Handoffs

- **Worker orchestration** (`worker-orchestration.md`) sends each Worker step into
  the Ticket's conversation.
- **The front end** (`frontend.md`) renders the shared pane and its Workspace marks.

## Deferred

- **Held-line durability**: if losing the in-memory waiting line on a restart
  ever bites, the line can be made durable without changing the contract.
- **Error envelope**: the conversation routes speak plain HTTP errors, not the
  planner's error envelope. Trigger: one error contract is adopted across the API.

_Last verified: 2026-10-18._
