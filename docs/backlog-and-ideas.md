# Backlog & Ideas

Backlog and Ideas are two separate pages under Planning in the More menu because they
are two different things: one is committed work without a sprint, and the other is a
thought you do not want to lose.

```
Backlog: unscheduled Tickets ──► plan into a sprint
Ideas:   remembered possibility                    ──► stays an Idea
```

## Backlog

Backlog shows committed work that has no sprint. Active unscheduled Tickets are the
main list. They are fetched as bounded summaries and sorted into P0–P3 groups. Each
row opens the canonical Ticket in Workspace and shows only its title and Project chip.
A list that spans pages shows its range and page controls. The page does not request or
show an Outcome catalog.

A faint "+ New ticket" sits at the top, closed and out of the way. It opens a compact
form for title, kickoff context, Worker type, Project, priority, and optional deadline.
The form creates an ordinary Ticket with explicit backlog placement. Priority uses the
Project default unless you choose one. Its Worker and
Project choices come from the same server catalogs used elsewhere, so it does not
carry its own list or creation rules.

## Ideas

The opposite: its whole job is to catch a thought before you lose it, so the box to
write one is always open at the very top and is the biggest thing on the page. Type a
title and press Enter and it is saved; a longer note and a project are optional.
The project list is the same data-backed catalog used by Tickets, Sprint Items, and
ideas.
Below, the ideas are listed newest first. An idea with no note is one line. An idea
with a note gets a small arrow that opens its detail.

_Code paths:_ `web/src/routes/BacklogRoute.svelte`,
`web/src/routes/IdeasRoute.svelte`. Tickets live in `src/planner/tickets/`. Outcomes hold shared context independently of Sprint commitments. Outcomes and ideas
live in `src/planner/sprints/`.

## Handoffs

- **Days** (`days.md`) — the Day page is only the daily overview right now; loose
  capture is not wired there.
- **Sprints** (`sprints.md`) — where Tickets are scheduled and Outcomes are chosen for a Sprint.
- **Projects** (`projects.md`) — where the project list comes from.

## Deferred

- **No idea → backlog conversion, and no archive.** An idea can't be promoted into a
  backlog item or filed away; that was left out on purpose for now. Trigger: a product
  decision that ideas should convert or archive in place.

---

_Last verified: 2026-09-05._
