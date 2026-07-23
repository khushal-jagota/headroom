# Systems

Panels is one planning record, one gate for canonical decisions, one Automatic
Employee runtime, and one ACP conversation system. This page is the cold-start map:
what each system owns and where the important boundaries sit.

```
Human browser ───────────────► FastAPI + SQLite ◄────────────── panels CLI
      │                              │                              │
      │ decisions and conversation  │ canonical record             │ proposals only
      │                              ▼                              │
      └──────────────► ACP Conversation Composition ◄──────────────┘
                                      │
                                      ▼
                         selected ACP agent backend
```

The browser is the human decision surface. Workers use the CLI to inspect Tickets and
file proposals. The resolution engine is the only door from a proposal to a canonical
field value or Stage advance. Human and Automatic Employee prompts both reach the same
durable ACP conversation for an employee.

## The Systems

### 1. The Record System

SQLite is the canonical product record. `src/planner/core/db.py` creates the schema,
owns versioned migrations, and opens connections with foreign keys enabled. Domain
writers group related record and event changes in one transaction.

The record contains planning objects, Ticket fields and status, proposals, Stage
ownership and scope, links, the event log, pending worker context, durable ACP session
bindings, and correctness-only Employee-step runs. Conversation transcript content
belongs to the ACP backend and typed replay, not to duplicate Panels message tables.

Schema version 25 is the one-way conversation cutover. It converts only former worker
step correctness rows, interrupts a row that was running, removes the old conversation
tables and Day conversation field, clears former bindings and Ticket mirrors, and then
commits the version marker in the same transaction. Fresh schema has only the ACP-era
owners. Runtime code does not retain a compatibility path.

The event log is normally append-only. It is a doorbell and audit trail, not the source
for rebuilding the whole product. Permanent Ticket deletion is the deliberate
exception: old events for that Ticket are replaced by one deletion audit while
surviving related objects receive their own cleanup events.

_Code paths:_ `src/planner/core/db.py`, `src/planner/core/events.py`.

### 2. The Planning Objects

The planning record is made from:

- **Days**, using the configured planning-day boundary rather than midnight.
- **Sprints** and **Sprint items**, which group a bounded push and its meaningful
  chunks.
- **Tickets**, small enough to hand to one employee.
- **Ideas**, which are remembered possibilities rather than committed work.
- **Projects**, a shared classification catalog.
- **Links**, currently the explicit blocker relationship between Tickets and Sprint
  items.

Each domain owns its contracts, framework-free rules, data writers, and HTTP routes.
Cross-domain actions call those owners rather than writing around them.

_Code paths:_ `src/planner/days/`, `sprints/`, `tickets/`, `ideas/`, `projects/`,
and `src/planner/core/links.py`.

### 3. The Ticket Gate System

A Ticket's Worker type declares its ordered Stages, gated fields, default Stage
ownership, scope range, and specialist skill. The coding lifecycle is one configured
example; the engine itself uses the Ticket's stored Worker type.

Workers propose. The resolution engine alone accepts a proposal into a canonical field
and advances the Stage. Scope says how far worker-owned work may advance without human
approval. Stage ownership says whether the worker, the user, or both drive the current
Stage. Paired work receives one automatic opening turn and then continues through the
same employee conversation.

Ticket status is runtime control state. `empty`, `agent_running_step`,
`awaiting_approval`, `user_takeover`, `needs_user`, and `paired_work` say who may act next; they are
not conversation transcript states.

The Review screen is the human gate. Approval settles the proposal and records the
next scope in one decision. Returning for revision clears the parked proposal and
sends the guidance as the real next ACP worker prompt in the same durable session.

_Code paths:_ `src/planner/tickets/`, `src/planner/worker_types/`, and the resolution
engine in `src/planner/core/loops.py`.

### 4. The Employee Runtime

`AutomaticEmployeeStepDiscoveryLoop` scans only Tickets on today's planning day and
applies the complete eligibility decision. It is advisory. `EmployeeStepRunner`
rechecks eligibility under a SQLite write lock, claims the Ticket, creates one running
Employee-step record, and sends work through `AcpStepGateway`.

`employee_step_runs` is correctness state only. It records identity, status, exact
employee session, error, and timestamps. It stores no prompt, reply, transcript,
activity, usage, image, tool, or browser state. One partial index permits at most one
running step per Ticket.

The gateway prepares pending worker context into the actual model prompt before ACP
delivery. It acknowledges exact context revisions only after ACP admits that prompt.
An event or correctness row is never treated as model context.

Restart recovery uses the same durable session. It replaces a stranded running record
without replaying the original prompt, and all terminal settlements are first-wins.
The payload-free wake reduces latency after eligibility-affecting commits; SQLite and
the periodic timer remain canonical.

_Code paths:_ `src/planner/runtime/` and `src/planner/worker_context/`.

Runtime environments let the same foreground server run against separate prepared live
and staging state. Live has a fixed ingress port. Staging keeps persistent fake state
but chooses a port only while it is running. Ticket worktree servers are temporary
processes with worktree-local state, not prepared environment instances. See
[`runtime environments`](environments.md).

### 5. The ACP Conversation System

`ConversationComposition` is the one production conversation composition. It owns:

- an `AcpEmployeeRegistry` for one live child generation per employee;
- a `ConversationHub` for typed replay and browser publication;
- a `ConversationTurnBroker` for active work, Steer, Send Now, Queue, Stop, and
  compaction;
- a `ConversationPermissionBroker` for exact pending permission ownership;
- an `AcpStepGateway` for Automatic Employee work; and
- a `SqliteConversationBindingRepository` for durable session identity.

The server uses the official ACP client library. Each selected agent backend runs as a
child process and speaks ACP with Panels over standard input and output.

