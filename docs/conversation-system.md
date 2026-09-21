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

Reading is one rule everywhere: fetch the rows after the last one you hold, then
listen for new ones. Opening a conversation, reconnecting after a dropped
connection, and a second device are all that same fetch. Nothing re-downloads
mid-read.

A browser with no saved lens choice opens through the Focus lens. Focus shows the
owner's prompts, explicit messages addressed to the owner, permission requests, agent questions, and
the answers that settle those requests. Historical owner prompts without principals use
their established owner label, so they remain readable without a record migration.
Failed turns, stopped turns, and missing explicit replies remain visible as compact
system rows. Complete turn boundaries still
settle the Focus thread and rest line when runtime rows are hidden. A turn with a hidden
opening prompt has no Focus turn head.
Full shows the complete runtime notebook, every held prompt, all live agent text, and
every tool call without a Focus fold. The header toggle and the unmodified `f` key switch
the lens without replacing the conversation. Editable controls keep the key. The browser
keeps the choice across conversation switches, new conversations, and later visits.

The lens changes only what the person reads. Liveness, streaming, reconnects, and
snapshots continue to use the complete feed. A switch to another conversation invalidates
the old read and tail. A late snapshot, row, frame, or refresh from the old conversation
cannot change the newly opened conversation.

While an active lens is open, the owner read position advances through the newest delivered
row. Runtime-only rows can clear an unread mark because each lens displays every result that
needs the owner's attention.

An open conversation is handed each new row directly, so it never has to be told to
come and look. That is why most rows are written quietly: a historical agent-message row, a tool
call starting or finishing, a plan, a token count, and a compaction are shown only
inside the conversation. Writing a runtime-only row does not send every other open screen
back for a fresh copy of itself. A working agent writes dozens of those rows a minute, and
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
same send operation. It accepts the owner, Chief, Ticket, or Sprint Item principal. An
employee-to-owner send appends an addressed `message_to_owner` row to the employee's
current conversation without invoking a backend. An employee without a current
conversation cannot send to the owner. Employee recipients use their normal current
conversation path, creating it when any send needs one. Every mode starts a turn when the
recipient is idle. Agent keys remain a private resolution detail. The command adds no
second transport, queue, or conversation record.

Every employee-authored send carries canonical sender and recipient principals derived
from the authenticated request. Panels records that address on every durable outcome,
including held, refused, uncertain, and discarded prompts. Browser-supplied display
labels are not authority.

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

The unlinked development conversation page keeps a lower-level raw send route for testing
conversation mechanics in isolation. It rejects every conversation associated with a
Ticket, the Chief, or a Sprint Item supervisor. It therefore is not an employee
conversation door and does not participate in principal addressing. Ticket, Chief, and
Sprint Item composers never use it; they all use the addressed Send Message operation.

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

Claude Stop sends one interrupt that also cancels queued UUIDs. Panels waits for the
provider receipt, verifies that no owned UUID remains queued, and waits for the active
command result. If any proof is absent, Panels discards the child. The next prompt resumes
the stored session without replay of the steer.

A send may also carry a model or reasoning-effort change. The change rides the
message: browsing a picker does nothing, the change lands when the message is
delivered, a waiting message applies it when it runs, and a refused delivery
changes nothing. Codex and hermes take the change in place; claude is restarted
under the same conversation with its memory carried over.

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

A send waits three minutes for an answer. Past that nothing is coming, and the
copy says so. Three minutes is long on purpose: the biggest message this system
takes is around eighteen megabytes once its files and pictures are encoded, which
is minutes of uploading on a poor connection before the server can answer at all.
Waiting too long costs almost nothing, because a message the record turns out to
have disappears from the screen as soon as the record is read again. Waiting too
little would put a frightening sentence on a message that was about to turn out
fine.

A message nobody answered for does not stay in the thread. The thread draws these
copies after every row the conversation has, so one left there would sit under
every later turn for as long as the tab stayed open, looking like the newest thing
said. It moves up to the composer, with the other messages that have not landed,
and it says that no answer came. Two things can be done with it there, and both
belong to the browser rather than the record: stop showing it, or send the same
words again. Sending again is a new message with a new name, so if the first one
did arrive after all there are two — that is the person's call to make, and
nothing guesses it for them.

None of that is a guess about whether the message arrived, and the question stays
open until the record answers it. Most of the time the record already has the
answer and this browser simply missed it, so a conversation holding a send nobody
answered for reads the record again the moment the server is reachable. If the row
is there, the copy goes, the ordinary way.

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

Panels keeps the last successful usage reading for each backend in its database.
Opening the Backends page, reading `GET /backends`, receiving a change signal, or
refreshing another query only reads that stored answer. None of those actions contacts
a provider. The one Refresh on the Backends page starts every usage source through
`POST /backends/refresh`, then resolves those answers against ordinary backend snapshots.
It does not force model catalogues, versions, identities, or update advice to refresh.
Codex and Claude usage reads start independently, so catalogue work cannot delay them and
one provider failure does not discard the other provider's new answer. A failed refresh
also leaves that backend's prior reading in place. When the Backends page opens, it paints
an ordinary snapshot first. It then refreshes catalogue and Codex and Claude update
advice in the background. Cached ordinary reads stay available during that work. The
page's Refresh action and the shared model picker stop after the usage answer.

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

_Last verified: 2026-09-21._
