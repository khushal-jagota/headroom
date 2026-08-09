# The planner — the map

The planner is a personal planning system that runs entirely on one computer. It
replaces a folder of markdown files with a small database and a web page. It keeps
track of four kinds of thing — **sprints** (fixed seven-day periods), **sprint items** (the
meaningful chunks a sprint is made of), **tickets** (pieces of work small enough to
hand to an AI worker), and **days** (one page per day) — plus a light list of
**ideas**, things worth remembering that aren't work yet, and a small **projects**
catalog used by tickets, sprint items, and ideas.

This is the entry point. Read it to find which system owns a question, then read
that system's doc.

## The two doors

```
   THE HUMAN                              THE WORKERS (AI)
   ─────────                              ────────────────
   the web page                          the `panels` command-line tool
   every decision:                       every action is a proposal:
   approve · grant · drop · plan         draft the next blank, park it
        \                                       /
         \______  the proposal resolver  ______/
                   (the single door)
                          │
                          ▼
                      the record
```

Two kinds of user, two surfaces, on purpose. The human uses the web page, where
every decision that matters lives — approving work, granting how far a worker may
go, closing things out. AI workers use a command-line tool and can only ever file
_proposals_. A piece of code called the **proposal resolver** is the one thing that
can turn a proposal into a real value or move a ticket to its next stage; a worker
can never take a decision that belongs to the human.

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
- **Worker types and settings** (`worker-types.md`) — the registry declares each workflow's
  immutable Stages, gates, fields, specialist identity, and starting worker setup. Managed
  settings own prospective Stage defaults and editable specialist-skill content.
- **Worker orchestration** (`worker-orchestration.md`) — how Panels decides a ticket is
  ready for its next worker step, claims it, and sends the step into that ticket's
  conversation. It starts work; it does not watch it.
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

- **Days** (`days.md`) — the daily page, the 5am boundary, and the scheduled
  `planning-day` and `planning-midday-check` Workers.
- **Sprints** (`sprints.md`) — Project and Sprint Item tracking, each Item's Ticket
  view, and the Sprint documents.
- **Backlog & Ideas** (`backlog-and-ideas.md`) — the two catch surfaces.
- **Projects** (`projects.md`) — the data-backed project catalog.

**The two interfaces**

- **The front end** (`frontend.md`) — the Svelte web app: the screens, shared tokens,
  and how open screens follow the server.
- **The command-line tool** (`cli.md`) — the `panels` tool workers act through, and
  why it holds no approval powers.

## Not built yet

- **Recovery from a failed run** — an errored ticket is stuck (see
  `worker-orchestration.md`).

---

_Last verified: 2026-07-29 · Covers the system landscape; each doc carries its own
code paths._
