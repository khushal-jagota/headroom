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
          SQLite records + append-only events
                     |
       +-------------+-------------+
       |                           |
       v                           v
  WebSocket doorbell          System A readiness poll
  keyed UI refetch            today runnable tickets
                                   |
                                   v
                              System B one step
                              Hermes employee
```

The important separation is simple: the server owns truth, workers propose, and the
human resolves decisions. The event log is not the data model. It is a doorbell that
tells screens which server resources to refetch.

## The Systems

### 1. The Record System

SQLite stores the planner's canonical objects: sprints, sprint items, tickets, days,
ideas, links, and events. The main shape is contracts for records and enums, writer
modules for mutations, API files for HTTP, and logic files for pure rules.

The clean rule is: a write goes through a canonical writer, the writer appends an
event, and the UI refetches from the server. Events are append-only. Records are not.
Some older or gap-fill writers still live outside the intended homes; those are
listed in the friction section.

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
today's day. System A starts from that same today membership, then narrows further
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

A ticket has four fields: `success`, `approach`, `plan`, and `result`. Each field
has a settled value, a pending proposal, and notes. Workers write proposals. The
resolution engine is the only code that can settle a proposed value or advance the
ticket's `state`.

Scope decides how far a worker may go without another human approval. It is the pair
`ceiling` plus `at_cap`. Below the ceiling, a proposal can auto-accept. At the cap,
the ticket either stops or parks the next proposal for approval.

Code paths: `src/planner/tickets/contracts.py`,
`src/planner/tickets/logic/machine.py`,
`src/planner/tickets/logic/resolution.py`, `src/planner/tickets/data.py`.

### 4. The Employee Runtime

The runtime has two systems.

**System A** is readiness. It polls today's tickets whose `ticket_status` is `empty`,
then applies the readiness predicate: not terminal, has a next gating field, no
pending proposal on that field, not blocked, and not stopped at its scope limit.

**System B** is one worker step. It marks the ticket `agent_running_step`, resumes or
creates that ticket's Hermes session, submits one prompt, then settles the runtime
status when the turn ends. If the worker parks a proposal, the ticket becomes
`awaiting_approval`; if the proposal auto-accepted and more work is allowed, it
returns to `empty` so System A can run the next step.

System A can be poked by readiness-changing writes, so the timer is a backstop rather
than the normal user experience.

Code paths: `src/planner/runtime/readiness.py`,
`src/planner/runtime/system_a.py`, `src/planner/runtime/system_b.py`,
`src/planner/core/loops.py`.

### 5. The Hermes Gateway System

Hermes is outside the planner. The planner talks to it through a gateway adapter.
Production startup creates one shared gateway owner and installs it on app state.
That owner spawns the child lazily on first use. Tests use fake adapters and never
call the real gateway.

Tickets and days store only a `chat_session_key`. The transcript belongs to Hermes.
When the planner needs history, it resumes that Hermes session lazily and normalizes
the returned messages.

Code paths: `src/planner/core/adapters/`, `src/planner/minds/shared_gateway.py`,
`src/planner/minds/gateway.py`, `src/planner/minds/config.py`.

### 6. The Chat System

Ticket chat and automatic worker steps use the same durable ticket session. System B
stores a newly created or resumed session key before submitting the worker prompt, so
tools inside the worker can resolve their ticket while the turn is still active.

Human chat sends are rejected while `ticket_status=agent_running_step`. History
remains readable. There is no queue behind the active worker step.

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

`panels` is the worker and debugging CLI. It speaks HTTP to the server. It can create,
show, list, propose, write notes, set a few ordinary fields, and place tickets on
days. It has no accept, approve, scope, takeover, release, or chat authority.

The server classifies requests with `X-Plan-Actor`: no header means the human; any
actor header means an agent. Human-only routes reject agent-classified requests.

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

2. **Blocking exists in two shapes.** Tickets use `links.kind='blocks'` for runtime
   readiness. Sprint items also have a `blocked_by` JSON list when their item status
   is `blocked`. Both are logical in isolation, but a reader has to learn two ways
   to say "blocked."

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

_Last verified: 2026-07-08._
