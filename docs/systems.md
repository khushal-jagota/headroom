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
  WebSocket doorbell          AutomaticEmployeeStepDiscoveryLoop
  keyed UI refetch            today membership candidates
                                   |
                                   v
                              EmployeeStepRunner
                              one Hermes employee step
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
today's day. AutomaticEmployeeStepDiscoveryLoop starts with that same membership-only
candidate set, then asks the complete Automatic Employee-step eligibility decision
about each Ticket. A Ticket that is not on today's day will not auto-run.

Daily rollover is an agent workflow rather than a domain engine. The repo-owned
`panels-rollover` skill reads the day and sprint boundary through the CLI, drafts the
likely four-field kickoff, and records obvious carryover candidates pending review.
Automatic runs never add tickets to today before user agreement and never carry
`done` or `dropped` tickets. Broad reprioritization stays in sprint planning.

Code paths: `src/planner/sprints/`, `src/planner/tickets/`,
`src/planner/days/`, `src/planner/core/links.py`.

### 3. The Ticket Gate System

Tickets are the correctness center. A Ticket has a Stage and a separate control status:

- `stage` is the stored work Stage. Which Stages exist is set by the Ticket's **Worker
  type**, not fixed for all Tickets; the gate reads each row's Stage order, gates, and
  fields from the Worker type registry (see `worker-types.md`). For the
  `coding` Worker type the Stages are
  `needs_kickoff`, `needs_success`, `needs_approach`, `needs_plan`,
  `needs_implementation`, `needs_closeout`, `done`, or `dropped`.
- `ticket_status` is runtime control: `empty`, `agent_running_step`,
  `awaiting_approval`, `user_takeover`, `paired_work`, or `errored`. This set is
  universal.

An ordinary Ticket starts with a parked proposal on the `kickoff` field — every Worker type
leads with Kickoff. The title is separate editable Ticket metadata; approving Kickoff
settles only the Kickoff field and advances the Ticket to its first working stage
(`needs_success` for coding). Until then no worker stage runs. For coding the fields are
`kickoff`, `success`, `approach`, `plan`, `implementation`, and `closeout`; another Worker type
carries its own. Each field has a settled value, a pending proposal, and a field
`user_note` for step-specific guidance. Workers write field proposals. The resolution
engine is the only code that can settle a proposal or advance the Ticket.

Scope decides how far a worker may go without another human approval. It is the pair
`ceiling` plus `at_cap`. Below the ceiling, a proposal can auto-accept. At the cap,
the ticket either stops or parks the next proposal for approval; the UI labels that
parked-proposal choice **Continue** while the stored value remains `propose`.

Stage ownership decides who drives the current Stage. Every non-terminal Stage has a
default owner from its Worker type: worker, user, or paired. A Ticket may override one
Stage. Worker-owned Stages can be discovered automatically when every other condition
allows it. User-owned Stages rest in `user_takeover` and return through Chief
external-work reconciliation. Paired Stages rest in `paired_work`; ordinary Ticket Chat
continues the same Employee session, and any real proposal parks for approval.

The Worker type selects the Employee's specialist skill. Tickets do not carry a separate
execution-route selector.

Code paths: `src/planner/tickets/contracts.py`,
`src/planner/tickets/logic/machine.py`,
`src/planner/tickets/logic/resolution.py`, `src/planner/tickets/data.py`.

Chief external-work intake is a second, explicit canonical path for reality already
established outside Panels. It creates or reconciles a coherent settled-field prefix,
sets the Ticket to the reported Stage, and stops there. It refuses pending proposals,
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

The runtime keeps advisory discovery separate from step execution.

One complete decision owns **Automatic Employee-step eligibility**. It returns yes
only when all nine facts hold:

1. The Ticket belongs to the supplied `planning_day_id` — today's day during
   automatic discovery.
2. Its current Stage's effective ownership is `worker`.
3. Its `ticket_status` is `empty`.
4. Its Stage is not terminal.
5. Its Stage has a next gated field.
6. That field has no parked proposal.
7. Its `ceiling` and `at_cap` allow another proposal; the Ticket is not at or beyond
   a stopping ceiling.
8. It has no active blocker.
9. It has no running Panels Chat turn, from either a human or an Employee step.

**AutomaticEmployeeStepDiscoveryLoop** is read-only and advisory. It selects only
Tickets that belong to today's day, then calls the complete eligibility decision for
each membership candidate. It does not claim Tickets, write state, or contact Hermes.

