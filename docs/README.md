# The planner — the map

The planner is a personal planning system that runs entirely on one computer. It
replaces a folder of markdown files with a small database and a web page. It keeps
track of four kinds of thing — **sprints** (two-week pushes), **sprint items** (the
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
         \______  the resolution engine  ______/
                   (the single door)
                          │
                          ▼
                      the record
```

Two kinds of user, two surfaces, on purpose. The human uses the web page, where
every decision that matters lives — approving work, granting how far a worker may
go, closing things out. AI workers use a command-line tool and can only ever file
_proposals_. A piece of code called the **resolution engine** is the one thing that
can turn a proposal into a real value or move a ticket to its next stage; a worker
can never take a decision that belongs to the human.

## The systems

**The full-system view**

- **Systems** (`systems.md`) — the cold-start map: the record, ticket gate,
  runtime, ACP conversation, UI, CLI, and the main boundaries.
- **Systems artifact** (`systems.html`) — the same map as a designed, collapsible
  reading artifact.

**The core of the work**

- **Tickets & the gates** (`tickets-and-gates.md`) — what a ticket is, the stages
  it moves through, and the resolution engine, scope, and approval gate that govern
  every advance. The correctness heart of the system.
- **Worker types and settings** (`worker-types.md`) — the registry declares each workflow's
  immutable Stages, gates, fields, specialist identity, and starting Employee setup. Managed
  settings own prospective Stage defaults and editable specialist-skill content.
- **The employee runtime** (`employee-runtime.md`) — the single AI worker that
  carries each worker-owned ticket Stage forward, and the loop that fires it, watches
  it, configures its first session, and feeds proposals back through the gate.
- **Conversation** (`chat.md`) — the typed ACP pane shared by Ticket workers and the
  Chief of Staff, including the `hermes`, `codex`, and `claude` backends, live work,
  commands, permissions, and compaction state.
- **The conversation system, new** (`conversation-system.md`) — the replacement being
  built behind a fixed contract: one agent process per conversation, an append-only
  notebook of events, honest send fates, and backend cards. Serves the development
  pane today; replaces `chat.md`'s layer at the swap.
- **Runtime environments** (`environments.md`) — prepared live and staging runtime
  layouts, Ticket worktree servers, scrubbed launch, and Linux render intent.
- **Database backups** (`backups.md`) — verified SQLite snapshots and the safe operator restore.
- **Exact-commit releases** (`release-deployment.md`) — Git-free production releases and deployment.

**The surfaces you plan on**

- **Days** (`days.md`) — the daily page, the 5am boundary, and the review-first
  rollover skill.
- **Sprints** (`sprints.md`) — the Overview (Kickoff / Mid-sprint / Review) and the
  Tracking list.
- **Backlog & Ideas** (`backlog-and-ideas.md`) — the two catch surfaces.
- **Projects** (`projects.md`) — the data-backed project catalog.

**The two interfaces**

- **The front end** (`frontend.md`) — the Svelte web app: the screens, shared tokens,
  and how open screens follow the server.
- **The command-line tool** (`cli.md`) — the `panels` tool workers act through, and
  why it holds no approval powers.

## Not built yet

- **Recovery from a failed run** — an errored ticket is stuck (see `employee-runtime.md`).
- **An in-server rollover scheduler** — the agent-owned rollover skill is provisioned,
  while thin morning and afternoon prompts remain external (see `days.md`).

---

_Last verified: 2026-07-25 · Covers the system landscape; each doc carries its own
code paths._
