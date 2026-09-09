# The conversation system

This is how Panels talks to an AI agent. One conversation = one agent
process (hermes, codex, or claude) working in a folder, plus a permanent notebook
of everything that happened in it. The rest of Panels can start it, send a message,
interrupt or kill activity, manage held messages, and ask about live work that needs
the user. Those operations form the whole boundary.

It serves every screen that shows a conversation: a Ticket's, the Chief of
Staff's, and the development pane at `#/dev/conversation`. Worker orchestration
uses the same system. There is no second path.

```
  caller (pane, loop)                the conversation system                agent CLIs
  ───────────────────                ───────────────────────                ──────────
  start / send / interrupt   ──▶   one core: queue, turns, asks,   ──▶   hermes (ACP)
  kill / held / live state          notebook, janitor                     codex (app-server)
                                          │                               claude (Agent SDK)
  read: events after N  ◀──   conversations + conversation_events
        + live tail                 (SQLite, written once)
```

## The notebook

Every conversation owns an append-only run of numbered rows in the database —
its notebook. A row is a finished thing: a prompt that was actually delivered
(the message itself, who sent it, how, and — when the sender minted them — the
name the sender gave the message and the moment it was sent), a completed agent
message, a tool call
starting, a tool call finishing, a permission ask or agent question request, its answer, a model change, a
discarded held message, a turn ending (completed, failed, or interrupted). Rows
are written once and never edited. Streaming output (the text growing word by word) is live
decoration only — it is never stored, and the agent's private reasoning is
dropped entirely, not stored and not shown.

A finished agent message remains recordable when it arrives just after its backend turn
ending. This matters for persistent runs such as Claude's: delegated work can wake the
parent after an earlier result. The whole parent message is durable conversation content,
so Panels keeps it under the most recently ended turn. Late deltas, tool activity, asks,
usage and extra endings remain live-turn facts and are still discarded.

Reading is one rule everywhere: fetch the rows after the last one you hold, then
listen for new ones. Opening a conversation, reconnecting after a dropped
connection, and a second device are all that same fetch. Nothing re-downloads
mid-read.

An open conversation is handed each new row directly, so it never has to be told to
come and look. That is why most rows are written quietly: an agent message, a tool
call starting or finishing, a plan, a token count, and a compaction are shown only
inside the conversation, and writing them does not send every other open screen back
for a fresh copy of itself. A working agent writes dozens of those a minute, and
announcing each one sends every open tab back for everything it is showing.

The rows anything else reads still announce themselves the ordinary way: a delivered,
refused, or discarded prompt, a permission ask and its answer, an agent question and
its answer, a model change, and a turn ending. Those are what the board reads to say
whether a Ticket is working or needs its owner, so they are what a screen outside the
conversation is told about. A kind added later announces itself unless somebody
establishes that nothing outside the conversation shows it.

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

A successful boundary suppresses another automatic run until a later ordinary
turn produces agent activity. The maintenance turn does not reset its own clock.
If a message arrives after the deadline, the conversation lock reserves compaction
first and holds the message behind it. The message proceeds once after confirmed
success, refusal, failure, or a completed turn with no compaction confirmation.

## Sending

The top-level `panels send-message` command is the plain-text command-line door into this
same send operation. It resolves a Chief, Ticket, Sprint Item, or registered agent, then
uses that owner's current conversation path. It creates the normal conversation for the
first three owner types when needed. A general agent row has no launch configuration, so
it can receive a message only while it points to a current conversation. The command adds
no second transport, queue, or conversation record.

The composer accepts pictures and supported files from its pickers, the clipboard, or
a drop. Attachments wait beside the draft and can be removed one at a time. They can
travel with words or form the whole message. There is no separate upload conversation
or attachment record.

Send has no delivery knob. Every new message runs when the agent is free, and a
busy agent holds it in a FIFO line. Enter and the send arrow use that same rule,
including while a turn runs.

When the agent frees, everything waiting goes to it as one prompt rather than one
turn each. The messages keep their order and each keeps its sender's name in front
of its own words, so an agent handed one run of text can still tell who said what.
Nothing is summarised or reworded, and a single waiting message is sent exactly as
it was. The record is not collapsed with the prompt: each message still gets its own
row, because a row names one sender's message id and that id is how a sender
recognises its own message when the record hands it back. A message that asks to run
on a different model starts the next turn instead of joining this one, because a turn
runs on one model and the messages in front of it never named that one.

A waiting message that cannot be delivered at all is written down as discarded, and
the line carries on to the next one. One message nobody can deliver does not take the
rest of the line with it. If a message fails after its text already reached the agent,
the line stops there instead: the agent is working now, and a second message would be
sent into a turn that is already running.