**EmployeeStepRunner** separately owns one worker step. Its final claim starts
`BEGIN IMMEDIATE`, reloads the Ticket and its Worker type definition, resolves the
planning day inside the transaction, and calls the same complete eligibility decision.
A stale discovery result cannot change status, create chat state, call the gateway, or
build a prompt. After a successful claim, the runner marks the Ticket
`agent_running_step`, resumes or creates its durable `employee_session_id`, submits
one prompt through that Hermes conversation, and settles runtime status when the turn
ends. A parked proposal becomes
`awaiting_approval`; auto-accepted work or a no-proposal completion derives the next
inactive status from the current Stage's effective owner.

Human Chat admission and the final Employee claim both use SQLite's write lock and
recheck the opposing fact inside the transaction. A human turn that wins admission
makes the final Employee claim a no-op. An Employee claim that wins first makes human
admission fail without creating a visible message or turn.

A direct requested revision is different. It bypasses Automatic Employee-step
eligibility and planning-day resolution, uses a reserved runner handoff, and strictly
resumes the Ticket's stored `employee_session_id`.

At startup, Panels starts both Hermes role gateways, recovers ordinary human Chat
turns, then recovers Tickets still at `agent_running_step`, all before automatic
discovery starts. Employee recovery is not another automatic claim: it neither checks
eligibility nor rebuilds the old prompt. It strictly resumes the stored
`employee_session_id` and tells the Employee to inspect the canonical Ticket and
existing conversation before continuing unfinished work. The old visible turn is
settled as interrupted with partial output preserved, and one replacement recovery
turn uses the same id. Repeated restarts keep one active turn. A missing or mismatched
id fails recovery without contacting Hermes or creating a replacement session. A
leftover worker turn whose Ticket is no longer running is only settled; it is never
prompted again.

Ticket, Day membership, and blocking-link actions commit first, then call
`AutomaticEmployeeStepEligibilityWake.wake()`. Runner settlement does the same. This
best-effort same-process wake carries no Ticket id, owns no state, and sends no IPC.
Failure is logged and ignored; SQLite and the periodic discovery timer remain
canonical. The polling-lock owner uses
`LoopAutomaticEmployeeStepEligibilityWake`; a process without the lock receives
`NoOpAutomaticEmployeeStepEligibilityWake`.

Chat completion, Chat error, and Pause do not send an eligibility wake. They only
settle the visible Chat turn. The next SQLite-backed periodic scan observes that the
running-chat factor has cleared and may run the Ticket only if ownership and every
other condition allow it.

Employee shutdown and restart recovery are owned by the Employee runtime. See
[`employee-runtime.md`](employee-runtime.md).

Code paths: `src/planner/runtime/automatic_employee_step_eligibility.py`,
`src/planner/runtime/automatic_employee_step_discovery_loop.py`,
`src/planner/runtime/automatic_employee_step_eligibility_wake.py`,
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

Each Ticket stores an `employee_session_id`. Human Ticket Chat, automatic Employee
steps, and revisions all deliver through that durable Hermes conversation. Days and
top-level-agent Chat instead keep their honest Chat-specific `chat_session_key`.
The lightweight in-process listener may detach after Hermes reports idle and no Panels
consequence remains. A later operation or process restart resumes the stored identity
instead of replaying prior input.

Restart recovery is strict. The gateway may resume the exact stored session but may not
create a new one, including for `/new`. If that identity no longer exists, recovery
fails honestly instead of silently starting a different conversation.

Panels owns product-visible Chat state in `chat_messages` and `chat_turns`. Its
`ChatState` read never contacts Hermes or merges Employee history. Employee session
history is available only through the direct Ticket route
`GET /api/tickets/{ticket_id}/employee-session-history`. That explicit result is the
authoritative Hermes record of what the Employee received and produced, so it may
contain internal context, revision guidance, system or tool content, or other material
that Panels Chat intentionally does not show.

Every human Hermes write uses causal session binding. A candidate created or resumed
by Hermes is passed to the Chat data writer before a command, image attachment, or
prompt is sent. That writer atomically updates the entity and running turn and returns
the effective key. If another transaction already chose a winner, the gateway resumes
and writes to that winner's live session. Exact `/new` is the one forced-fresh case.

Panels Chat state is not model context. Appending to `chat_messages` or `chat_turns`
does not make an Employee see that text. Anything the Employee must read has to go
through the Hermes gateway/session path or the actual Employee prompt; a Panels row
alone is only the UI/audit mirror, never delivery.

Code paths: `src/planner/core/adapters/`, `src/planner/minds/shared_gateway.py`,
`src/planner/minds/gateway.py`, `src/planner/minds/config.py`.

### 6. The Chat System

