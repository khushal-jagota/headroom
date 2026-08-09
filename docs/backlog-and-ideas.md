# Backlog & Ideas

Backlog and Ideas are the two catch surfaces. They used to share one page; they are
now two separate pages under Planning in the More menu, because they are two different things —
one is work you haven't scheduled, the other is a thought you don't want to lose.

```
Backlog: committed work without a sprint ──► Sprint Item in a sprint
Ideas:   remembered possibility           ──► stays an Idea
```

## Backlog

The pile of work you have noted down but not yet put into a sprint. Its whole job is
to be scanned: the items are sorted into four groups by importance — P0 at the top
down to P3 — under quiet little headings. Each item is a single flat line showing its
title, which project it belongs to, and a deadline if it has one. It does not repeat
the priority on the line, because the group it sits in already says that. To add
something, a faint "+ New backlog item" sits at the top, closed and out of the way
until you click it, then opens a small form (title, project, priority, an optional
deadline, an optional description) with a plain "Add to backlog" button. A new item
shows up in its group a moment after you add it. The project selector is loaded from
the projects catalog, so new projects appear without a code change.

## Ideas

The opposite: its whole job is to catch a thought before you lose it, so the box to
write one is always open at the very top and is the biggest thing on the page. Type a
title and press Enter and it is saved; a longer note and a project are optional.
The project list is the same data-backed catalog used by backlog items and tickets.
Below, the ideas are listed newest first. An idea with no note is one line. An idea
with a note gets a small arrow that opens its detail.

_Code paths:_ `web/src/routes/BacklogRoute.svelte`,
`web/src/routes/IdeasRoute.svelte`. Backlog items and ideas live in
`src/planner/sprints/`; backlog items are Sprint Items with no sprint.

## Handoffs

- **Days** (`days.md`) — the Day page is only the daily overview right now; loose
  capture is not wired there.
- **Sprints** (`sprints.md`) — a backlog item becomes a sprint item once it's placed
  in a sprint.
- **Projects** (`projects.md`) — where the project list comes from.

## Deferred

- **No idea → backlog conversion, and no archive.** An idea can't be promoted into a
  backlog item or filed away; that was left out on purpose for now. Trigger: a product
  decision that ideas should convert or archive in place.

---

_Last verified: 2026-08-09._
