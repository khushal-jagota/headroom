# Panels — the map

Panels is a personal planning and work system that runs on one host. Its record contains
**Days**, **Sprints**, **Sprint Items**, **Tickets**, **Ideas**, and **Projects**. Tickets
can carry work for an AI Worker, paired work, or user-owned personal tasks. Each Ticket
owns its Project and optional Sprint placement. A Sprint Item is an optional outcome
classification, not the container that places a Ticket in a Sprint.

This is the entry point. Read it to find which system owns a question, then read
that system's doc.

## Interfaces and authority

```
browser and direct CLI ───────────────► domain writers ───────► record
                                               ▲
Ticket Worker ──field proposal──► proposal resolver
                                               ▲
Sprint Item supervisor ──agent review──────────┘
planning Worker ──guarded claim──► Day or Sprint writer
Chief ──external-work intake─────► Ticket reconciliation writer
```

The browser is the main human surface. The `panels` CLI exposes ordinary direct actions,
Ticket Worker actions, and bounded Chief intake as separate command groups.

Ticket gated fields still have one door: a Worker files a proposal, and the proposal
resolver alone can settle its value or advance its Stage. Three planning Worker types
also receive narrow authority to write their agreed Day or Sprint result at Closeout.
The Chief can import reality established outside Panels through explicit reconciliation
operations. Neither path is a general Ticket Stage setter.

Each Ticket ceiling uses Stop, Agent review, or User review. Agent review parks for the
exact owning Sprint Item supervisor. User Review contains only user-review proposals and
explicit Worker help requests. Automatic supervisor delivery remains later work.

## The systems

**The full-system view**

- **Systems** (`systems.md`) — the cold-start map: the record, ticket gate,
  runtime, the conversation system, UI, CLI, and the main boundaries.
- **Systems artifact** (`systems.html`) — the same map as a designed, collapsible
  reading artifact.

**The core of the work**

- **Tickets & the gates** (`tickets-and-gates.md`) — what a ticket is, the stages
  it moves through, and the proposal resolver, scope, and approval gate that govern
  every advance. The correctness heart of the system.
- **Ticket judgments** (`judgments.md`) — the optional user verdict and worker trouble
  notes, kept outside the Ticket workflow fields.
- **Worker types and settings** (`worker-types.md`) — the registry declares each workflow's
  immutable Stages, gates, fields, specialist identity, and starting worker setup. Managed
  settings own prospective Stage defaults and editable specialist-skill content.
- **Worker orchestration** (`worker-orchestration.md`) — how Panels decides a ticket is
  ready for its next worker step, claims it, and sends the step into that ticket's
  conversation. It starts work; it does not watch it.
- **Scheduled Ticket creation** (`scheduled-tickets.md`) — exact-time Ticket supply,
  durable occurrence receipts, and the boundary before Worker readiness.
- **The conversation system** (`conversation-system.md`) — the one way Panels talks to an
  agent, behind a fixed contract: one agent process per conversation, an append-only
  notebook of events, honest send fates, and backend cards. It serves every screen that
  shows a conversation — a Ticket's, the Chief of Staff's, and the development pane.
- **Runtime environments** (`environments.md`) — prepared live and staging runtime
  layouts, Ticket worktree servers, scrubbed launch, and user-service inputs.
- **Database backups** (`backups.md`) — verified SQLite snapshots and the safe operator restore.
- **Notifications** (`notifications.md`) — installable Panels, notification choices,
  and the durable Web Push path from a system fact to a phone.
- **Production deployment** (`deployment.md`) — exact-commit building, single-app replacement,
  and automatic recovery.

**The surfaces you plan on**

- **Days** (`days.md`) — the Home daily hub, the 5am boundary, and the scheduled
  `planning-day` and `planning-midday-check` Workers.
- **Sprints** (`sprints.md`) — direct Ticket placement, optional Sprint Item
  classification, Project tracking, and the Sprint documents.
- **Backlog & Ideas** (`backlog-and-ideas.md`) — the two catch surfaces.
- **Projects** (`projects.md`) — the data-backed project catalog.

**The two interfaces**

- **The front end** (`frontend.md`) — the Svelte web app: the screens, shared tokens,
  and how open screens follow the server.
- **The command-line tool** (`cli.md`) — the `panels` tool workers act through, and
  the separation between direct approval commands and the approval-free `worker`
  subgroup.

## Not built yet

- **Recovery from a failed run** — an errored ticket is stuck (see
  `worker-orchestration.md`).

---

_Last verified: 2026-08-12 · Covers the system landscape; each doc carries its own
code paths._
