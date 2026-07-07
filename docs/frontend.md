# The front end

The front end is the web page the human uses — where every decision that matters
lives. It is plain vanilla JavaScript with no build step: classic scripts loaded
straight into the page, no bundler. Its whole personality — colour, type, spacing —
lives in a single design-token file, so the look is changed in one place.

## The screens

One screen per part of the system:

- **Day** — the morning brief, the proposed plan with accept/invalidate per line, the
  day's tickets, and the day's chat.
- **Review** — the one-at-a-time approval walk. The approve button physically refuses
  to work until "how far may the worker go next" has been answered, both halves.
- **Board** — every ticket by stage.
- **Ticket** — the whole story of one piece of work: the four blanks, the scope row,
  links, runs, history, chat, and a copy button that produces a plain-text block for
  pasting anywhere.
- **Sprint** — the Overview and Tracking tabs (see `sprints.md`).
- **Backlog** and **Ideas** — the two catch surfaces (see `backlog-and-ideas.md`).

Each screen is a projection of a backend; the behaviour behind it is documented with
that backend, not here. This doc owns the shell and the rendering rules the screens
share.

## The two rules that shape it

- **Refetch on signal, no client state store.** The event log is a doorbell: when a
  line lands, the screen refetches the JSON it needs. There is no client-side store
  mirroring the server — the server is always the source of truth.
- **The markdown renderer is hardened.** Written text (briefs, notes, ideas) renders
  through a markdown pass built so a crafted link that a browser would quietly treat
  as runnable code is impossible to express.

_Code paths:_ `assets/tokens.css` (the design tokens), `assets/app.css`,
`assets/app.js` (the shell and router), `assets/components.js` (shared pieces),
`assets/markdown.js` (the hardened renderer), `assets/screens-*.js` (one per screen).

## Handoffs

- Every backend doc owns the behaviour its screen projects — **Tickets & the gates**
  (`tickets-and-gates.md`), **Days** (`days.md`), **Sprints** (`sprints.md`),
  **Backlog & Ideas** (`backlog-and-ideas.md`), **Chat** (`chat.md`).
- **`DESIGN.md`** (repo root) — the visual language the tokens implement.

## Deferred

- **A screen refetches wholesale on every signal.** The refetch isn't scoped to what
  changed, so a single event can re-render more than it needs. Trigger: the re-render
  cost becomes worth narrowing.

---

_Last verified: 2026-07-07._
