# Panels — the map

Panels is a personal planning and work system that runs on one host. Its record contains
**Days**, **Sprints**, **Sprint Items**, **Tickets**, **Ideas**, **Feedback**, and **Projects**. Tickets
can carry work for an AI Worker, paired work, or user-owned personal tasks. Each Ticket
owns its Project and optional Sprint placement. An Outcome holds optional shared context across Sprints. Explicit commitments select
Outcomes before Tickets exist; Tickets keep their own scheduling. The stored Item
identity and its supervisor conversation remain stable.

This is the entry point. Read it to find which system owns a question, then read
that system's doc.

## Interfaces and authority

```
browser and direct CLI ─────┐
Sprint Item supervisor ─────┴──────────► domain writers ───────► record
                                               ▲
Ticket Worker ──field proposal──► proposal resolver
planning Worker ──guarded claim──► Day or Sprint writer
Chief ──external-work intake─────► Ticket reconciliation writer
```

The browser is the main human surface. The `panels` CLI exposes ordinary direct actions,
Ticket Worker actions, and bounded Chief intake as separate command groups.

Ticket gated fields still have one door: a Worker files a proposal, and the proposal
resolver alone can settle its value or advance its Stage. Three planning Worker types
also receive narrow authority to write their agreed Day or Sprint result at Closeout.
The Chief can import reality established outside Panels through explicit reconciliation
operations. Neither path is a general Ticket Stage setter. A Sprint Item supervisor has no
private door: it writes through the same domain writers the direct surfaces use, limited
to its own Item.

At its ceiling a Ticket either stops or proposes, and there is one approval gate — a
parked proposal waits for the user. Review holds today's parked proposals and explicit
Worker help requests. A Sprint Item conversation takes no part in that: nothing starts it
except a message from the user, and it reads the current state of its Item and Tickets
when they ask.

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
  shows a conversation — a Ticket's and the Chief of Staff's.
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
- **Backlog & Ideas** (`backlog-and-ideas.md`) — committed work and remembered possibilities.
- **Feedback** (`feedback.md`) — loose notes captured from any page and their handled history.
- **Projects** (`projects.md`) — the data-backed project catalog.

**The two interfaces**

- **The front end** (`frontend.md`) — the Svelte web app: the screens, shared tokens,
  and how open screens follow the server.
- **The command-line tool** (`cli.md`) — the `panels` tool workers act through, and
  the separation between direct approval commands and the approval-free `worker`
  subgroup.

---

_Last verified: 2026-09-15 · Covers the system landscape; each doc carries its own
code paths._
