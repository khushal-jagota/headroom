# The employee runtime

Each ticket has a single worker that carries it forward — think of it as an
**employee** you have handed that one piece of work to. Behind the scenes it is a
live AI session (a Hermes "mind") that stays with the ticket across its stages. You
are its manager: you talk to it in the ticket's chat, you set how far it may go (its
scope), and you approve its work. It only ever _proposes_ — nothing it writes becomes
real until it is accepted, automatically within the room you granted or by you in
person.

Automatic discovery decides _whether_ a Ticket may start another employee step. A
separate runner owns the step itself.

```
   AutomaticEmployeeStepDiscoveryLoop      EmployeeStepRunner
   ───────────────────────────────────      ──────────────────
   scan today's membership candidates      claim inside one transaction
   apply the complete eligibility rule ───► resume the Ticket's Hermes session
                                           run and settle one employee step
        ▲                                          │
        └──── best-effort wake after commit ───────┘
              (SQLite + the timer remain canonical)
```

## Discovery and execution

One function answers **Automatic Employee-step eligibility**. It returns yes only
when all eight facts hold:

1. The Ticket belongs to the supplied `planning_day_id` — today's day during
   automatic discovery.
2. Its `ticket_status` is `empty`.
3. Its Stage is not terminal.
4. Its Stage has a next gated field.
5. That field has no parked proposal.
6. Its `ceiling` and `at_cap` allow another proposal; the Ticket is not at or beyond
   a stopping ceiling.
7. It has no active blocker.
8. It has no running Panels Chat turn, whether that visible turn came from a human or
   an Employee step.

**AutomaticEmployeeStepDiscoveryLoop** is read-only and advisory. Its database scan
selects only Tickets that belong to today's day, then it asks the complete eligibility
function about each membership candidate. It passes eligible Ticket ids to the
runner, but it does not claim a Ticket, touch Hermes, or write Ticket state.

**EmployeeStepRunner** separately owns one step through the shared persistent Hermes
gateway child. Its final claim starts `BEGIN IMMEDIATE`, reloads the Ticket and its
Worker type definition, resolves the planning day inside that transaction, and asks
the same complete eligibility function again. A stale discovery result therefore
cannot change status, create chat state, call the gateway, or build a prompt. After a
successful claim, the runner assembles the prompt, resumes or creates the Ticket's
durable session, submits one turn, watches the run end, and settles runtime status
through the Ticket data writers. Proposals, approvals, takeover, release, and runtime
start/finish/error use those same writers, so "the code owns the Stage, the worker
only proposes" holds here too.

Human Chat admission and the final Employee claim both take SQLite's write lock and
recheck their opposing fact inside the transaction. Therefore only one side can win:
a running Chat turn makes the Employee claim a no-op, while
`agent_running_step` makes human admission fail before it creates a visible message or
turn.

The runner exists whenever the worker gateway exists. Automatic discovery is
optional: it may be disabled or another process may own the polling lock. Returning
Review work for revision deliberately bypasses Automatic Employee-step eligibility
and planning-day resolution. It reserves a runner handoff before changing the Ticket,
then releases that handoff after commit. The revision resumes the stored Hermes
session strictly. If that session is stale, Panels records an errored employee turn
instead of silently creating a different conversation.

One employee is one Ticket session, so Hermes' per-session busy guard keeps one turn
in flight for that Ticket while the shared employee-role child can hold many sessions.
The Ticket keeps its durable `employee_session_id` across stages. Human Ticket Chat
and Employee steps both deliver through this conversation. The lightweight Panels
listener may detach after Hermes is observed idle and no accepted operation remains;
reopening the employee or restarting Panels resumes the stored session. If delivery is
unknown, Panels records that honest outcome and never retries the employee prompt
automatically. After an eligibility-affecting action commits, it calls the payload-free
best-effort `AutomaticEmployeeStepEligibilityWake.wake()`. Runner settlement does the
same to continue automatic work. A failed wake is logged and never changes the
successful action. The wake carries no Ticket id, owns no state, and sends no IPC.
SQLite and the periodic discovery timer remain canonical. The polling-lock owner uses
`LoopAutomaticEmployeeStepEligibilityWake`; a process without the lock receives
`NoOpAutomaticEmployeeStepEligibilityWake`. A ticket that is not on today is outside
the automatic run set even
when its status is `empty`; it does not appear on the Board, and the ticket page
shows this as `auto not on today`.

