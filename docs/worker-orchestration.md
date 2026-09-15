# Worker orchestration

Each Ticket has one active conversation, and one worker on the other end of it. A Ticket
also retains its past conversations. This system decides when the active worker receives
the Ticket's next step, and sends that step.
That is the whole job. It does not watch the worker, wait for it to finish, or settle
anything afterwards — a Ticket moves again only when someone acts on it: a proposal
filed, an approval given, a take-over.

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
3. The Stage's owner is not the user.
4. Its status is `empty`. This fact keeps blocked work, parked approvals, active claims,
   and errors out of the runnable set. User-owned Stages are already excluded by rule 3.
5. Nothing is already parked on that blank waiting for approval.
6. Scope permits work at the current ceiling.
7. If the blank is Closeout, no other Ticket in the same project-and-Worker-type lane
   is at Closeout and not resting. One poll sends at most one Ticket into each free
   lane.

Then one more question that the record cannot answer: **is this Ticket's worker busy
right now?** The conversation system is asked directly, and a busy worker is left alone
for this pass.

If everything says yes, Panels takes the Ticket from `empty` to `agent` in one guarded
write. That flip **is** the claim. There is no claim stamp or general run record. For a
paired Stage, the same transaction records a tentative opener fact for that Stage entry.
Readiness checks that fact, not the Ticket status, to prevent a second opener. A refused
send removes the fact. An accepted send keeps it and returns the Ticket to `empty`.
Leaving the Stage clears the fact, so a later Stage entry can receive its own opener.
All readiness questions run again inside the write, so only one racer wins the claim.

Checking too often costs nothing: the check reads and decides, and writes nothing. So
the loop does not wait out its timer. Every write committed through the database door
announces itself — the announcement says nothing about what changed — and the loop
answers by asking all of it again. The timer and the database stay the canonical
answer, so a lost announcement cannot lose work.

_Code paths:_ `src/planner/runtime/worker_step_readiness.py`,
`src/planner/runtime/worker_step_readiness_loop.py`,
`src/planner/core/change_signal.py`, and the claim and release writers in
`src/planner/tickets/data.py`.

## Scheduled Ticket handoff

The scheduled-Ticket loop shares the server lifespan and single-machine lock, but not
this system's job. Scheduling stops after ordinary Ticket creation and Day placement.
That commit emits the change signal, and readiness applies the same rules as it does to
any other Ticket. There is no schedule-to-Worker shortcut.

See **Scheduled Ticket creation** (`scheduled-tickets.md`) for schedule configuration,
occurrences, and suppression.

## Sending the step

The opening message is written first: a short instruction naming the Ticket, its Stage,
and the blank to fill, plus any worker context that was waiting to be delivered. The
worker sees that context because it is in the actual message — never because Panels
wrote a row somewhere. It is written before anything else so that a failure here cannot
leave a conversation behind.

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
  free. This counts as delivered: the waiting context is ticked off and the step is
  done being started.
- **Refused** — nothing was delivered. The claim is given back and one line is logged.

Giving a claim back checks the status it wrote and the revision of that status change.
Every actual change advances the revision, even when two changes share a second. An
old release therefore cannot erase a newer claim that happens to use the same status.

Once a send reports started or queued, it cannot be taken back, so nothing after that
point reverts. A failure to tick off the delivered context there is logged and left
alone; reverting would only re-arm the Ticket to send the same thing twice.

One slow backend must not hold up every other Ticket, so each Ticket's start runs as
its own independent piece of work rather than in a queue behind the others. On
shutdown the loop stops polling, waits out the starts already in flight until the
deadline, and abandons whatever is left.

_Code paths:_ `src/planner/runtime/conversation_start.py`,
`src/planner/runtime/logic/conversation_start_resolution.py`,
`src/planner/runtime/logic/worker_step_prompt.py`, and
`src/planner/worker_context/`.

## Status is not liveness, and Panels says so

A Ticket's status says what Panels last decided about it. Whether a worker is actually
running is the conversation system's fact, and it is asked for it every time it
matters. The two can disagree — a process that dies mid-flight leaves a Ticket sitting
at `agent` with nothing running.

