# Systems

Panels is one planning record, one gate for canonical decisions, one worker-orchestration
system, and one conversation system. This page is the cold-start map: what each system
owns and where the important boundaries sit.

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
file proposals. The proposal resolver is the only door from a proposal to a canonical
field value or Stage advance. What the human types and what Panels sends on its own
both reach the same one conversation per Ticket.

## The Systems

### 1. The Record System

SQLite is the canonical product record. `src/planner/core/db.py` opens connections with
foreign keys enabled and brings a database up to the current schema. Domain writers group
related changes in one transaction.

The record contains planning objects, Ticket fields and status, proposals, Stage
ownership and scope, links, pending worker context, and each Ticket's conversation
link. Conversation transcript content belongs to the conversation system, not to
duplicate Panels message tables.

The schema is not written out in one place. It is a numbered history of changes, kept
under `src/planner/core/migrations/`, starting from a first entry that holds the schema as
it stood when the history began. Changing the schema means adding an entry, never editing
an old one. Every open brings the database forward through whichever entries it has not
seen yet: a new database is built from the whole history, a current one is left alone, and
a database that predates the history is recorded as starting at the first entry, its rows
untouched. A database older than that is refused by name rather than half-upgraded — an
older checkout is what brings those forward. The whole step is all-or-nothing, so a change
that fails leaves the database exactly as it was.

Committing a write is also what tells the rest of the process that something changed.
The signal carries nothing — no entity, no kind, no payload — and both listeners answer
it the same way: the browser refetches what it is showing, and the readiness loop asks
again which Tickets are ready. It is a nudge for latency only; SQLite and the periodic
timer stay canonical.

_Code paths:_ `src/planner/core/db.py`, `src/planner/core/migrations/`,
`src/planner/core/change_signal.py`.

### 2. The Planning Objects

The planning record is made from:

- **Days**, using the configured planning-day boundary rather than midnight.
- **Sprints** and **Sprint items**, which group a bounded push and its meaningful
  chunks.
- **Tickets**, small enough to hand to one worker.
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

Workers propose. The proposal resolver alone accepts a proposal into a canonical field
and advances the Stage. Scope says how far worker-owned work may advance without human
approval. Stage ownership says whether the worker, the user, or both drive the current
Stage. A paired Stage receives one automatic opening turn on entry and then continues
through the same Ticket conversation; no further step is ever started for it
automatically.

Ticket status is runtime control state — one word for what is happening on the Ticket
right now, not a conversation transcript state. There are eight: `empty` (at rest and
ready), `blocked` (at rest, waiting on a live blocker), `agent` (a worker is running a
step), `paired` (working together), `awaiting_approval` (a proposal is waiting for the
human), `needs_user` (the worker asked for help), `user` (the user has taken the
stage), and `errored` (a confirmed backend Worker failure).

`blocked` is `empty`'s stand-in and nothing else's: a Ticket that comes to rest with
nothing running lands there instead of `empty` while a live blocker remains, and only
`empty` Tickets are ever started automatically.

The Review screen is the human gate, and it holds exactly the Tickets whose status is
`awaiting_approval`. Approval settles the proposal and records the next scope in one
decision. Returning for revision clears the parked proposal and sends the guidance as
the real next message into the Ticket's conversation. Replying to a parked proposal in
chat instead moves the Ticket to `paired` and out of Review; the proposal itself stays
filed.

_Code paths:_ `src/planner/tickets/`, `src/planner/worker_types/`, and the proposal
resolver in `src/planner/tickets/logic/resolution.py`.

### 4. Worker Orchestration

One loop asks, over and over, which of today's Tickets are ready for their next worker
step, and starts one for each. Ready means the record allows it — on today, not
terminal, a blank to fill, owner is not the user, status `empty`, nothing parked,
scope permits, Closeout lane free — plus one question the record cannot answer: the
conversation system is asked whether that Ticket's worker is already busy.

Taking the Ticket out of `empty` in one guarded write **is** the claim. There is no
claim stamp and no run record. The step is then sent as a real message: the opening
instruction plus any worker context that was waiting. Started and queued both count as
delivered; only a refusal gives the claim back.

Nothing watches the turn end. A Ticket moves again only when someone acts on it. That
means a Ticket's status and whether its worker is actually running can disagree after
a crash, and Panels leaves that visible rather than running a recovery sweep — the
conversation system is asked for liveness whenever it matters.

The loop wakes on the commit signal so a write that changes readiness is picked up
promptly; SQLite and the periodic timer remain canonical.

_Code paths:_ `src/planner/runtime/` and `src/planner/worker_context/`.

Runtime environments let the same foreground server run against separate prepared live
and staging state. Live has a fixed ingress port. Staging keeps persistent fake state
but chooses a port only while it is running. Ticket worktree servers are temporary
processes with worktree-local state, not prepared environment instances. See
[`runtime environments`](environments.md).

### 5. The Conversation System

