# Worker orchestration

Each Ticket has one active conversation, and one worker on the other end of it. A Ticket
also retains its past conversations. This system decides when the active worker receives
the Ticket's next step, and sends that step.
That is the whole job. It does not watch the worker, wait for it to finish, or settle
anything afterwards. A Ticket moves again only when someone files or decides a proposal.

```
the readiness loop                      one Ticket's start
──────────────────                      ──────────────────
today's Tickets, oldest first           is the worker busy? → leave it alone
    │                                   take the Ticket out of "empty"
    │  ask of each: is it ready?        start a conversation if it has none
    └──────────────────────────────►    write the opening message and send it
         one independent start          started or queued? → done
         per ready Ticket               refused? → give the claim back
    ▲
    │  a timer, and any committed write
```

## Deciding, then claiming

The loop only ever looks at Tickets on today's planning day. A Ticket is ready when
all of these hold right now:

1. It is on today's planning day.
2. Its Stage is not terminal and has a next blank to fill.
3. It reads as `empty`. That one answer keeps blocked work, parked approvals, active
   claims, and errors out of the runnable set. A parked proposal is not asked about
   separately: a Ticket with one reads as `awaiting_approval`, so rule 3 already refuses
   it.
4. If the Stage's owner is the user, its one opening turn has not run yet. The opener
   fact belongs to that Stage entry, so a later entry gets its own turn.
5. If the blank is Consequences, no other Ticket in the same project-and-Worker-type
   lane is at Consequences and not resting. One poll sends at most one Ticket into
   each free lane.

The ceiling is not one of these questions. It decides what a finished step does with its
answer, not whether the step may run.

Then one more question that the record cannot answer: **is this Ticket's worker busy
right now?** The conversation system is asked directly, and a busy worker is left alone
for this pass.

If everything says yes, Panels takes the Ticket's worker-step claim in one guarded
write. That claim is the only state of control Panels stores, and it is why the Ticket
then reads as `agent`. There is no separate claim stamp or run record. For a
user-owned Stage, the same transaction records a tentative opener fact for that Stage entry.
Readiness checks that fact, not the Ticket status, to prevent a second opener. A refused
send removes the fact. An accepted send keeps it and returns the Ticket to `empty`.
The canonical Stage writer clears the fact when the Ticket leaves that Stage. A later
Stage entry can therefore receive its own opener without a database trigger.
All readiness questions run again inside the write, so only one racer wins the claim.

Day membership is one of those questions, and it is asked at the start of a worker step
and again inside the claim. Nothing reads it afterwards to continue, re-wake, or end
that turn: a supervisor restart reads it once more, only to explain why a start produced
nothing. Taking a Ticket off today
therefore stops its next wake, and it does not stop the turn already in flight. That
turn ends on its own, and the Ticket moves again only when someone acts on it.

Checking too often costs nothing: the check reads and decides, and writes nothing. So
the loop does not wait out its timer. Every write committed through the database door
announces itself — the announcement says nothing about what changed — and the loop
answers by asking all of it again. The timer and the database stay the canonical
answer, so a lost announcement cannot lose work.

_Code paths:_ `src/planner/runtime/worker_step_readiness.py`,
`src/planner/runtime/worker_step_readiness_loop.py`,
`src/planner/core/change_signal.py`, and the claim and release writers in
`src/planner/tickets/data.py`.

## Sprint Item manager wakes

Proposals routed to a Sprint Item manager and explicit worker-error transitions create
durable wakes in the same transaction as their source event. Proposal routing remains a
separate deterministic decision before wake creation. It contains no TypeSafe call.

The manager wake loop shares the background-loop machine lock. A commit wakes it, and a
periodic poll recovers missed signals and restart state. The loop groups open wakes for one
manager into an immutable batch. It sends that batch through the normal supervisor
conversation boundary in queue mode.

A queued batch remains open while it waits behind active work. The exact durable prompt
event closes its member wakes. A definite refusal or discard creates a later attempt with a
new sender message identity. An uncertain delivery remains unresolved and is not replayed
automatically. Supervisor reset and deletion share a lifecycle lock with delivery.

_Code paths:_ `src/planner/manager_wakes/`, `src/planner/core/loops.py`, and the proposal
and worker-error writers in `src/planner/tickets/data.py`.

## Scheduled Ticket handoff

The scheduled-Ticket loop shares the server lifespan and single-machine lock, but not
this system's job. Scheduling stops after ordinary Ticket creation and Day placement.
That commit emits the change signal, and readiness applies the same rules as it does to
any other Ticket. There is no schedule-to-Worker shortcut.

See **Scheduled Ticket creation** (`scheduled-tickets.md`) for schedule configuration,
occurrences, and suppression.

## Sending the step

