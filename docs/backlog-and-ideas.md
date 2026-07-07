# Backlog & Ideas

Backlog and Ideas are the two catch surfaces. They used to share one page; they are
now two separate pages in the top navigation, because they are two different things —
one is work you haven't scheduled, the other is a thought you don't want to lose.

## Backlog

The pile of work you have noted down but not yet put into a sprint. Its whole job is
to be scanned: the items are sorted into four groups by importance — P0 at the top
down to P3 — under quiet little headings. Each item is a single flat line showing its
title, which project it belongs to, and a deadline if it has one. It does not repeat
the priority on the line, because the group it sits in already says that. To add
something, a faint "+ New backlog item" sits at the top, closed and out of the way
until you click it, then opens a small form (title, project, priority, an optional
deadline, an optional description) with a plain "Add to backlog" button. A new item
shows up in its group a moment after you add it.

## Ideas

The opposite: its whole job is to catch a thought before you lose it, so the box to
write one is always open at the very top and is the biggest thing on the page. Type a
title and press Enter and it is saved; a longer note and a project are optional.
Below, the ideas are listed newest first. An idea with no note is just a line; one
with a note gets a small arrow you can click to open and read it. A light date on the
right — "2d", or "Jul 1" for older ones — tells you roughly when it was captured.

_Code paths:_ `assets/screens-backlog.js`, `assets/screens-ideas.js`. Backlog items
are sprint items with no sprint; ideas are their own list (`src/planner/`).

## Handoffs

- **Days** (`days.md`) — the day chat captures loose work straight into the backlog
  or ideas.
- **Sprints** (`sprints.md`) — a backlog item becomes a sprint item once it's placed
  in a sprint.

## Deferred

- **No idea → backlog conversion, and no archive.** An idea can't be promoted into a
  backlog item or filed away; that was left out on purpose for now. Trigger: a product
  decision that ideas should convert or archive in place.

---

_Last verified: 2026-07-07._