This is the one way Panels talks to an AI agent. One conversation is one agent process
— hermes, codex, or claude — working in a folder, plus a permanent notebook of
everything that happened in it. Every screen that shows a conversation uses it, and so
does worker orchestration: there is no second path and no stand-in.

The rest of Panels can do exactly five things to a conversation: start it, send text
into it, interrupt its running turn, kill its activity outright, and ask whether it is
running. Plus one more question — is a permission ask waiting. Nothing else crosses the
boundary. In particular there is no read of which backend or model a conversation is on,
because those are values a caller passed in rather than questions the contract answers.

A conversation is identified by an id the caller owns. A Ticket stores its own in
`tickets.employee_session_id`; the Chief stores its own in the `agents` table. The
backend process's own session id is an internal, rebindable detail of the conversation
system and appears nowhere else.

The notebook is an append-only run of numbered rows in the same database as everything
else. A row is a finished thing: a delivered prompt, a completed agent message, a tool
call starting or finishing, a permission ask and its answer, a model change, a turn
ending. The browser reads the rows after a position over ordinary HTTP and then keeps up
over a live tail. Nothing holds a socket open to Panels.

Sending says what actually happened to that text and never more: it started a turn, it
is held until the agent is free, it was injected into a running turn, or it was refused
for a named impossibility. A busy agent is never a refusal — a message the system can
hold is held.

Steering is a per-backend fact rather than a negotiation: hermes can take text into a
running turn, codex and claude cannot, and a steer aimed at one that cannot is refused.

Which backends exist is a closed set of three, stated once, and every part of Panels
that reads a backend name goes through one door that turns text into a member of it.
What each backend is on this machine — installed, which version, signed in as whom,
which models it offers and which reasoning efforts each of those takes — is one answer,
probed when asked and kept until asked again.

_Code paths:_ `src/planner/conversation2/`, and `/api/conversation2` in
`src/planner/core/server.py`.

### 6. The Human UI System

The web app is Svelte built by Vite. FastAPI serves `web/dist` at `/`, Vite chunks
under `/_app/`, and the shared token and application CSS under `/assets/`.

Every shared Markdown surface uses one Vite-owned GFM pipeline. Raw HTML stays visible
as text, unsafe content is removed before DOM creation, and managed links keep their
exact source tokens through direct editing and preview lifecycles.

Canonical product reads are cached under one name each, such as `board`, `review`,
`sprint:current`, or a single Ticket. The server holds open a change stream at
`GET /api/changes` and sends one contentless line per committed write; the browser
marks every cached read stale and refetches only the ones a screen is currently using.
A refetch that comes back the same leaves the page alone. The server remains the source
of truth; the browser does not keep a second canonical product store.

Conversation state is separate from that REST cache. Each ACP pane owns a typed
conversation controller and reducer. Chief, Ticket, and Workspace mounts all use the
same restrained pane: bubble-less worker prose, one user pill, compact thought/tool
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
operations do not create a second path around the proposal resolver.

Worker identity uses `PLAN_TICKET_ID` and the Ticket's worker-self endpoint. The
server's ACP child receives the exact Ticket environment. Duplicate session ownership
fails instead of guessing which Ticket a worker belongs to.

_Code paths:_ `src/planner/cli/`, `src/planner/authctx.py`, and domain admission rules.

## Boundaries That Matter

- **Proposal versus canonical value.** A worker proposal is inert until the proposal
  resolver accepts it.
- **Signal versus state.** The change signal only says that something changed; domain
  tables hold canonical values.
- **Status versus liveness.** A Ticket's status is what Panels last decided; whether a
  worker is running now is the conversation system's answer, asked fresh each time.
- **Stored worker context versus delivered context.** Pending context reaches the model
  only when it is included in a message that was actually sent.
- **Conversation id versus backend session id.** The id a Ticket or the Chief names its
  conversation by is theirs and lives on their own row; the backend process's session id
  is internal to the conversation system and is rebound without anything outside it
  noticing.
- **Product cache versus conversation record.** REST resources are refetched on a
  contentless change signal; a conversation pane reads the rows after the position it
  holds and then keeps up over a live tail of the same rows.
- **Human versus worker authority.** Humans approve and grant scope. Workers propose.

## System-Level Friction

- **An errored Ticket has no way back.** Nothing retries or resets one.
- **A crash leaves a Ticket looking busy.** Its status still says a worker has it while
  nothing is running. Deliberate: liveness is asked of the conversation system, and no
  recovery machinery pretends to know better.
- **Rollover scheduling is external.** Panels provisions the role skill but does not own
  a deterministic morning/afternoon scheduler.
- **Built frontend artifacts are tracked.** Source changes still require one deliberate
  Vite build before the served app changes.

## Deferred

- An explicit retry/reset policy for an errored Ticket.
- In-server rollover scheduling, if the product chooses to own it.
- The replacement conversation system, which takes over from the in-memory stand-in
  and from the browser pane's older machinery in one swap.

---

_Last verified: 2026-07-25 (the eight Ticket statuses, one contentless change signal
per commit, and worker orchestration rebuilt on the conversation contract)._