The opening message is composed first as one ordered, inspectable list of Panels inputs.
It carries only what a worker cannot get for itself: a notice when the worker's context
was compacted, the Stage instruction, and revision feedback for the current Stage when
there is some. The Stage instruction names the command that reads the Ticket, because
the Ticket's Brief and guidance are a read the worker makes rather than a payload every
message carries. Worker skills and conversation history stay separate from this list. It
is composed before anything else so that a failure here cannot leave a conversation
behind.

## Telling a worker it lost its memory

Panels compacts a worker that has been idle for fifty minutes, and a backend may compact
one that fills its context. Either way the conversation the worker was reading gets
shorter and nothing in it says so. The conversation record keeps the boundary, so Panels
is the only side that can report it.

A boundary counts as answered once Panels has sent a worker-step message after it. That
is how the same boundary is never reported twice, and it needs no second record of its
own. The boundary is read at two moments:

- A step falls due. The wake leads with the notice, and the Stage instruction follows.
- A step is already in flight — the Ticket's worker-step claim is out. The notice is sent
  straight away, because a worker part-way through a step is not waiting for a wake, and
  most compactions are never followed by another one.

_Code path:_ `src/planner/runtime/worker_memory.py`.

Then it is sent, through the one door there is. If the Ticket has a conversation the
message goes into it. If it has none, the message is what brings one into being — and
what it should run on is resolved from three layers in order: the Worker type's launch
defaults, the Ticket's own last-chosen values, then anything the sender explicitly asked
for. The backend, model, and reasoning effort resolve as a unit, because a model name
means nothing to a backend that has never heard of it. The conversation is created first.
One guarded transaction points the Ticket at it and records its durable association. If
another start wins that pointer race, the losing conversation gets no association.

Making a conversation and saying the first thing in it are one act. If the message does
not land, the conversation goes with it and the Ticket is left with none — which is
exactly what the next attempt wants to find. The refusal or exception also removes that
start's exact provisional association. Reset differs: it clears the active pointer but
keeps the association and transcript in the Ticket's history.

The conversation system reports one of three fates:

- **Started** — it is running now.
- **Queued** — the worker was busy, so the message is held and will run when it is
  free. This counts as delivered: the exact revision feedback batch is removed and the
  step is done being started.
- **Refused** — nothing was delivered. The claim is given back and one line is logged.

Giving a claim back checks the claim it took and the revision of that claim change.
Every actual change advances the revision, even when two changes share a second. An
old release therefore cannot erase a newer claim that looks the same.

Once a send reports started or queued, it cannot be taken back, so nothing after that
point reverts. A failure to remove delivered revision feedback is logged and left alone;
reverting would only re-arm the Ticket to send the same thing twice.

One slow backend must not hold up every other Ticket, so each Ticket's start runs as
its own independent piece of work rather than in a queue behind the others. On
shutdown the loop stops polling, waits out the starts already in flight until the
deadline, and abandons whatever is left.

_Code paths:_ `src/planner/runtime/conversation_start.py`,
`src/planner/runtime/logic/conversation_start_resolution.py`,
`src/planner/runtime/logic/worker_step_prompt.py`, and
`src/planner/tickets/revision_feedback.py`.

## A claim is not liveness, and Panels says so

A Ticket's claim says that Panels sent its worker a step. Whether a worker is actually
running is the conversation system's fact, and it is asked for it every time it
matters. The two can disagree — a process that dies mid-flight leaves a Ticket reading
`agent` with nothing running.

That is the honest record, and there is no machinery that pretends otherwise: no
recovery sweep at startup, no stranded-run cleanup, no correctness table to reconcile.
The owner sees a Ticket that is not moving and picks it up. This is a deliberate choice
in favour of one true answer over a second bookkeeping system that can itself be wrong.

Picking it up is a real action rather than a repair. `ticket restart-worker` clears the
dead conversation, gives the claim back, and starts the step again. Both halves happen
together because either one alone leaves the Ticket stuck: a Ticket with no conversation
still reads as claimed, and a Ticket with its claim back but still pointing at a dead
conversation would talk into it.

Anyone standing above the Ticket can do it, which is Khushal, the Chief, or the Ticket's
own Outcome. Khushal could not before: no ordinary route restarted a Worker, and the
only door was the Outcome's. This is a new capability on his surface, not a rename.

Nothing there asks whether the old Worker was alive, because nothing can answer. A
Worker that dies without ending its turn goes on looking like one that is running, so a
check on that would refuse exactly the Tickets that need recovering. Whoever restarts
reads the conversation and decides. The action still requires the Ticket to be the
current child, a Worker-owned Stage, and a worker step that is out. These checks define
whether a restart is meaningful. The action does not add a delay or a bound for a caller
that restarts repeatedly.

The Ticket's launch configuration is frozen while its conversation holds it, so
`employee-configuration` returns `already_running` in that state. `restart-worker` is the
route that releases the old step and applies a new backend, model, or reasoning effort
before the next Worker starts.

