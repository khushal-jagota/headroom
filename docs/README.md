# The planner — the map

The planner is a personal planning system that runs entirely on one computer. It
replaces a folder of markdown files with a small database and a web page. It keeps
track of four kinds of thing — **sprints** (two-week pushes), **sprint items** (the
meaningful chunks a sprint is made of), **tickets** (pieces of work small enough to
hand to an AI worker), and **days** (one page per day) — plus a light list of
**ideas**, things worth remembering that aren't work yet.

This is the entry point. Read it to find which system owns a question, then read
that system's doc.

## The two doors

```
   THE HUMAN                              THE WORKERS (AI)
   ─────────                              ────────────────
   the web page                          the `plan` command-line tool
   every decision:                       every action is a proposal:
   approve · grant · drop · plan         draft the next blank, park it
        \                                       /
         \______  the resolution engine  ______/
                   (the single door)
                          │
                          ▼
              the record  +  the append-only event log
                 (one permanent line per change)
```

Two kinds of user, two surfaces, on purpose. The human uses the web page, where
every decision that matters lives — approving work, granting how far a worker may
go, closing things out. AI workers use a command-line tool and can only ever file
_proposals_. A piece of code called the **resolution engine** is the one thing that
can turn a proposal into a real value or move a ticket to its next stage; a worker
can never take a decision that belongs to the human. Every change, by anyone, also
writes a line into an event log that is strictly append-only — the records it
describes change, but the history of what happened is never edited or lost.

## The systems

**The core of the work**

- **Tickets & the gates** (`tickets-and-gates.md`) — what a ticket is, the stages
  it moves through, and the resolution engine, scope, and approval gate that govern
  every advance. The correctness heart of the system.
- **The employee runtime** (`employee-runtime.md`) — the single AI worker that
  carries each ticket forward, and the loop that fires it, watches it, and feeds an
  approval back in. Holds the one blocker that stops the loop running today.
- **Chat** (`chat.md`) — talking to a ticket's worker, and the slash menu of
  commands and skills.

**The surfaces you plan on**

- **Days** (`days.md`) — the daily page, the 5am boundary, and quick capture.
- **Sprints** (`sprints.md`) — the Overview (Kickoff / Mid-sprint / Review) and the
  Tracking list.
- **Backlog & Ideas** (`backlog-and-ideas.md`) — the two catch surfaces.

**The two interfaces**

- **The front end** (`frontend.md`) — the no-build web app: the screens, the single
  design-token file, and the refetch-on-signal rule.
- **The command-line tool** (`cli.md`) — the `plan` tool workers act through, and
  why it holds no approval powers.

## Not built yet

- **Recovery from a failed run** — an errored ticket is stuck (see `employee-runtime.md`).
- **Automatic daily rollover** — the day isn't auto-generated yet (see `days.md`).

---

_Last verified: 2026-07-07 · Covers the system landscape; each doc carries its own
code paths._
