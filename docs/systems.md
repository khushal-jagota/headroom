# Systems

Panels is one canonical planning record with several narrow ways to act on it. The
browser is the main human surface. The CLI serves direct actions, Ticket Workers, and
the Chief. A separate conversation system runs the AI agents.

```
human browser ───────────────┐
direct CLI ──────────────────┼──► domain writers ─────────────► SQLite
                             │                                      │
Ticket Worker ─► proposal resolver                                 │ commit
planning Worker ─► guarded Day or Sprint writer                    ▼
Chief ───────────► external-work reconciliation             change signal
                                                                    │
                         ┌──────────────────────────────────────────┤
                         ▼                                          ▼
                 mounted browser reads                      Worker readiness
                                                                    │
                                                                    ▼
                                                          Ticket conversation
```

The proposal resolver is the only door for gated Ticket field values and Stage
advances. It is not the only writer in Panels. Ordinary direct actions have their own
domain writers. Three planning Worker types receive narrow Day or Sprint write authority
for their Closeout. The Chief has explicit operations for importing external work.

## The systems

### 1. The record and change signal

SQLite is the canonical product record. It stores planning objects, Ticket fields and
control state, conversations, notifications, schedules, and operational receipts.
Worker settings and managed skills are filesystem state beside the database. Domain
writers group related database changes in one transaction.

Schema changes are an ordered migration history. A database open applies missing
migrations atomically and refuses an unsupported older schema instead of guessing.

Every connection created by the database door announces its commits. The signal carries
no entity name or payload. The browser invalidates its cached reads, and Worker readiness
checks current Tickets again. SQLite and periodic loops remain canonical if a signal is
missed.

_Code paths:_ `src/planner/core/db.py`, `src/planner/core/migrations/`, and
`src/planner/core/change_signal.py`.

### 2. The planning domains

Days orient one planning date. Tickets carry bounded work and own their Project and
optional Sprint placement. Sprint Items optionally classify Tickets under an outcome,
and their placement must match each classified Ticket. Backlog items are Sprint Items
without a sprint. Ideas remember possibilities. Projects classify Tickets, Items, and
Ideas.

Each domain owns its contracts, rules, writers, views, and HTTP routes. Cross-domain
actions use those owners. The planning date changes at 05:00 local time. Stored Sprint
date ranges remain canonical.

Read the focused pages for the product surfaces:

- **Days** (`days.md`)
- **Sprints** (`sprints.md`)
- **Backlog & Ideas** (`backlog-and-ideas.md`)
- **Projects** (`projects.md`)

_Code paths:_ `src/planner/days/`, `src/planner/sprints/`,
`src/planner/tickets/`, and `src/planner/projects/`.

### 3. Tickets, gates, and Worker types

A Ticket's Worker type declares its ordered Stages, gated fields, default Stage
ownership, specialist skill, and launch defaults. Eleven Worker types ship, including
coding, planning, design, debugging, general, and user-owned personal work. The browser
gets the same registry manifest that the server uses.

Workers propose gated Ticket fields. The proposal resolver alone settles one of those
values and advances the Stage. Scope controls how far worker-owned Stages can advance.
Ownership says whether the worker, user, or both drive the current Stage. A paired Stage
gets one automatic opening turn and then continues in the same Ticket conversation.

Ticket status is separate control state: `empty`, `blocked`, `agent`, `paired`,
`awaiting_approval`, `needs_user`, `user`, or `errored`. The Review screen contains
today's approval and help requests. Workspace groups today's Tickets by this operating
state.

Read **Tickets & the gates** (`tickets-and-gates.md`) and **Worker types**
(`worker-types.md`).

_Code paths:_ `src/planner/tickets/`, `src/planner/worker_types/`, and
`src/planner/worker_settings/`.

### 4. Scheduled Ticket creation

A saved schedule supplies an ordinary Ticket at one exact local minute. Its cadence is
every planning day, day four of the current sprint, or the sprint's final day. One
transaction records a created, suppressed, or failed occurrence. The loop checks only
the current minute and does not backfill downtime.

Scheduling stops at Ticket creation and Day placement. The commit signal hands the new
Ticket to ordinary Worker readiness. The Scheduled tasks screen and `panels schedule`
manage the template. Neither starts a Worker directly.

Read **Scheduled Ticket creation** (`scheduled-tickets.md`).

_Code paths:_ `src/planner/scheduled_tickets/` and `src/planner/core/loops.py`.

### 5. Worker orchestration

The readiness loop examines today's Tickets and applies one complete, read-only
decision. A Ticket must be on today, non-terminal, worker or paired owned, `empty`,
within scope, free of a parked proposal, and clear for its Closeout lane. The
conversation system supplies the one fact the record cannot: whether that Ticket's
worker is already busy.

One guarded status flip out of `empty` is the claim. There is no claim stamp or run row.
Panels then starts or reuses the Ticket conversation and sends the Stage instruction
with pending Worker context. Started and queued both count as delivered. Only refusal
releases the claim.

