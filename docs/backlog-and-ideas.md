# Backlog & Ideas

Workers and Ideas are separate views. A Worker is committed work. An Idea preserves
a possibility. Explicit Ticket backlog defers automatic work without changing assignment.

```
Backlogged Ticket ──► resume ──► ordinary readiness and approvals
Idea: remembered possibility
```

## Workers and explicit backlog

The Workers view retains the historical `#/backlog` address. It lists bounded Ticket
summaries in P0–P3 groups. Ticket state selects active, backlogged, outside backlog,
or retired Tickets. A separate filter selects Worker type. Retired discovery requests
the canonical terminal Stage explicitly and remains paginated.
Each row opens the canonical Ticket in Workspace. Backlogged rows display their state.
Pages show their range and navigation controls.

Backlog ticket on the Ticket page prevents automatic progression. Resume ticket restores
ordinary readiness checks. Messages, proposals, approvals, assignment, history, and
artifacts remain available. A delivered turn continues. Parent Settle remains independent.
Backlog no longer means an unscheduled Ticket or an assignment change.

A faint "+ New ticket" sits at the top, closed and out of the way. It opens a compact
form for title, kickoff context, Worker type, Project, priority, and optional deadline.
The form creates an ordinary Ticket assigned to Chief. It does not implicitly defer
the Ticket. Priority uses the
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
`web/src/routes/IdeasRoute.svelte`. Tickets live in `src/planner/tickets/`. Sprint Items hold shared context independently of Sprint commitments. Sprint Items and ideas
live in `src/planner/sprints/`.

## Handoffs

- **Feedback** (`feedback.md`) — the loose capture path available from every page.
- **Days** (`days.md`) — historical Day records.
- **Orchestrators** (`sprints.md`) — assignment and independent settlement.
- **Projects** (`projects.md`) — where the project list comes from.

## Deferred

- **No idea → backlog conversion, and no archive.** An idea can't be promoted into a
  backlog item or filed away; that was left out on purpose for now. Trigger: a product
  decision that ideas should convert or archive in place.

---

_Last verified: 2026-10-16._