Ticket chat, Chief of Staff chat, and automatic worker steps use the same chat-state
contract. A chat state is durable messages plus one optional active turn. The active
turn carries the phase, activity label, partial output, error, and whether Pause is
available. It does not expose durable Employee identity.

Ordinary human messages and commands have one ingress:
`POST /api/chat/{entity_id}/turns`. The Chief message endpoint is a narrow shell over
the same owner. `ChatTurnLifecycle` is the single coordinator: it atomically admits
one visible human turn, invokes the typed human gateway transport, records safe
activity and output, and lets the first completion, error, or Pause settle the turn.
The browser observes durable `ChatState` instead of owning a second HTTP stream.

An ordinary human turn left running by a restart is rolled into one new recovery turn
after the role gateways are ready. Panels preserves the old partial output, does not
replay the person's original message, and sends a new recovery instruction through the
actual Hermes session using the stored id. The system row shown in Panels mirrors that
instruction for the product transcript; the row itself is not delivery. A missing or
mismatched id settles the stale turn as an error without calling Hermes or minting a
replacement. Repeating the restart still leaves only one active recovery turn.

EmployeeStepRunner remains a separate lane. It stores a newly created or resumed
`employee_session_id` before calling the employee-only `run_ticket_step`, so tools
inside the worker can resolve their ticket while the turn is still active. It does
not use `ChatTurnLifecycle` or the human transport. It also records the worker turn
in chat state, so the UI does not infer activity from ticket status.

Human Chat turns are rejected while `ticket_status=agent_running_step`. Automatic
Employee-step eligibility rejects any running Panels Chat turn. The two checks happen
again under the same transaction lock, so concurrent admission has one winner.
Explicit Employee session history remains readable. There is no queue behind the
active worker step.

Because Chat state is product state, backend code must not use a visible row as a
substitute for Employee-session delivery. An empty Panels transcript stays empty even
when Hermes has history. Designs that depend on Employee awareness must prove the text
reached the Hermes session.

Code paths: `src/planner/chat/`, `web/src/components/ChatPanel.svelte`.

### 7. The Human UI System

The Svelte app is a set of server projections. The Resource Catalogue names every
cached read, its endpoint and type, and the events that affect it. Screens open named
resources such as `ticket:<id>`, `board`, `review`, `day:today`, and
`sprint:current`. The cache engine only manages loaded values, subscribers, and
overlapping requests. It does not know what a Ticket or Project is. Review contains
only today's parked Ticket proposals and the global running-worker count.

The WebSocket sends event batches. Events invalidate resources through catalogue
dependencies, and successful UI writes apply one named catalogue effect immediately.
Only the affected projections refetch. There is no whole-screen refetch and no
canonical client store; the server remains the source of truth.

A Project rename refreshes Projects, Board, today's Day, backlog Sprint items, Ideas,
current Sprint, and an already-opened Ticket whose direct Project matches. An opened
Ticket whose first read has not settled is included conservatively. A loaded Ticket
with another Project, no Project, or no direct Project field is excluded. Project
creation and summary-only edits refresh Projects only. The catalogue keeps a private
process-lifetime list of resources opened through its own methods so it can inspect
those Ticket details. The cache remains generic.

For a Ticket, `employee_session_changed` refreshes only its Ticket detail. The four
Panels Chat events — `chat_message_recorded`, `chat_turn_started`,
`chat_turn_updated`, and `chat_turn_finished` — refresh only that Ticket's Chat.
Neither set refreshes Board, current Sprint, or Review. Day and top-level-agent
`chat_session_created` and those same Chat events refresh only their matching Chat;
Day Chat events do not refresh the Day projection. Explicit Employee session history
is an ordinary uncached read, not a Panels Chat resource.

Managed Markdown has one browser-DOM owner. `managedMarkdown.ts` renders through the
hardened renderer, mounts file previews, maintains editable atomic blocks, serializes
edits, reconciles moved or deleted previews, and tears everything down. The
`MarkdownBlock` and `InlineEdit` components only supply product values and coordinate
read-only presentation or ordinary editing and save behavior. Plain contenteditable
helpers remain separate from Markdown lifecycle work.

Code paths: `web/src/App.svelte`, `web/src/routes/`,
`web/src/lib/resourceCatalogue.ts`, `web/src/lib/resources.svelte.ts`,
`web/src/lib/ws.ts`,
`web/src/lib/managedMarkdown.ts`, `web/src/lib/editableText.ts`.

### 8. The CLI And Authority System