The composer shows the held line in one inset tray above its recessed input on desktop
and phone. Messages stack inside that tray. Each row stays on one line and can discard
the message or make it run next. A Hermes row can also steer its text into the running
turn. The server snapshot is the shared answer, so a second tab or device shows the same
held line. A tab merges its immediate copy with that snapshot by the sender's message id
rather than drawing it twice.

The queue actions and the input action row use the same order, labels, and button treatment
on desktop and phone. Width changes the available text space, not the control design.

The answer to a send is the fate of that delivery, and fate means it happened:
started (the text reached a live agent), queued at a position, injected, or
refused with a named reason. The only refusals are genuine impossibilities — no
such conversation, the agent would not start, its session would not load, the
write failed, a steer with no running turn to join, or a steer at a backend that
cannot steer. A busy agent is never a refusal. A message with nothing in it is not a refusal
either — it is not a message, and it is turned away where it is sent. How a turn later ends is never
part of the answer — endings are notebook rows.

A held message has one server-owned line id. The browser sender id stays beside
it when the browser supplied one, which is how the optimistic copy matches the
shared snapshot. The server supplies an id and send instant when the original
sender supplied neither, so every held row still has an order and actions.

Discard takes one held message out of the line and writes it down as discarded.
Send now stops the running turn, records that interruption honestly, and runs
the selected message ahead of the line. A refusal still records the selected
message, then the remaining FIFO line continues. Steer consumes the selected
text into a running Hermes turn and does not apply model choices that waited with
that message. Being told that the message is gone is an ordinary answer because
automatic delivery may win the same race.

A send may also carry a model or reasoning-effort change. The change rides the
message: browsing a picker does nothing, the change lands when the message is
delivered, a waiting message applies it when it runs, and a refused delivery
changes nothing. Codex and hermes take the change in place; claude is restarted
under the same conversation with its memory carried over.

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

Nobody waits for the network to see what they typed or attached. The browser gives a message
its own name and stamps the moment the person pressed send, draws it in the
composer stack there and then, and empties the box. Those two stamps travel with
the message and are kept on its row, so the shared held snapshot and the eventual
notebook row can recognize the same message. If the message turns out to have got
nowhere, the copy goes and its exact words, pictures and pending run choices come
back to the box, unless something else has been composed there since.

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

Sending also decides where the thread sits. The message that was just sent
settles near the top of the view with the rest of it kept for the answer, and
then nothing moves for as long as the answer fits in that space. Once the turn
outgrows the view the thread follows, by the least it can while keeping the
newest line in sight, and never backwards. Scrolling away is read from the
person — a wheel, a finger, a hand on the scrollbar — so nothing the thread does
to itself is mistaken for them; while they are reading elsewhere nothing moves
them, and the jump button is the way back.

## Reading a turn

A turn is one thing in the thread, however much it took to produce. While it runs
it has a head that says how long it has been going, counting from the moment the
person pressed send rather than from the moment the record caught up — so a
reload part way through shows the real elapsed time instead of starting again
from zero, and a long wait for the agent to start is counted rather than lost.
Where the sender minted no such moment, or minted one its own row cannot be
reconciled with, the whole second the row was written in is counted from instead.

The newest plan is conversation status, not thread history. At rest, an active plan
step shows its position, its text, and the elapsed time. The position opens the whole
checklist on pointer hover or keyboard focus. A permission request still replaces this
line because it needs the person. Without an active step, the line keeps its normal
elapsed time and newest tool call. In peeked and opened states, the same checklist sits
in a centered 34-pixel strip between the thread and composer while unfinished work runs.
The plan never appears under the turn that first stated it.

When the turn is over that head becomes a fold, and everything the turn produced
goes behind it: its tool calls, and everything the agent said on the way to its
answer. What stays out is the person's own message, the last thing the agent
said, and — when a turn failed or was stopped — the line saying so. A turn that
thought out loud for five paragraphs is exactly as long to scroll past as one
that made five tool calls, so both collapse the same way and a finished turn
reads as one paragraph with a "Worked for 14s" you can open. Opening it puts
everything back where it happened, the commentary between the runs of tool calls
it sat between rather than gathered up at the end. A turn that never said
anything keeps nothing back, because there is no answer to hold; a turn still
running folds nothing at all.

Each tool call inside that fold is one line: what happened, and the single fact
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

Hermes installation maintenance is exclusive with those child processes. Panels
refuses an update while any Hermes child is starting or alive, including an idle
child. Once an update has been accepted, a new Hermes child waits until the
update command and the card refresh have both finished. The reservation is made
before spawn, so a send and an update cannot both see an empty gap and race into
it.

