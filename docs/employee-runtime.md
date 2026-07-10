# The employee runtime

Each ticket has a single worker that carries it forward — think of it as an
**employee** you have handed that one piece of work to. Behind the scenes it is a
live AI session (a Hermes "mind") that stays with the ticket across its stages. You
are its manager: you talk to it in the ticket's chat, you set how far it may go (its
scope), and you approve its work. It only ever _proposes_ — nothing it writes becomes
real until it is accepted, automatically within the room you granted or by you in
person.

Around each employee runs a small loop that decides _when_ to make it act, runs one
step at a time, and feeds an approval straight back in.

```
   System A (the poll)                     System B (one step)
   ───────────────────                     ───────────────────
   scan today's tickets for ones           use the shared gateway · resume the
   READY to move                           ticket's mind · submit one prompt
        │  fire ─────────────────────────► watch the single run end
        │                                  write the ticket's status (the one door)
        ▼                                          │
   an approval / unblock, or a finished           │  a proposal, parked or applied
   step, POKES the poll at once  ◄────────────────┘
   (a timer is only the backstop)
```

## The loop: System A and System B

**System A** polls today's tickets and picks the ones that are _ready_ — able to
move and not already in flight — then fires the employee for each. It never touches
the AI itself. **System B** runs one step through the shared persistent Hermes
gateway child: it assembles the prompt, resumes (or creates) the ticket's durable
session, submits one turn, and watches for the single run to end — then writes the
runtime status through the ticket data writers. Proposals, approvals, takeover,
release, and runtime start/finish/error all use those same writer functions, so
"the code owns the state, the worker only proposes" holds even here.

One employee is one ticket session, so Hermes' per-session busy guard keeps one turn
in flight for that ticket while the shared child can hold many sessions. An approval
or an unblock from the web page pokes the poll immediately so the ticket advances
the moment you act; adding a ticket to today's day list does the same. The timer is
only a backstop. A ticket that is not on today is outside the automatic run set even
when its status is `empty`; it does not appear on the Board, and the ticket page
shows this as `auto not on today`.

_Code paths:_ `src/planner/runtime/system_a.py`, `src/planner/runtime/system_b.py`,
`src/planner/minds/` (the employee primitive: the shared gateway child and its
per-session busy guard).

## Talking to the employee, and running skills

You reach the employee through the ticket's chat, and you can run a skill into it
from the slash menu — both go to the same live worker over the same gateway. Those
are their own system; see `chat.md`.

### Pending context

Panels keeps a small keyed set of facts that an employee must receive with its next
model-bound turn. Repeating the same fact refreshes one key instead of creating a
queue of duplicate messages. For example, a human ticket edit sets `ticket_changed`,
which tells the employee to reread the ticket.

The shared worker gateway adds every pending item immediately before it submits the
next Hermes prompt. This covers an automatic step, a human message, and a model-backed
slash command. Commands that do not submit a prompt do not consume anything. After
Hermes accepts the prompt, Panels acknowledges the exact key revisions it sent. A
failed or busy submission keeps them pending, and a newer revision written during a
send cannot be erased by the older acknowledgement.

Panels chat rows and event rows remain display and audit records. They are not this
delivery mechanism. The generic storage, contracts, and composition live in
`src/planner/worker_context/`; ticket-specific keys live with the ticket domain.

## When a run fails: errors in the event log

When an employee's run goes wrong, the ticket's history tells you why. A failed run
used to show only a bare "errored"; now the event log carries the actual reason —
for example, that a skill the worker needed was not installed — marked in amber so it
stands out, with the full message allowed to wrap so nothing is cut off. That turns a
stuck ticket from a mystery into something you can debug.

## What is proved

The runtime now points at the **`panels-worker`** role skill, and the CLI entry point
workers use is **`panels`**. The shared gateway can use `PLAN_HERMES_HOME` so it sees
the same skills and credentials as the configured Hermes home. On startup the server
links this repo's `panels` and `panels-worker` skills into that home, so the role
skills are present even when the default dedicated home starts empty. Provider/model
credentials are still home configuration: the dedicated home needs its own `.env` and
`config.yaml` links or files before a live worker can initialize. Ticket chat has
been smoked against a non-test server and reached the real Hermes worker.

The full live worker loop has also been smoked against fresh non-test databases.
A ticket placed on today was picked up by System A, run by System B, and parked at
`awaiting_approval` after the employee filed a proposal. The same durable Hermes
session history showed the System B prompt and worker reply in ticket chat.

A second smoke used a long poll interval to prove that a settled success-condition
edit is a wake event, not just something the timer eventually notices: editing the
success value poked System A and the employee filed the next approach proposal.
System B persists a created or resumed `chat_session_key` before submitting the
prompt, so a worker calling `panels worker my-ticket` during its own turn can resolve
the current ticket immediately.

A live smoke on the default DB also proved that adding a new harmless ticket to
today now wakes System A without a follow-up scope edit. The ticket moved to
`agent_running_step` on the immediate read after the day add, then parked at
`awaiting_approval` with a success proposal.

## Handoffs

- **Tickets & the gates** (`tickets-and-gates.md`) — the proposals the employee
  files, the scope that decides whether a step auto-accepts, and the approval that
  pokes the poll.
- **Chat** (`chat.md`) — the live conversation with the employee and the slash menu
  of commands and skills, over the same gateway.
- **The command-line tool** (`cli.md`) — the surface the employee acts through.

## Deferred

- **No recovery from a failed run.** An errored ticket is stuck — there is no retry
  or clear. Trigger: recovery is designed and built.
- **Automatic daily rollover writing isn't wired.** The morning boundary materializes
  days, but no worker writes the overview yet. Trigger: the boundary-rebuild work
  lands. See `days.md`.
- **Chat is not queued behind an active worker step.** If you talk to the same
  employee while its worker step is already running, the send is rejected as
  `already_running`; history remains readable. The UI still shows thinking dots,
  then the full answer, rather than streaming token by token.
- **The "mind" → "employee" rename is unfinished** — some code still calls the
  employee a "mind" (`src/planner/minds/`, `MindQueue`).

---

_Last verified: 2026-07-08._