The browser uses one typed WebSocket at `/api/conversation`. Attach, load, and live
updates share the same employee, ACP session, binding generation, and sequence. A gap
or identity mismatch fails closed. The UI renders typed messages, thoughts, tools,
plans, terminals, permissions, receipts, connection state, and compaction boundaries.

Human and Automatic Employee prompts share the same Ticket binding. A Ticket mirrors
the binding's ACP session id in `tickets.employee_session_id`; the binding table is the
owner and the Chief has no second mirror. Binding replacement uses compare-and-swap,
and an ACP session cannot be owned by two employees.

Commands come from ACP. Conversation images are ordered inline ACP content. Durable
Ticket artifacts remain under `/files/tickets/...`; there is no conversation upload or
managed conversation-file route.

Compaction is intentionally small in Panels: show one content-free started boundary
and then one content-free completed boundary or exact failure. Panels waits up to 300
seconds to observe the terminal result. The backend's compacted context and summary
stay private. Later reload and restart resume the resulting durable session with the
typed boundary.

The production catalog contains exactly `hermes`, `codex`, and `claude`; Gemini is not
registered. Hermes provisions the planner Hermes home and skills and supports native
Steer. Codex and Claude Code do not support native Steer, so active work uses Queue or
Send Now. Claude runs one initialize-only preflight at server startup and closes that
temporary child without creating a session. Codex starts lazily on first demand.

Each Worker type supplies a default backend. A Ticket may override it during pristine
Kickoff, before a session or binding exists. Human chat and Automatic Employee work use
that same selected backend and durable session after the choice freezes.

Managed Worker and Chief settings also provide Model and Reasoning defaults. Ticket creation
copies its Worker's trio once; a new Chief conversation copies the Chief trio into its durable
empty conversation, and the first demand later creates its backend binding.
Permission is not managed or persisted. Every new session starts in backend-native full access,
and every durable-session load reasserts that mode before the runtime can be used.

_Code paths:_ `src/planner/conversation/`, `src/planner/runtime/acp_step_gateway.py`,
and `/api/conversation` in `src/planner/core/server.py`.

### 6. The Human UI System

The web app is Svelte built by Vite. FastAPI serves `web/dist` at `/`, Vite chunks
under `/_app/`, and the shared token and application CSS under `/assets/`.

Every shared Markdown surface uses one Vite-owned GFM pipeline. Raw HTML stays visible
as text, unsafe content is removed before DOM creation, and managed links keep their
exact source tokens through direct editing and preview lifecycles.

Canonical product reads use one Resource Catalogue. Events invalidate only the keyed
resources they affect, such as `ticket:<id>`, `board`, `review`, or `sprint:current`.
The server remains the source of truth; the browser does not keep a second canonical
product store.

Conversation state is separate from that REST cache. Each ACP pane owns a typed
conversation controller and reducer. Chief, Ticket, and Workspace mounts all use the
same restrained pane: bubble-less employee prose, one user pill, compact thought/tool
disclosures, one persistent status line, and permission as the only prominent blocking
inset. The ACP composer accepts available commands, text, and inline images.

Managed Ticket and generic previews share one safety contract. Ticket paths are
validated on the server, direct responses use `nosniff`, and HTML previews run in a
sandbox without same-origin privilege. Conversation images do not create stored file
routes.

_Code paths:_ `web/src/`, `assets/`, and `web/dist/`.

### 7. The CLI And Authority System

`panels` is the CLI entry point. Workers use it to inspect their exact Ticket, read the
Worker type and specialist skill, write notes and recaps, and file proposals. It has no
general approval power.

Human and service actions carry explicit actor and claim context. Direct-only
operations reject worker claims. Worker writes remain proposals. The Chief's bounded
operations do not create a second path around the resolution engine.

Worker identity uses `PLAN_TICKET_ID` and the Ticket's worker-self endpoint. The
server's ACP child receives the exact Ticket environment. Duplicate session ownership
fails instead of guessing which Ticket a worker belongs to.

_Code paths:_ `src/planner/cli/`, `src/planner/authctx.py`, and domain admission rules.

## Boundaries That Matter

- **Proposal versus canonical value.** A worker proposal is inert until the resolution
  engine accepts it.
- **Event versus state.** Events notify and audit; domain tables hold canonical values.
- **Correctness run versus conversation.** `employee_step_runs` proves ownership and
  settlement; ACP owns model input, output, and replay.
- **Stored worker context versus delivered context.** Pending context reaches the model
  only when `AcpStepGateway` includes it in an admitted prompt.
- **Binding owner versus Ticket mirror.** `conversation_session_bindings` owns ACP
  identity; a Ticket mirror supports Ticket correctness and worker lookup.
- **Product cache versus conversation reducer.** REST resources use keyed invalidation;
  the ACP pane uses strict typed sequence and generation admission.
- **Ticket file versus conversation image.** Ticket artifacts use managed paths;
  images in a prompt are inline ACP blocks.
- **Human versus worker authority.** Humans approve and grant scope. Workers propose.

## System-Level Friction

- **A settled errored Employee step has no retry policy.** Startup recovery handles a
  stranded running step, not a Ticket already settled as errored.
- **Rollover scheduling is external.** Panels provisions the role skill but does not own
  a deterministic morning/afternoon scheduler.
- **Built frontend artifacts are tracked.** Source changes still require one deliberate
  Vite build before the served app changes.

## Deferred

- An explicit retry/reset policy for errored Employee work.
- In-server rollover scheduling, if the product chooses to own it.

---

_Last verified: 2026-07-21 (three-backend ACP conversation, GFM rendering, and Employee-step runtime)._
