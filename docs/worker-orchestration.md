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
4. Its status is `empty`. This one fact carries most of the rule: a Ticket that is
   blocked, paired, waiting for approval, asking for help, held by the user, already
   out with a worker, or errored is by definition not `empty`.
5. Nothing is already parked on that blank waiting for approval.
6. Scope permits work at the current ceiling.
7. If the blank is Closeout, no other Ticket in the same project-and-Worker-type lane
   is at Closeout and not resting. One poll sends at most one Ticket into each free
   lane.

Then one more question that the record cannot answer: **is this Ticket's worker busy
right now?** The conversation system is asked directly, and a busy worker is left alone
for this pass.

If everything says yes, Panels takes the Ticket out of `empty` in a single guarded
write — to `agent` for a worker-owned Stage, to `paired` for a paired one. That flip
**is** the claim. There is no claim stamp and no separate run record. The readiness
questions are all asked again inside that write, so two racers both re-check under the
same lock and only one of them writes.

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

Giving a claim back checks both halves of what it took: the status it wrote **and** the
moment it wrote it. Comparing the status alone would let a late release erase a
later, legitimate move that happened to land on the same word.

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

## Sending a proposal back

When the owner returns a proposal for revision, the guidance goes to the worker as a
real message in the same conversation, and the Ticket goes back out to it.

The order is: check everything, send, and only then write. It has to be that way round,
because the write is the one part that cannot be undone honestly — it deletes the
pending proposal, so undoing a failed send afterwards would leave nothing to approve. A
refused delivery changes nothing at all and comes back as an error the owner can retry
cleanly. The one residue is a send that succeeded and a write that then failed: the
guidance is out and the proposal is intact, so a retry may deliver the same guidance
twice. Visible, harmless, and far better than losing the proposal.

_Code path:_ `src/planner/tickets/actions.py`.

## The seam: one conversation contract

Everything above talks to the conversation system through one small contract: start a
conversation, send into it, ask whether it is running, ask whether it is waiting on a
permission, kill it. Nothing here knows what a backend is, what ACP is, or what a
session id looks like.

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

## Deferred

- **Retry after an errored Ticket.** An errored Ticket still needs a deliberate way
  back. Trigger: a product decision about what retry should mean.

---

_Last verified: 2026-08-10._
