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
- **Links** are only blockers. A Ticket can block another Ticket or a Sprint item.
  A blocker only counts while its source Ticket is not `done` or `dropped`.

The Board is not an inventory. It is today's execution board: tickets attached to
today's day. TicketReadinessLoop starts from that same today membership, then narrows further
to empty, non-terminal tickets that pass readiness. A ticket can exist and be ready
in every other way, but if it is not on today's day, it will not auto-run.

Daily rollover is an agent workflow rather than a domain engine. The repo-owned
`panels-rollover` skill reads the day and sprint boundary through the CLI, drafts the
likely four-field kickoff, and records obvious carryover candidates pending review.
Automatic runs never add tickets to today before user agreement and never carry
`done` or `dropped` tickets. Broad reprioritization stays in sprint planning.

Code paths: `src/planner/sprints/`, `src/planner/tickets/`,
`src/planner/days/`, `src/planner/core/links.py`.

### 3. The Ticket Gate System

Tickets are the correctness center. A ticket has two different kinds of state:

- `state` is the work stage. Which stages exist is set by the ticket's **type**, not
  fixed for all tickets; the gate reads each row's stage order, gates, and fields from
  the type registry (see `ticket-types.md`). For the default `coding` type the states are
  `needs_kickoff`, `needs_success`, `needs_approach`, `needs_plan`,
  `needs_implementation`, `needs_closeout`, `done`, or `dropped`.
- `ticket_status` is runtime control: `empty`, `agent_running_step`,
  `awaiting_approval`, `user_takeover`, or `errored`. This set is universal.

An ordinary Ticket starts with a parked proposal on the `kickoff` field — every type
leads with Kickoff. The title is separate editable Ticket metadata; approving Kickoff
settles only the Kickoff field and advances the Ticket to its first working stage
(`needs_success` for coding). Until then no worker stage runs. For coding the fields are
`kickoff`, `success`, `approach`, `plan`, `implementation`, and `closeout`; another type
carries its own. Each field has a settled value, a pending proposal, and a field
`user_note` for step-specific guidance. Workers write field proposals. The resolution
engine is the only code that can settle a proposal or advance the Ticket.

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
and reconciliation reasoning live in the Ticket's `kickoff` field; ordinary Ticket and
sprint-item events remain the audit and invalidation signals.

Request identity is operational provenance in this local, same-user app, not an
authentication credential. A request without `X-Plan-Actor` is **unattributed**, not
implicitly human. Production gateways set `worker` or `chief`; direct-only routes allow
unattributed and Chief requests while rejecting the worker role. This prevents the
normal worker and Chief paths from confusing their authority, but it is not a security
barrier against a local process that deliberately forges headers or environment values.

Hosted access keeps that same local trust model. Panels still binds only to
`127.0.0.1`; a public interface should sit in front of it. The first supported
hosted ingress is Tailscale Serve. Configure it with:

```
PLAN_TRUSTED_INGRESS_PROVIDER=tailscale
PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN=khushal@example.com
PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN=https://<tailscale-serve-name>
```

When `Tailscale-User-Login` is present, Panels treats the request as the remote
user path. The login must exactly match `PLAN_TRUSTED_INGRESS_ALLOWED_LOGIN`, and
any public `X-Plan-Actor` header on that request is ignored. When that Tailscale
identity header is absent, Panels treats the request as the co-located trusted-host
path and preserves the existing `X-Plan-Actor` provenance rules. Same-host processes
are trusted in this phase; Panels is not trying to isolate one local process from
another.

This depends on a Tailscale policy assumption: only Khushal's user-owned, non-tagged
devices should reach the served Panels URL. Tagged nodes do not provide the user
login header this boundary relies on, so they are not a supported remote-client path.
Tailscale enforces device enrollment and ACLs; Panels only verifies the trusted
identity header that Serve forwards.

Browser Origin protection is narrow and explicit. In hosted mode, an unsafe HTTP
request with an `Origin` header must match `PLAN_TRUSTED_INGRESS_CANONICAL_ORIGIN`.
Requests without `Origin`, such as `curl` or Python scripts, remain valid. WebSocket
handshakes follow the same rule: a present wrong `Origin` is rejected, while an absent
Origin is allowed for non-browser clients.

The Tailscale parsing lives behind the trusted-ingress contract in
`src/planner/core/trusted_ingress.py`. A later Cloudflare Access, Pomerium, or other
gateway can replace that provider adapter without changing ticket, chat, or file
routes.

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

Ticket, Day membership, and blocking-link actions commit first, then ring a
best-effort `ReadinessDoorbell`. Runner settlement rings the same doorbell to continue
automatic work. The doorbell carries no Ticket id and owns no state. Delivery failure
is logged and ignored; SQLite and the periodic timer remain canonical. Processes that
do not own the polling lock receive a no-op doorbell instead of trying to wake another
process.

Code paths: `src/planner/runtime/readiness.py`,
`src/planner/runtime/readiness_doorbell.py`,
`src/planner/runtime/ticket_readiness_loop.py`,
`src/planner/runtime/employee_step_runner.py`,
`src/planner/tickets/actions.py`, `src/planner/days/actions.py`,
`src/planner/core/link_actions.py`,
`src/planner/core/loops.py`.

### 5. The Hermes Gateway System

Hermes is outside the planner. The planner talks to it through a gateway adapter.
Production owns two independently configured role gateways: one for employees and
one for the Chief of Staff. Each gateway spawns its Hermes child lazily on first use.
Each child has one listener for all of its live sessions. The listener separates
observations by live Hermes session and gives each accepted Panels operation its own
consequence, so a delayed completion cannot settle a different operation. Tests use
fake adapters and never call the real gateway.

Tickets, days, and top-level agent chats store a `chat_session_key` for Hermes
transport. Panels owns the product chat state in `chat_messages` and `chat_turns`.
Hermes history is still readable for old sessions, but the UI reads Panels'
`ChatState` resource.

The stored Hermes session key is durable conversation identity. The lightweight
in-process listener for that conversation may detach after Hermes reports idle and
no Panels consequence remains. A later operation resumes the stored session instead
of replaying prior input.

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
Hosted Tailscale user requests deliberately ignore `X-Plan-Actor`; same-host internal
requests still use it as provenance.

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
   tickets and active `links.kind='blocks'` rows. A `done` or `dropped` source clears
   its block. This keeps ticket readiness and item blocking on the same read model.

3. **The frontend no longer copies the ticket state machine (resolved).** The UI used
   to repeat stage order, gating fields, and advance targets in `web/src/lib/ui.ts`. It
   now derives them per type from the served `GET /api/ticket-types` manifest, through
   `web/src/lib/lifecycle.ts` — the server is the single source and the drift risk is
   closed (see `ticket-types.md`). `ui.ts` keeps only the label helper and visual-state
   shapes the lifecycle builds on.

4. **Gateway bootstrap is indirect.** The adapter registry returns a real adapter
   placeholder, then production startup replaces app-state adapters with
   `SharedGateway`. This keeps tests clean, but it is not obvious from the registry
   alone.

## Deferred

- **No failed-run recovery.** An errored ticket has no retry or clear path. Trigger:
  a recovery policy is designed.
- **No in-server rollover scheduler.** The repo provisions the agent-owned rollover
  skill; thin morning and afternoon prompts remain external to the server runtime.
- **No chat queue during worker steps.** Human sends are rejected while a ticket worker
  is active. Trigger: the product needs conversation to queue behind active work.
- **No idea conversion or archive.** Ideas stay ideas. Trigger: a decision that ideas
  should be promoted or filed away.

---

_Last verified: 2026-07-13._