That is the honest record, and there is no machinery that pretends otherwise: no
recovery sweep at startup, no stranded-run cleanup, no correctness table to reconcile.
The owner sees a Ticket that is not moving and picks it up. This is a deliberate choice
in favour of one true answer over a second bookkeeping system that can itself be wrong.

Picking it up is a real action rather than a repair. `sprint item supervisor
restart-worker` clears the dead conversation, gives the claim back, and starts the step
again, so a Sprint Item supervisor can recover its own child Ticket without the user.
Both halves happen together because either one alone leaves the Ticket stuck: a Ticket
with no conversation still reads as claimed, and a Ticket at `empty` still pointing at a
dead conversation would talk into it.

Nothing there asks whether the old Worker was alive, because nothing can answer. A
Worker that dies without ending its turn goes on looking like one that is running, so a
check on that would refuse exactly the Tickets that need recovering. The supervisor
reads the conversation and decides, and the rules around the action bound what that
decision can reach: its own Item, a Worker-owned Stage, and a worker step that has
already had five minutes.

## Sending a proposal back

When the holder returns a proposal for revision, one Ticket transaction checks every
authorization and current-parent route. It commits the decision and two ordered outbox
records: a Panels lifecycle fact, then the decider's separately attributed comment.
The transaction performs no backend I/O.

The machine-lock-owned delivery loop sends those records after the commit. Durable
sender identities preserve transcript order and prevent duplicates across retries and
process restarts. A refusal leaves the message pending for retry. An accepted prompt
with a failed transcript write becomes terminal `uncertain`, so Panels never sends it
twice. The conversation shows that uncertain attempt, and later rejection messages
continue in order. If the runtime row fails too, the same recovery loop recreates that
row from the durable rejection record without another backend send. Reply bookkeeping
credits the source turn after the commit and cannot undo the rejection.

_Code path:_ `src/planner/tickets/actions.py`.

## Waking a non-owner proposal holder

Filing a parked proposal commits a durable wake row and returns without backend I/O.
Only the machine-lock-owned loop claims that row as `delivering`. A decision,
replacement, or deletion cancels or supersedes any undelivered wake in its own database
transaction. A wake that already reached the wire is harmless because the holder reads
canonical Ticket state. Definite refusal advances the
attempt identity and returns the row to `pending`. A queued prompt stays `delivering`
because that queue is process-local; the loop probes the same sender identity until the
conversation reports durable delivery.

The proposal-holder wake loop shares the process machine lock and server event loop with
the other reconcilers. Database change signals wake it promptly, while its periodic tick
is the retry backstop. It schedules at most one delivery per Ticket at a time. After it
owns the machine lock, startup returns crash-abandoned `delivering` rows to `pending`
without changing their attempt identity. A second server therefore cannot reset a live
delivery claim. Conversation idempotency either discovers the earlier success or safely
recreates a lost queue. Shutdown retains the lock if a delivery does not settle before
the deadline or a durable `delivering` row still represents a process-local queue.
Process exit then releases the lock. A post-wire transcript failure becomes terminal
`uncertain`; it is visible for repair and never retried automatically.

_Code paths:_ `src/planner/proposal_holder_wakes/`, `src/planner/core/loops.py`.

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

- **Worker types** (`worker-types.md`) declares Stages, default ownership, and the
  specialist skill.
- **Tickets & the gates** (`tickets-and-gates.md`) owns proposals, scope, approval,
  and Ticket status.
- **The conversation system** (`conversation-system.md`) owns the pane the human types
  into, and the conversation the step is sent into.
- **The front end** (`frontend.md`) owns the row marks these signals feed.
- **The command-line tool** (`cli.md`) is the surface the worker acts through.

An errored worker-owned Ticket remains errored through reads and owner replies. A
successful start supersedes the failed turn in derived agent state. A Sprint Item
supervisor can also use explicit restart, which clears the error before a new start.

---

_Last verified: 2026-08-10._