One narrow recovery also covers Tickets stranded by an older rejection. It requires a
Worker-owned Stage, Empty status, no claim, an existing conversation, and pending revision
feedback for that Stage. Restart adds that Ticket to the current Day and starts normal
readiness without resetting its conversation. It clears stale running and queued traffic
first, then sends the revision into the same conversation history. Other prior-Day Tickets
remain at rest. This recovery preserves the launch configuration, so it refuses restart
options that name a backend, model, or reasoning effort.

## Sending a proposal back

When the holder returns a proposal for revision, one Ticket transaction checks every
authorization and current-parent route. It clears the proposal, appends the exact comment
to a separate attributed revision-feedback record, and returns the Ticket to its resting
status. If the rejected Stage belongs to the Worker, the same transaction adds the Ticket
to the current Day. Its commit wakes normal readiness, which reuses the existing
conversation. A same-Stage user opener is cleared so the discussion can open again. The
next normal worker-step prompt carries feedback for that Stage, and only a successful
send consumes it. Ticket guidance is not sent with it, and the worker reads that off the
Ticket. Reply bookkeeping credits the source
turn after the commit and cannot undo the rejection.

_Code path:_ `src/planner/tickets/actions.py`.

## The seam: one conversation contract

Everything above talks to the conversation system through one small contract: start a
conversation, send into it, ask whether it is running, ask whether it is waiting on a
permission, kill it. Nothing here knows what a backend is, what ACP is, or what a session
id looks like.

The system behind that contract is the real one, and the only one. The same object the
browser's conversation pane uses is the object a worker step sends into, so a Ticket's
automatic work and the person typing to it are in one conversation rather than two that
happen to be started the same way. A test that wants to hold a conversation still passes
an in-memory implementation of the same contract; production never does.

_Code paths:_ `src/planner/conversation/contracts.py`,
`src/planner/conversation/system.py`, and `src/planner/core/server.py`.

## Worker roles and backends

The base role is `panels-worker`. It reads the Ticket's Worker type, loads that type's
specialist skill, and uses the `panels` command-line tool to inspect the Ticket and
file proposals. Panels exposes the repository's role skills through each backend's
native skill location: project links for Codex and Claude Code, and startup links in
the configured planner Hermes home.

The production catalog is exactly `hermes`, `codex`, and `claude`. A new Ticket worker
runs in its Project folder when that path names an existing directory. Otherwise, it
runs in `~/projects` when that folder exists, and in the Panels repository otherwise.
Startup creates no folders. Existing conversations keep their stored workspace folder.

The Ticket's stored backend, model, and reasoning effort are its **last-chosen**
values, kept up to date with what its conversation actually runs on, so a fresh
conversation starts from where the last one ended. Managed Worker and Chief settings
own the defaults a Ticket starts from, and the Workers screen edits them.

Those three values can be changed while the Ticket names no conversation and has no
worker step out. That is a Ticket nobody has started yet, or one whose conversation was
just cleared. At any other moment they are frozen, because they are what a live
conversation was started on. Changing them is one act, never one value on its own: a
model name belongs to the backend that named it.

Panels keeps every distinct byte sequence for each managed skill in SQLite. A SHA-256
content hash reuses an existing version when an edit returns to the same content. Startup
records all current managed skills before any worker loop starts.

Each automatic worker-step message gets a sender message ID. Before the send, Panels binds
that ID to the current `panels`, `panels-worker`, and specialist versions. The binding stays
provisional while a prompt waits in the process-local queue. The conversation record makes
it final in the same transaction that stores the prompt event. A refusal or discard event
removes it. Startup resolves any provisional binding from its durable prompt outcome and
expires one with no outcome, because the old process queue no longer exists.

The sender message ID links these records without a second run tracker. The prompt row keeps
the Ticket and Stage instruction that the worker received. This version store has no API or
browser reader yet.

_Code paths:_ `src/planner/worker_types/`, `src/planner/worker_settings/`,
`src/planner/skill_versions.py`, and `src/planner/skills/`.

## Handoffs

- **Worker types** (`worker-types.md`) declares Stages, ownership, and the
  specialist skill.
- **Tickets & the gates** (`tickets-and-gates.md`) owns proposals, the ceiling,
  approval, and Ticket status.
- **The conversation system** (`conversation-system.md`) owns the pane the human types
  into, and the conversation the step is sent into.
- **The front end** (`frontend.md`) owns the row marks these signals feed.
- **The command-line tool** (`cli.md`) is the surface the worker acts through.

An errored worker-owned Ticket remains errored through reads and owner replies. A
successful start supersedes the failed turn in derived agent state. Explicit restart
clears the error before a new start.

---

_Last verified: 2026-09-21._
