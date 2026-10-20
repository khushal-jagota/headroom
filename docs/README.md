# Panels — the map

Panels is a workspace for orchestrators and Workers on one host. Chief is a special
orchestrator and an explicit Worker assignment destination. Ordinary orchestrators keep
their former Sprint Item identities, artifacts, and supervisor conversations. Tickets
remain the stored Workers. Ideas hold possibilities in the background.

The owner can settle any ordinary orchestrator. Chief never settles. Settlement changes
presentation and does not gate Worker continuation or hide approvals and messages.
Explicit Ticket backlog separately prevents automatic Worker progression. Resume restores
ordinary checks. Delete removes a Ticket when its record is no longer needed.
Sprint and Day records remain historical reads. Their live planning machinery is retired.

This is the entry point. Read it to find which system owns a question, then read
that system's doc.

## Interfaces and authority

```
browser and direct CLI ─────┐
Sprint Item supervisor ─────┴──────────► domain writers ───────► record
                                               ▲
Ticket Worker ──field proposal──► proposal resolver
direct user ──user-owned value───► proposal resolver
```

The browser is the main human surface. The `panels` CLI exposes ordinary direct actions
and Ticket Worker actions as separate command groups.

Every operation has exactly one address, and one sentence decides who may call it: you
may act on anything strictly below you. See `authority.md`.

Ticket gated fields still have one door: a Worker files a proposal, and the proposal
resolver alone can settle its value or advance its Stage. Retired planning Worker types
retain their history but no longer receive shared-plan write authority.
A direct user can complete the unset gate of the current user-owned Stage. The same
canonical transition settles the value and advances one Stage. There is no arbitrary
Stage setter. A Sprint Item supervisor has no address of its own: it calls the routes
every other principal calls, and the rule holds it to its own current child Tickets.

At its ceiling a Ticket proposes and waits, and there is one approval gate — a parked
proposal is addressed to somebody, and anyone above the Ticket can decide it. Review
holds owner-addressed parked proposals and explicit Worker help requests. A Sprint Item
conversation starts through an addressed message or the existing durable manager-notice
path. The supervisor reads canonical Item and Ticket state before it acts on a notice.

## The systems

[Navigation transactions](navigation.md) describe derived records, durable revisions, and the generic-only compatibility boundary.

**The full-system view**

- **Systems** (`systems.md`) — the cold-start map: the record, ticket gate,
  runtime, the conversation system, UI, CLI, and the main boundaries.
- **Systems artifact** (`systems.html`) — the same map as a designed, collapsible
  reading artifact.

**The core of the work**

- **Who may act** (`authority.md`) — one address per operation, and the one sentence
  that decides every call: you may act on anything strictly below you.
- **Tickets & the gates** (`tickets-and-gates.md`) — what a ticket is, the stages
  it moves through, and the proposal resolver, the ceiling, and the approval gate that govern
  every advance. The correctness heart of the system.
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
- **Diagnostic history** (`diagnostic-history.md`) — explicit offline reports, history protection, and separate expiry and compaction decisions.
- **Database backups** (`backups.md`) — verified SQLite snapshots and the safe operator restore.
- **Notifications** (`notifications.md`) — installable Panels, notification choices,
  and the durable Web Push path from a system fact to a phone.
- **Production deployment** (`deployment.md`) — exact-commit building, single-app replacement,
  and automatic recovery.

**The surfaces you plan on**

- **Historical Days** (`days.md`) — retained writing and memberships, with no live
  Today gate or planning schedule.
- **Orchestrators and historical Sprints** (`sprints.md`) — explicit Chief assignment,
  owner-controlled settlement, stable identities, and historical reads.
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

_Last verified: 2026-10-18 · Covers the system landscape; each doc carries its own
code paths._