`panels` is the product and worker CLI. Its command groups are `project`, `day`,
`ticket`, `sprint`, `sprint item`, `worker`, and `chief`. Ordinary commands preserve an
ambient `PLAN_ACTOR` when one exists and otherwise send no actor. Worker commands send
the ambient role or the `agent` fallback. Chief commands never synthesize Chief identity;
production starts the worker and Chief gateways with explicit `worker` and `chief` roles.

Ordinary groups can plan days, manage tickets, plan sprints, and file worker
proposals/recaps/notes without exposing runtime controls. The exceptional Chief group
has two external-work operations that establish a coherent imported Stage; it is not a
generic Stage setter.

`panels serve` is the operator-owned foreground process. It holds one port-scoped
lifecycle lease and supervises one application child from the source root captured at
launch. `panels restart` sends a versioned request to that supervisor through its local
control socket. The supervisor acknowledges the request, gracefully stops its owned
child, waits for that child to exit, and only then starts one replacement from the same
root. The command never discovers or signals a listener PID. A caller in a Ticket
worktree cannot choose the replacement's code, configuration, or built frontend.

Stopping the foreground supervisor gracefully stops its application child and removes
the control socket. An application child that exits unexpectedly ends the supervisor
with an error; this system is not a crash-retry daemon. Application shutdown and recovery
remain in the existing FastAPI lifespan and Employee runtime rather than the supervisor.

The server classifies a missing `X-Plan-Actor` as **unattributed**, not human. `chief` is
the explicit Chief role; every other non-empty value is an attributed non-Chief agent.
Direct-only routes allow unattributed and Chief requests while rejecting worker agents.
Hosted Tailscale user requests deliberately ignore `X-Plan-Actor`; same-host internal
requests still use it as provenance.

Code paths: `src/planner/cli/main.py`, `src/planner/cli/http.py`,
`src/planner/server_lifecycle/`, `src/planner/core/authctx.py`.

## Boundaries That Matter

- The resolution engine owns Ticket field values and Ticket `stage` transitions.
- Ticket runtime writers own `ticket_status`.
- Canonical writer functions own database mutations. Most live in domain `data.py`
  files; current exceptions are called out below.
- Hermes owns Employee session history. Panels owns its intentionally visible Chat
  messages and live turn state.
- The frontend owns rendering and local edit drafts only. Within it,
  `managedMarkdown.ts` alone owns rendered Markdown DOM and preview lifetime.
- The CLI is an action surface, not a decision surface.

These boundaries are why the system is understandable: there are many surfaces, but
few places where truth can change.

## System-Level Friction

These are not local style issues. They are places where the system boundaries are
less clean than the rest.

1. **Employee-session ownership is explicit (resolved).** `ChatTurnLifecycle`
   coordinates human admission and delivery. The Ticket data writer is the single
   owner of `employee_session_id`; Chat binding calls it inside the same transaction
   that binds the running turn and returns the effective id the gateway must use.
   Day and top-level-agent Chat keys remain Chat-owned because they are not Employee
   identity.

2. **Sprint item status is a read projection.** Sprint items no longer store their
   own status. They derive `todo`, `in_progress`, `blocked`, or `done` from child
   tickets and active `links.kind='blocks'` rows. A `done` or `dropped` source clears
   its block. This keeps Automatic Employee-step eligibility and item blocking on the
   same read model.

3. **The frontend no longer copies the Ticket Stage machine (resolved).** The UI used
   to repeat stage order, gating fields, and advance targets in `web/src/lib/ui.ts`. It
   now derives them per Worker type from the served `GET /api/worker-types` manifest, through
   `web/src/lib/lifecycle.ts` — the server is the single source and the drift risk is
   closed (see `worker-types.md`). `ui.ts` keeps only the label helper and visual-state
   shapes the lifecycle builds on.

4. **Gateway bootstrap is indirect.** The adapter registry returns a real adapter
   placeholder, then production startup replaces app-state adapters with
   `SharedGateway`. This keeps tests clean, but it is not obvious from the registry
   alone.

## Deferred

- **No errored-run retry.** Startup recovery resumes work that was still active when
  Panels stopped, but an already errored Ticket has no retry or clear path. Trigger:
  an errored-run retry policy is designed.
- **No in-server rollover scheduler.** The repo provisions the agent-owned rollover
  skill; thin morning and afternoon prompts remain external to the server runtime.
- **No chat queue during worker steps.** Human sends are rejected while a ticket worker
  is active. Trigger: the product needs conversation to queue behind active work.
- **No idea conversion or archive.** Ideas stay ideas. Trigger: a decision that ideas
  should be promoted or filed away.

---

_Last verified: 2026-07-14 (Resource Catalogue ownership and targeted refresh verified)._
