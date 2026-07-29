# The conversation system

This is how Panels talks to an AI agent. One conversation = one agent
process (hermes, codex, or claude) working in a folder, plus a permanent notebook
of everything that happened in it. The rest of the planner can do exactly five
things to a conversation — start it, send a message into it, interrupt its running
turn, kill its activity outright, and ask whether it is running. It can also ask whether
the agent is waiting for a permission decision or answers to its questions.

It serves every screen that shows a conversation: a Ticket's, the Chief of
Staff's, and the development pane at `#/dev/conversation`. There is no second
one. The layer that came before it — a WebSocket, a session-binding table, a
per-ticket projection of what the agent was doing — is gone, along with the
second database, relay, neutral protocol and history adapter that preceded it.

```
  caller (pane, loop)                the conversation system                agent CLIs
  ───────────────────                ───────────────────────                ──────────
  start / send / interrupt   ──▶   one core: queue, turns, asks,   ──▶   hermes (ACP)
  kill / running / waiting?         notebook, janitor                     codex (app-server)
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

Reading is one rule everywhere: fetch the rows after the last one you hold, then
listen for new ones. Opening a conversation, reconnecting after a dropped
connection, and a second device are all that same fetch. Nothing re-downloads
mid-read.

## What a message is

A message is not a piece of text. It is a run of pieces, in the order they were
put in, and there are two kinds of piece: written words, and a picture.

There is deliberately no third kind. A voice note becomes words by speech-to-text
before anything reaches a message, so nothing here ever sees a sound. And a file
an agent wants you to look at is a markdown link in its own words, which already
draws as a preview — a separate kind for it would be a second, worse way to draw
the same thing.

Nearly every message is one piece of written words, and that stays as simple as
it sounds. A message that is only words is stored exactly the way it was before a
message could be anything else, so every conversation already in the notebook
reads unchanged and an ordinary row never grows.

The bytes of a picture do not go in the row. A notebook is read in
full every time somebody opens a conversation, and a screenshot inside one of
those rows would be megabytes re-read every time. So the bytes are kept in a file
beside the notebook, under the conversation that carries them, and the row names
the file. That one value serves everybody: the notebook holds it, the browser
fetches the picture from it, and the agent is handed it — codex wants the file's
path and is given exactly that, while claude and hermes want the bytes and they
are read from the same file.

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

Those files last as long as the notebook does, which is forever. Nothing in
Panels deletes a conversation: resetting one stops it and unlinks it, and
deleting a Ticket leaves its conversation behind. A file removed by either would
turn a picture somebody sent into a picture nobody can see, while the row still
says a picture was sent.

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
do it, none of them was asked to by Panels, and now each says so: codex sends it
as an item, claude as a system message, and hermes inside its own metadata on a
session update — the ACP protocol has no word for compaction at all, so hermes'
own `_meta` is the only place it appears. The thread draws it as a seam, in the
same stylesheet the pane that came before drew the same thing with.

## Sending

The composer accepts pictures from its image picker, the clipboard, or a drop. They
wait beside the draft in one visible order, can be removed one at a time, and can be
sent with words or as the whole message. The browser sends one native content run:
the trimmed words when there are any, followed by every remaining picture in the
order shown. There is no separate upload conversation or attachment record.

Send has one knob with three settings. The default runs the message when the
agent is free — if it is busy, the message waits in line. "Send now" makes the
message the running turn: a busy agent's current turn is stopped (recorded
honestly as interrupted) and the new message runs next, ahead of the line.
"Steer" injects text into the running turn without ending it — only hermes can
do that.

The answer to a send is the fate of that delivery, and fate means it happened:
started (the text reached a live agent), queued at a position, injected, or
refused with a named reason. The only refusals are genuine impossibilities — no
such conversation, the agent would not start, its session would not load, the
write failed, a steer with no running turn to join, or a steer at a backend that
cannot steer. A busy agent is never a refusal. A message with nothing in it is not a refusal
either — it is not a message, and it is turned away where it is sent. How a turn later ends is never
part of the answer — endings are notebook rows.

A message that is waiting can be taken back, by the name the sender gave it. It
has reached no agent, so taking it back reaches none either — it comes out of the
line and is written down as discarded, the same row a New writes for everything it
throws away, because text somebody handed over never disappears without a trace.
Being told there was nothing to take back is an ordinary answer: a waiting message
runs the moment the agent frees up, so the one you were looking at may already have
gone.

A send may also carry a model or reasoning-effort change. The change rides the
message: browsing a picker does nothing, the change lands when the message is
delivered, a waiting message applies it when it runs, and a refused delivery
changes nothing. Codex and hermes take the change in place; claude is restarted
under the same conversation with its memory carried over.

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
thread there and then, and empties the box — which stays typeable, with only the
send arrow saying anything is still in flight. Those two stamps travel with the
message and are kept on its row, so when the row comes back the browser knows it
for its own and simply stops drawing its copy; nothing is swapped and nothing
moves. If the message turns out to have got nowhere, the copy goes and its exact
words, pictures and pending run choices come back to the box, unless something
else has been composed there since. A
message the agent was too busy for stays in the thread and says it is waiting,
because nothing is answering it yet.

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

One child process per conversation, started only when a send needs it. The
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
kills the old conversation before starting fresh.

## Backend cards

The system can describe each agent CLI as a card: is the binary installed and
what version, who is logged in (where the CLI will say without a network call),
which models it offers, and whether a newer version exists — with a one-click
update whose command is inferred from how the CLI was installed, re-checked
afterwards, and reported as succeeded, unchanged, or failed. All of it is
advisory; nothing blocks on it. Logging in stays in the terminal, and the card
names the command.

## The commands an agent takes

Each agent has its own commands — the things you type at it starting with a
slash. They belong to the agent, not to us: the agent says what it takes, the
composer offers that list and narrows it as you type, and the command you pick
goes into the message as ordinary text. The agent reads its own name back out of
that text. Nothing on our side interprets a command or acts on one, and we do not
add commands of our own to the list or leave any of the agent's out.

The three agents answer differently, and all three answers are true. Hermes
volunteers its list as soon as a session starts, unasked, and may send a fresh one
later. Claude has its list in the handshake its process gives when it connects,
which is why the list follows the conversation's folder — a project can keep
commands of its own. Codex's wire has no notion of a typed command at all; its
slash commands live inside its own terminal program, so a codex conversation has
none to offer, and the menu says so rather than sitting there empty.

The last list an agent reported is kept on the conversation, so the menu still
works when nothing is running — which is exactly when you are likely to be writing
the first message. A conversation nobody has reported for yet offers nothing, and
that is honest: until an agent has been up once, nothing has said what it takes.

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
- The pane: `web/src/routes/DevConversationRoute.svelte`,
  `web/src/components/conversation/`, `web/src/lib/conversation/`.
- The contract's proof: `tests/support/conversation_contract_conformance.py`,
  run against the real system in
  `tests/unit/test_conversation_conformance.py`.

## Handoffs

- Ticket-side surfacing (which ticket needs you, row dots) is the worker
  orchestration's job, built against this contract: `worker-orchestration.md`.

## Deferred

- **Swap**: production screens and the worker loop move onto this system when
  the program rules the swap; the old layer and this doc's "dev pane" framing
  are corrected in the same breath.
- **Held-line durability**: if losing the in-memory waiting line on a restart
  ever bites, the line can be made durable without changing the contract.
- **Error envelope**: the conversation routes speak plain HTTP errors, not the
  planner's error envelope; unify when the swap wires production screens.

_Last verified: 2026-07-25._