Nothing watches a turn end. A Ticket moves only when someone acts on it. A process crash
can therefore leave a Ticket marked `agent` with no live turn. Panels leaves that
disagreement visible.

Read **Worker orchestration** (`worker-orchestration.md`).

_Code paths:_ `src/planner/runtime/` and `src/planner/worker_context/`.

### 6. The conversation system

One conversation is one backend agent working in a folder, plus a durable notebook of
finished events. The closed backend set is Hermes, Codex, and Claude. A Ticket or the
Chief owns the conversation id. The backend's session handle remains internal and can
be rebound.

The system starts conversations, sends messages, manages held messages, interrupts or
kills activity, and reports live work that needs the user. Sending reports an honest
fate: started, queued, injected, or refused. Backend output reaches an append-only event
record and a live tail. Every production conversation pane and every Worker step uses
this same system.

The Backends screen shows installation, identity, models, updates, model enablement,
and the stored usage readings available from Codex and Claude.

Read **The conversation system** (`conversation-system.md`).

_Code paths:_ `src/planner/conversation/` and
`web/src/components/conversation/`.

### 7. The human interface

The Svelte app is built by Vite and served by FastAPI. Home, Review, Workspace, Ticket,
Sprint, Backlog, Ideas, Config, Backends, Notifications, and Scheduled tasks are server
projections. The Chief conversation is the first Workspace row. Former Agents routes
redirect to Workspace or Config.

One query catalogue names cached product reads. `GET /api/changes` streams contentless
commit signals. Each signal marks all cached reads stale, but only mounted queries
refetch. Structural sharing keeps unchanged results still. Conversations use their own
event read and live-tail path.

Markdown uses one GFM and sanitization pipeline. Managed file previews enforce safe
paths, response types, and sandboxed HTML.

Read **The front end** (`frontend.md`).

_Code paths:_ `web/src/`, `assets/`, and `web/dist/`.

### 8. The CLI and authority boundary

`panels` speaks HTTP to the server. Ordinary groups manage Days, Projects, Sprints,
Tickets, schedules, and environments. `worker` files Ticket proposals, recaps, notes,
and help requests. `chief` performs only bounded external-work intake.

Requests carry explicit actor context. Direct-only operations reject Worker claims.
The `planning-day`, `planning-midday-check`, and `planning-sprint` Workers are the narrow
exception: the server resolves the claimed Ticket's stored Worker type before it admits
the matching Day or Sprint write. Missing or mismatched claims fail closed. These local
claims narrow authority; they are not authentication credentials.

Read **The command-line tool** (`cli.md`).

_Code paths:_ `src/planner/cli/`, `src/planner/core/authctx.py`, and the domain
admission rules.

### 9. Runtime and operations

Live runs as one Git-free application with persistent data and logs. Staging uses a
prepared fake environment. Ticket servers use isolated worktrees and local state.
Launchers scrub ambient planner configuration and supply explicit paths.

Deployment builds one exact commit, verifies a persistent-state snapshot, replaces only
the app, and proves the requested revision. Failure restores both the prior app and the
pre-cutover snapshot. Backups contain the SQLite record and managed files. Web Push
projects selected Ticket and Chief events into durable delivery work.

Read **Runtime environments** (`environments.md`), **Production deployment**
(`deployment.md`), **Database backups** (`backups.md`), and **Notifications**
(`notifications.md`).

_Code paths:_ `src/planner/environments/`, `src/planner/notifications/`,
`ops/panels-environments/`, and `.github/workflows/deploy.yml`.

## Boundaries that matter

- **Proposal versus canonical value.** A Ticket Worker proposal is inert until the
  proposal resolver accepts it.
- **Signal versus state.** The change signal says only that a commit happened. Domain
  records remain canonical.
- **Status versus liveness.** Ticket status records the last control decision. The
  conversation system reports current activity.
- **Stored versus delivered context.** A Worker sees context only after that text is
  included in a delivered prompt.
- **Conversation id versus backend session.** The caller owns the first. The
  conversation system owns the second.
- **Managed skills versus packaged defaults.** `data/skills` is the live authority.
  Packaged skills seed missing entries. Codex and Claude provision all managed Panels
  skills. Hermes uses a separate allowlist.
- **Human and agent authority.** Direct actions, proposals, planning writes, and Chief
  intake use different admission paths.

## Deferred

- **Errored Ticket recovery.** An errored Ticket has no retry or clear path. Trigger: a
  product decision defines safe retry semantics.
- **Held-message durability.** A server restart loses messages still held in memory.
  Trigger: restart loss becomes important enough to persist the queue.
- **Missed schedule occurrences.** Exact-minute schedules do not backfill downtime.
  Trigger: the product adopts a recovery policy.
- **General Worker on Hermes.** The Hermes skill allowlist omits
  `panels-worker-general`, although the `general` Worker type uses it. Trigger: before a
  general Ticket runs on Hermes, add the specialist to that provisioning authority.

---

_Last verified: 2026-08-12._
