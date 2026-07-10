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
   TicketReadinessLoop                     EmployeeStepRunner
   ───────────────────                     ──────────────────
   scan today's tickets for ones           use the shared gateway · resume the
   READY to move                           ticket's mind · submit one prompt
        │  fire ─────────────────────────► watch the single run end
        │                                  write the ticket's status (the one door)
        ▼                                          │
   an approval / unblock, or a finished           │  a proposal, parked or applied
   step, RINGS a best-effort doorbell ◄───────────┘
   (SQLite + the timer remain the backstop)
```

## Discovery and execution

**TicketReadinessLoop** polls today's tickets and picks the ones that are _ready_ —
able to move and not already in flight — then passes each Ticket id to the employee
runner. It never touches the AI or writes Ticket state. **EmployeeStepRunner** owns
one step through the shared persistent Hermes gateway child: it rechecks readiness,
claims the Ticket, assembles the prompt, resumes or creates the Ticket's durable
session, submits one turn, and watches for the run to end. It then writes runtime
status through the Ticket data writers. Proposals, approvals, takeover, release,
and runtime start/finish/error all use those same writer functions, so
"the code owns the state, the worker only proposes" holds even here.

The runner exists whenever the worker gateway exists. Readiness polling is optional:
it may be disabled or another process may own the polling lock. Returning Review work
for revision therefore reserves a runner thread directly before changing the Ticket,
then releases that handoff after commit. The revision resumes the stored Hermes session
strictly. If that session is stale, Panels records an errored employee turn instead of
silently creating a different conversation.

One employee is one ticket session, so Hermes' per-session busy guard keeps one turn
in flight for that ticket while the shared employee-role child can hold many sessions.
The Ticket keeps its durable Hermes session key across stages. The lightweight Panels
listener for that session may detach after Hermes is observed idle and no accepted
operation remains; reopening the employee resumes the stored session. If delivery is
unknown, Panels records that honest outcome and never retries the employee prompt
automatically. After a
readiness-changing action commits, it rings a small doorbell that asks the local loop
to check again. A failed ring is logged and never changes the successful action. The
database and periodic timer are still the source of truth. A process that does not own
the polling lock uses a no-op doorbell; it does not send an IPC wake. A ticket that is
not on today is outside the automatic run set even
when its status is `empty`; it does not appear on the Board, and the ticket page
shows this as `auto not on today`.

_Code paths:_ `src/planner/runtime/ticket_readiness_loop.py`,
`src/planner/runtime/employee_step_runner.py`,
`src/planner/runtime/readiness_doorbell.py`,
`src/planner/tickets/actions.py`, `src/planner/days/actions.py`,
`src/planner/core/link_actions.py`,
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
Hermes accepts a streaming or steered prompt, Panels acknowledges the exact key
revisions it sent. A queued employee prompt is accepted for later but is not yet
acknowledged; Panels waits until that prompt's owned execution starts. A failed,
busy, unknown, or queued-before-start submission keeps the context pending, and a
newer revision written during a send cannot be erased by the older acknowledgement.

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
workers use is **`panels`**. By default, a server uses the `hermes-home` directory
beside its configured planning database, resolved to an absolute path before the
gateways start. An explicit `PLAN_HERMES_HOME` overrides that location. On startup
the server links only this repo's `panels`, `panels-worker`,
`panels-chief-of-staff`, and `panels-rollover` skills into the selected home. The
rollover skill is an operating role for manual or thin scheduled prompts, not a
deterministic server rollover engine. It does not copy or link credentials, provider
configuration, or global Hermes state; those remain configuration owned by the
selected home. Ticket chat has been smoked against a non-test server and reached the
real Hermes worker.

The full live worker loop has also been smoked against fresh non-test databases.
A ticket placed on today was discovered by TicketReadinessLoop, run by
EmployeeStepRunner, and parked at
`awaiting_approval` after the employee filed a proposal. The same durable Hermes
session history showed the employee-step prompt and worker reply in ticket chat.

A second smoke used a long poll interval to prove that a settled success-condition
edit is a wake event, not just something the timer eventually notices: editing the
success value rang the readiness doorbell and the employee filed the next approach proposal.
EmployeeStepRunner persists a created or resumed `chat_session_key` before submitting the
prompt, so a worker calling `panels worker my-ticket` during its own turn can resolve
the current ticket immediately.

A live smoke on the default DB also proved that adding a new harmless ticket to
today now rings the readiness doorbell without a follow-up scope edit. The ticket moved to
`agent_running_step` on the immediate read after the day add, then parked at
`awaiting_approval` with a success proposal.

## Handoffs

- **Tickets & the gates** (`tickets-and-gates.md`) — the proposals the employee
  files, the scope that decides whether a step auto-accepts, and the approval that
  rings the readiness doorbell.
- **Chat** (`chat.md`) — the live conversation with the employee and the slash menu
  of commands and skills, over the same gateway.
- **The command-line tool** (`cli.md`) — the surface the employee acts through.

## Deferred

- **No recovery from a failed run.** An errored ticket is stuck — there is no retry
  or clear. Trigger: recovery is designed and built.
- **Rollover scheduling stays outside the employee runtime.** The server provisions the
  role skill, which is designed for thin morning and afternoon Hermes jobs. Trigger:
  the product decides that Panels itself should own the schedule. See `days.md`.
- **Chat is not queued behind an active worker step.** If you talk to the same
  employee while its worker step is already running, the send is rejected as
  `already_running`; history remains readable. The UI still shows thinking dots,
  then the full answer, rather than streaming token by token.
- **The "mind" → "employee" rename is unfinished** — some code still calls the
  employee a "mind" (`src/planner/minds/`, `MindQueue`).

---

_Last verified: 2026-07-10._