Chat completion, Chat error, and Pause deliberately send no eligibility wake. They
only settle the visible Chat turn. The SQLite-backed periodic discovery timer is the
canonical backstop: its next scan observes that the eighth factor has cleared and may
submit the Ticket to the runner.

_Code paths:_ `src/planner/runtime/automatic_employee_step_eligibility.py`,
`src/planner/runtime/automatic_employee_step_discovery_loop.py`,
`src/planner/runtime/automatic_employee_step_eligibility_wake.py`,
`src/planner/runtime/employee_step_runner.py`,
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

Panels Chat rows and event rows remain display and audit records. They are not this
delivery mechanism: a row alone never makes the Employee receive anything. The
generic storage, contracts, and composition live in
`src/planner/worker_context/`; ticket-specific keys live with the ticket domain.

The explicit `GET /api/tickets/{ticket_id}/employee-session-history` route is the
authoritative inspection of what Hermes actually received and produced for the stored
Employee session. It may show pending context, hidden revision guidance, system or
tool content, or other real session material absent from Panels Chat. Panels Chat state
never loads or merges that history.

## When a run fails: errors in the event log

When an employee's run goes wrong, the ticket's history tells you why. A failed run
used to show only a bare "errored"; now the event log carries the actual reason —
for example, that a skill the worker needed was not installed — marked in amber so it
stands out, with the full message allowed to wrap so nothing is cut off. That turns a
stuck ticket from a mystery into something you can debug.

## What is proved

The runtime launches the **`panels-worker`** base role — deliberately Worker-type
agnostic; it points the worker to load its Worker type's specialist skill on demand
rather than baking one Worker type's Stages in (see `worker-types.md`). The CLI entry
point workers use is
**`panels`**. By default, a server uses the `hermes-home` directory beside its
configured planning database, resolved to an absolute path before the gateways start.
An explicit `PLAN_HERMES_HOME` overrides that location. On startup the server links this
repo's role skills into the selected home — the base `panels-worker`, the per-Worker-type
specialists (`panels-worker-coding`, `panels-worker-new-worker`, and the `probe-worker`
test fixture), plus `panels`, `panels-chief-of-staff`, `panels-sprint-planning`, and
`panels-rollover`. That link is what lets a worker `skill_view` its specialist. The
rollover skill is an operating role for manual or thin scheduled prompts, not a
deterministic server rollover engine. It does not copy or link credentials, provider
configuration, or global Hermes state; those remain configuration owned by the
selected home. Ticket chat has been smoked against a non-test server and reached the
real Hermes worker.

The full live worker loop has also been smoked against fresh non-test databases.
A ticket placed on today was discovered by AutomaticEmployeeStepDiscoveryLoop, run by
EmployeeStepRunner, and parked at
`awaiting_approval` after the employee filed a proposal. The explicit Employee session
history showed the employee-step prompt and worker reply; Panels Chat independently
showed only its intentionally visible rows.

A second smoke used a long poll interval to prove that a settled success-condition
edit wakes discovery instead of waiting for the timer: after the edit committed, its
eligibility wake led to the employee filing the next approach proposal.
EmployeeStepRunner persists a created or resumed `employee_session_id` before submitting the
prompt, so a worker calling `panels worker my-ticket` during its own turn can resolve
the current ticket immediately.

A live smoke on the default DB also proved that adding a new harmless ticket to
today wakes eligibility discovery without a follow-up scope edit. The ticket moved to
`agent_running_step` on the immediate read after the day add, then parked at
`awaiting_approval` with a success proposal.

## Handoffs

- **Worker types** (`worker-types.md`) — how the base worker self-routes to its Worker
  type's specialist skill, and the registry that declares each Worker type's Stages and
  worker.
- **Tickets & the gates** (`tickets-and-gates.md`) — the proposals the employee
  files, the scope that decides whether a step auto-accepts, and the approval that
  wakes Automatic Employee-step eligibility discovery after commit.
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
  `already_running`; explicit Employee session history remains readable. The UI still
  shows thinking dots, then the full answer, rather than streaming token by token.
- **The "mind" → "employee" rename is unfinished** — some code still calls the
  employee a "mind" (`src/planner/minds/`, `MindQueue`).

---

_Last verified: 2026-07-14._