Held messages live in memory only: a server restart loses whatever was still
waiting in line (the notebook keeps what was delivered or discarded). Kill is
the loud version of stopping: it ends the running turn and throws away the
waiting line, writing a discard row for each thrown-away message, because text
someone handed over must never vanish without a trace. Pressing New in the pane
kills the old conversation before starting fresh. The old transcript remains part of the
Ticket's ordered conversation history. Only the active conversation accepts new messages.

## Backend cards

The system can describe each agent CLI as a card: is the binary installed and
what version, who is logged in (where the CLI will say without a network call),
which models it offers, and whether a newer version exists — with a one-click
update whose command is inferred from how the CLI was installed, re-checked
afterwards, and reported as succeeded, unchanged, or failed. All of it is
advisory; nothing blocks on it. Logging in stays in the terminal, and the card
names the command.

Hermes is found from its configured Python environment, the same installation a
conversation launches, even when its executable is not on `PATH`. A packaged
Hermes-only inventory door asks `hermes_cli.inventory` for configured providers
and returns opaque `provider:model` choices, their provider details, and the
configured default. Normal first demand uses Hermes' cached inventory and probes
only the active custom endpoint; an explicit backend refresh forwards Hermes'
refresh and may probe every configured custom endpoint. The answer is kept in the
Panels process until that explicit refresh. It is not polled or copied onto
Tickets. Hermes still offers no Panels reasoning control.

Hermes supplies its own update advice through `hermes update --check`. Panels
withholds the updater when that command identifies an installation it cannot
drive; a network or authentication failure remains advisory because it does not
change how the installation is managed. An authorized update runs as
`hermes update --yes` without force options. A failed check does not make an
otherwise usable backend unavailable. Every attempted update refreshes the card,
even when the command fails.

Panels keeps the last successful usage reading for each backend in its database.
Opening the Backends page, reading `GET /backends`, receiving a change signal, or
refreshing another query only reads that stored answer. None of those actions contacts
a provider. The one Refresh on the Backends page re-reads every catalogue and usage
source through `POST /backends/refresh`. Codex and Claude refresh independently, so one
failure does not discard the other provider's new answer. A failed refresh also leaves
that backend's prior reading in place.

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
New Ticket, Chief, and Worker default saves refuse an off model.

## The composer catalog

Each conversation keeps one typed catalog for commands, skills, apps, and plugins.
Each entry carries its visible text, exact insertion text, description, and optional
argument hint. A slash at the absolute start of the message offers commands. A dollar
offers skills. An at sign offers apps and plugins. Leading whitespace, an empty first
line, or a trigger on a later line does not open the menu. The composer narrows that
eligible list as text is typed.

A choice replaces the active token with the entry's exact insertion text. That result is
still an ordinary draft. Message delivery and transcript rendering do not interpret or
rewrite it.

Hermes maps the command lists that it volunteers into slash command entries. Claude maps
the command list from its process handshake in the same way, so project commands still
follow the conversation folder. Their visible text is `/name`, and their insertion text
is `/name `.

Codex reads its catalog from the app-server after each thread starts or resumes. It joins
enabled skills, callable installed apps, and enabled installed plugins with the native
`/compact`, `/review`, and `/goal` commands. App metadata comes from `app/list`. Current app
callability comes from `app/installed`. This prevents an installed but unusable connector
from appearing in the menu.

Codex refreshes the complete catalog after skill or app change notifications. Refreshes
run beside the app-server reader and merge repeated notifications. Each source is read
independently. A failed source contributes nothing to the new snapshot, while fresh
sibling sources remain available. Apps appear only when both app reads succeed. Source
errors name the failed source in the log. Every refresh publishes its truthful new
snapshot, including a snapshot that contains only native commands.

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

## Code paths

- Contract and floor defaults: `src/planner/conversation/contracts.py` (the
  docstrings are the documentation of record).
- Core, notebook, storage: `src/planner/conversation/` (`system.py`,
  `events.py`, `storage.py`); tables land in
  `src/planner/core/migrations/versions/conversation_system_tables.py`.
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
  `tests/unit/test_conversation_conformance.py`.

## Handoffs

- **Worker orchestration** (`worker-orchestration.md`) sends each Worker step into
  the Ticket's conversation.
- **The front end** (`frontend.md`) renders the shared pane and its Workspace marks.

## Deferred

- **Held-line durability**: if losing the in-memory waiting line on a restart
  ever bites, the line can be made durable without changing the contract.
- **Error envelope**: the conversation routes speak plain HTTP errors, not the
  planner's error envelope. Trigger: one error contract is adopted across the API.

_Last verified: 2026-09-04._
