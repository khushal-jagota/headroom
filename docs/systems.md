# Systems

This planner is a local planning app with one source of truth: SQLite. Everything
else is a surface, a worker, or a projection of that record.

```
                human web page
              decisions and edits
                     |
                     v
              FastAPI domain routes
                     |
       +-------------+-------------+
       |                           |
       v                           v
  domain writers              resolution engine
  plain records               ticket gate writes
       |                           |
       +-------------+-------------+
                     |
                     v
       SQLite records + normally append-only events
                     |
       +-------------+-------------+
       |                           |
       v                           v
  WebSocket doorbell          TicketReadinessLoop
  keyed UI refetch            today runnable tickets
                                   |
                                   v
                              EmployeeStepRunner
                              Hermes employee
```

The important separation is simple: the server owns truth, ticket workers propose, and
direct product operations resolve decisions. Those operations are not assumed to be
human merely because no actor header is present. The event log is not the data model;
it is a doorbell that tells screens which server resources to refetch.

## The Systems

### 1. The Record System

SQLite stores the planner's canonical objects: sprints, sprint items, tickets, days,
ideas, links, and events. The main shape is contracts for records and enums, writer
modules for mutations, API files for HTTP, and logic files for pure rules.

The clean rule is: a write goes through a canonical writer, the writer appends an
event, and the UI refetches from the server. Event history normally only grows;
permanently deleting a mistaken ticket is the one exception, replacing that ticket's
history with a minimal deletion audit. Records are not append-only. Some older or
gap-fill writers still live outside the intended homes; those are listed in the
friction section.

Code paths: `src/planner/core/db.py`, `src/planner/core/contracts.py`,
`src/planner/core/events.py`, `src/planner/*/contracts.py`, `src/planner/*/data.py`.

### 2. The Planning Objects

These are the things the planner is made of:

- **Sprints** are two-week pushes with overview writing and a tracking list.
- **Sprint items** are larger chunks inside or outside a sprint.
- **Tickets** are worker-sized pieces of work.
- **Days** are daily overview records and ordered lists of tickets.
- **Ideas** are loose thoughts that are not work yet.
- **Links** connect records. The most important link is `blocks`, because it can stop
  a ticket from becoming runnable.

The Board is not an inventory. It is today's execution board: tickets attached to
today's day. TicketReadinessLoop starts from that same today membership, then narrows further
to empty, non-terminal tickets that pass readiness. A ticket can exist and be ready
in every other way, but if it is not on today's day, it will not auto-run.

Code paths: `src/planner/sprints/`, `src/planner/tickets/`,
`src/planner/days/`, `src/planner/core/links.py`.

### 3. The Ticket Gate System

Tickets are the correctness center. A ticket has two different kinds of state:

- `state` is the work stage: `needs_success`, `needs_approach`, `needs_plan`,
  `in_progress`, `needs_review`, `done`, or `dropped`.
- `ticket_status` is runtime control: `empty`, `agent_running_step`,
  `awaiting_approval`, `user_takeover`, or `errored`.

A ticket has a `user_note` for intake context plus four fields: `success`,
`approach`, `plan`, and `result`. Each field has a settled value, a pending proposal,
and a field `user_note` for step-specific user guidance. Workers write proposals. The
resolution engine is the only code that can settle a proposed value or advance the
ticket's `state`.

Scope decides how far a worker may go without another human approval. It is the pair
`ceiling` plus `at_cap`. Below the ceiling, a proposal can auto-accept. At the cap,
the ticket either stops or parks the next proposal for approval.

Code paths: `src/planner/tickets/contracts.py`,
`src/planner/tickets/logic/machine.py`,
`src/planner/tickets/logic/resolution.py`, `src/planner/tickets/data.py`.

Chief external-work intake is a second, explicit canonical path for reality already
established outside Panels. It creates or reconciles a coherent settled-field prefix,
sets the ticket to the reported state, and stops there. It refuses pending proposals,
active control, running turns, backward moves, and malformed field prefixes. The report
and reconciliation reasoning live in the existing ticket note; ordinary ticket and
sprint-item events remain the audit and invalidation signals.

Request identity is operational provenance in this local, same-user app, not an
authentication credential. A request without `X-Plan-Actor` is **unattributed**, not
implicitly human. Production gateways set `worker` or `chief`; direct-only routes allow
unattributed and Chief requests while rejecting the worker role. This prevents the
normal worker and Chief paths from confusing their authority, but it is not a security
barrier against a local process that deliberately forges headers or environment values.

### 4. The Employee Runtime

The runtime has two systems.

**TicketReadinessLoop** discovers readiness. It polls today's Tickets whose `ticket_status` is `empty`,
then applies the readiness predicate: not terminal, has a next gating field, no
pending proposal on that field, not blocked, and not stopped at its scope limit.

**EmployeeStepRunner** owns one worker step. It marks the Ticket `agent_running_step`, resumes or
creates that ticket's Hermes session, submits one prompt, then settles the runtime
status when the turn ends. If the worker parks a proposal, the ticket becomes
`awaiting_approval`; if the proposal auto-accepted and more work is allowed, it
returns to `empty` so TicketReadinessLoop can discover the next step.

TicketReadinessLoop can be poked by readiness-changing writes, so the timer is a backstop rather
than the normal user experience.

Code paths: `src/planner/runtime/readiness.py`,
`src/planner/runtime/ticket_readiness_loop.py`,
`src/planner/runtime/employee_step_runner.py`,
`src/planner/core/loops.py`.

### 5. The Hermes Gateway System

