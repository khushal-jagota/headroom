# The front end

The front end is the web page the human uses — where every decision that matters
lives. It is a Svelte app built by Vite. FastAPI serves the built `web/dist`
document at `/`; the Vite chunks are mounted under `/_app/`. Shared tokens,
application CSS, and the hardened markdown renderer still live in `assets/` so the
look and written-text rendering stay centralized.

## The screens

One screen per part of the system:

- **Day** — the day overview: focus, brief take, watchout, and what makes the day
  land.
- **Review** — the one-at-a-time approval walk. The approve button physically refuses
  to work until "how far may the worker go next" has been answered, both halves.
- **Board** — today's tickets by stage. It shows the same day-scoped ticket set that
  System A can poll.
- **Ticket** — the whole story of one piece of work: the four blanks, the scope row,
  live status markers, the `auto` run eligibility chip, chat, and a copy button that
  produces a plain-text block for pasting anywhere.
- **Sprint** — the Overview and Tracking tabs (see `sprints.md`).
- **Backlog** and **Ideas** — the two catch surfaces (see `backlog-and-ideas.md`).

Each screen is a projection of a backend; the behaviour behind it is documented with
that backend, not here. This doc owns the shell and the rendering rules the screens
share.

## The two rules that shape it

- **Keyed invalidation, no canonical client store.** The event log is a doorbell.
  Each event maps to resource keys such as `ticket:<id>`, `board`, `queues`, and
  `sprint:current`; ticket events also map to `chat:<id>` so the ticket chat rail
  can reload the worker's full Hermes trace. Only those resources refetch. There
  is no client-side store mirroring the server — the server is always the source
  of truth. The ticket chat rail refreshes while a worker is running and performs
  a short settled-state retry only while the transcript is empty, because Hermes
  history can become readable a moment after the DB status/proposal event that
  triggered the first refetch.
- **The markdown renderer is hardened.** Written text (briefs, notes, ideas) renders
  through a markdown pass built so a crafted link that a browser would quietly treat
  as runnable code is impossible to express.

_Code paths:_ `web/src/App.svelte` (the shell and router), `web/src/routes/`
(one route per screen), `web/src/components/` (shared pieces), `web/src/lib/`
(API, resources, event mapping, WebSocket), `assets/tokens.css` (design tokens),
`assets/app.css` (shared styling), `assets/markdown.js` (the hardened renderer),
`web/dist/` (built app served by FastAPI).

## Handoffs

- Every backend doc owns the behaviour its screen projects — **Tickets & the gates**
  (`tickets-and-gates.md`), **Days** (`days.md`), **Sprints** (`sprints.md`),
  **Backlog & Ideas** (`backlog-and-ideas.md`), **Chat** (`chat.md`).
- **`DESIGN.md`** (repo root) — the visual language the tokens implement.

## Deferred

- **Build artifacts are checked in for the local server.** The server serves
  `web/dist`; remember to rebuild it after Svelte changes. Trigger: a packaging
  change that builds before serving.

---

_Last verified: 2026-07-08._