Hermes is outside the planner. The planner talks to it through a gateway adapter.
Production startup creates one shared gateway owner and installs it on app state.
That owner spawns the child lazily on first use. Tests use fake adapters and never
call the real gateway.

Tickets, days, and top-level agent chats store a `chat_session_key` for Hermes
transport. Panels owns the product chat state in `chat_messages` and `chat_turns`.
Hermes history is still readable for old sessions, but the UI reads Panels'
`ChatState` resource.

Panels chat state is not model context. Appending to `chat_messages` or `chat_turns`
does not make a worker see that text. Anything the worker must read has to go
through the Hermes gateway/session path or the actual worker prompt; the Panels chat
row is only the UI/audit mirror.

Code paths: `src/planner/core/adapters/`, `src/planner/minds/shared_gateway.py`,
`src/planner/minds/gateway.py`, `src/planner/minds/config.py`.

### 6. The Chat System

Ticket chat, Chief of Staff chat, and automatic worker steps use the same chat-state
contract. A chat state is durable messages plus one optional active turn. The active
turn carries the phase, activity label, partial output, session key, and error.

EmployeeStepRunner stores a newly created or resumed session key before submitting the worker
prompt, so tools inside the worker can resolve their ticket while the turn is still
active. It also records the worker turn in chat state, so the UI does not infer
activity from ticket status.

Human chat sends are rejected while `ticket_status=agent_running_step`. History
remains readable. There is no queue behind the active worker step.

Because the chat state is product state, backend code must not use a visible chat row
as a substitute for worker-session delivery. Designs that depend on worker awareness
must prove the text reached the Hermes session.

Code paths: `src/planner/chat/`, `web/src/components/ChatPanel.svelte`.

### 7. The Human UI System

The Svelte app is a set of resource projections. It does not keep a canonical client
store. Each screen subscribes to named resources such as `ticket:<id>`, `board`,
`queues`, `day:today`, and `sprint:current`.

The WebSocket sends event batches. The browser maps each event to resource keys and
refetches only those keys. This is why adding a backend event kind must be covered by
the event-mapping test.

Code paths: `web/src/App.svelte`, `web/src/routes/`, `web/src/lib/resources.svelte.ts`,
`web/src/lib/ws.ts`, `web/src/lib/eventMapping.mjs`.

### 8. The CLI And Authority System

`panels` is the product and worker CLI. Its command groups are `project`, `day`,
`ticket`, `sprint`, `sprint item`, `worker`, and `chief`. Ordinary commands preserve an
ambient `PLAN_ACTOR` when one exists and otherwise send no actor. Worker commands send
the ambient role or the `agent` fallback. Chief commands never synthesize Chief identity;
production starts the worker and Chief gateways with explicit `worker` and `chief` roles.

Ordinary groups can plan days, manage tickets, plan sprints, and file worker
proposals/recaps/notes without exposing runtime controls. The exceptional Chief group
has two external-work operations that establish a coherent imported state; it is not a
generic state setter.

The server classifies a missing `X-Plan-Actor` as **unattributed**, not human. `chief` is
the explicit Chief role; every other non-empty value is an attributed non-Chief agent.
Direct-only routes allow unattributed and Chief requests while rejecting worker agents.

Code paths: `src/planner/cli/main.py`, `src/planner/cli/http.py`,
`src/planner/core/authctx.py`.

## Boundaries That Matter

- The resolution engine owns ticket field values and ticket `state` transitions.
- Ticket runtime writers own `ticket_status`.
- Canonical writer functions own database mutations. Most live in domain `data.py`
  files; current exceptions are called out below.
- Hermes owns chat transcripts.
- The frontend owns rendering and local edit drafts only.
- The CLI is an action surface, not a decision surface.

These boundaries are why the system is understandable: there are many surfaces, but
few places where truth can change.

## System-Level Friction

These are not local style issues. They are places where the system boundaries are
less clean than the rest.

1. **Chat session-key ownership is a special writer exception.** Most mutations now
   live behind domain `data.py` writers. Chat session-key writes still live in
   `src/planner/chat/service.py` because they coordinate with the gateway during
   live conversation setup. That is intentional, but it is still an exception a
   cold reader has to know.

2. **Sprint item status is a read projection.** Sprint items no longer store their
   own status. They derive `todo`, `in_progress`, `blocked`, or `done` from child
   tickets and open `links.kind='blocks'` rows. This keeps ticket readiness and item
   blocking on the same link model.

3. **The frontend copies some ticket state-machine constants.** The server owns the
   ticket state machine, but `web/src/lib/ui.ts` repeats `STATE_ORDER`, gating fields,
   and advance targets so the UI can render controls. That is acceptable as a view
   projection, but it is a drift risk. A shared generated contract or server-provided
   metadata would make the boundary cleaner.

4. **Gateway bootstrap is indirect.** The adapter registry returns a real adapter
   placeholder, then production startup replaces app-state adapters with
   `SharedGateway`. This keeps tests clean, but it is not obvious from the registry
   alone.

## Deferred

- **No failed-run recovery.** An errored ticket has no retry or clear path. Trigger:
  a recovery policy is designed.
- **No automatic rollover writer.** Days materialize, but no worker drafts the next
  day's overview. Trigger: rollover work lands.
- **No chat queue during worker steps.** Human sends are rejected while a ticket worker
  is active. Trigger: the product needs conversation to queue behind active work.
- **No idea conversion or archive.** Ideas stay ideas. Trigger: a decision that ideas
  should be promoted or filed away.

---

_Last verified: 2026-07-10._
