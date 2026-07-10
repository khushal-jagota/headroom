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
- **Workspace** — today's tickets in a left rail backed by the board resource. The
  rail groups tickets by project, orders rows by recent ticket activity inside each
  project, shows one current-stage/status dot per ticket, filters visible rows by
  `ticket_status`, and can hide done tickets separately. The hide-done choice stays
  in place when the human visits another screen and returns.

  The right side opens on the Chief of Staff chat. Selecting a ticket switches it to
  the same complete ticket screen used by a direct ticket link while leaving the
  Workspace rail in place, and records the selection at `#/workspace/<ticket-id>`.
  That address can be loaded, refreshed, shared, or revisited with browser history;
  a missing ticket safely leaves the Chief of Staff view open.
- **Ticket** — the whole story of one piece of work: the five blanks, the scope row,
  live status markers, the `auto` run eligibility chip, chat, and a copy button that
  produces a plain-text block for pasting anywhere. Its project picker is backed by
  the shared `projects` resource.
- **Sprint** — the Overview and Tracking tabs (see `sprints.md`).
- **Backlog** and **Ideas** — the two catch surfaces (see `backlog-and-ideas.md`).

Each screen is a projection of a backend; the behaviour behind it is documented with
that backend, not here. This doc owns the shell and the rendering rules the screens
share.

## The two rules that shape it

- **Keyed invalidation, no canonical client store.** The event log is a doorbell.
  Each event maps to resource keys such as `ticket:<id>`, `board`, `queues`, and
  `sprint:current`; ticket events also map to `chat:<id>` so the ticket chat rail
  can reload the worker's full Hermes trace. Project events map to `projects`, which
  refreshes project selectors. Only those resources refetch. There
  is no client-side store mirroring the server — the server is always the source
  of truth. The ticket chat rail refreshes while a worker is running and performs
  a short settled-state retry only while the transcript is empty, because Hermes
  history can become readable a moment after the DB status/proposal event that
  triggered the first refetch.
- **The markdown renderer is hardened.** Written text (briefs, notes, ideas) renders
  through a markdown pass built so a crafted link that a browser would quietly treat
  as runnable code is impossible to express.
- **Ticket files are linked, not stored in fields.** Canonical notes, fields,
  proposals, results, and chat stay as database text. Standalone files for a ticket
  live beside the database under `files/tickets/<ticket_id>/`, so the default local
  path is `data/files/tickets/<ticket_id>/...`. The browser reads them through
  `/files/tickets/<ticket_id>/<relative-path>`. The server sends `nosniff`; only
  explicit image, audio, and video types are inline. Markdown, HTML, SVG, and
  unknown files are attachments when opened directly.
- **File previews use one contract.** Markdown turns normal links such as
  `/files/tickets/t_123/notes/plan.md` into the shared file preview component.
  Markdown files render inline through the same markdown renderer, including nested
  managed links until a fixed depth or self-link bound turns them back into compact
  preview cards. HTML files render as cards with a fetched `srcdoc` iframe in an
  empty sandbox and a new-tab action to the full Panels preview route. Images,
  video, and audio render inline; unknown files stay as download cards; ordinary
  external links stay external-link cards with deterministic host text. The full
  preview route is `#/preview?source=ticket&ticket=<id>&path=<path>`. Chat images
  under `/files/chats/<entity-id>/...` use this same component and resolver rather
  than a chat-only renderer.
- **Editable Markdown stays one surface.** Ticket notes, recaps, passed fields,
  approval drafts, and future Markdown surfaces remain directly editable with their
  existing focus, blur/save, keyboard, paste, and Escape behavior. Links stay mounted
  as atomic preview blocks while the surrounding text is edited. Each block retains
  its original Markdown link token, so saving emits ordinary Markdown and ignores
  generated images, media controls, nested Markdown, and iframe content. There is no
  separate source mode and no Edit/Save/Cancel control set. Browser edits may move an
  atomic block within the editable DOM; that move keeps its mounted component alive,
  while actual deletion still unmounts it and cancels pending work.

_Code paths:_ `web/src/App.svelte` (the shell and router), `web/src/routes/`
(one route per screen), `web/src/components/` (shared pieces), `web/src/lib/`
(API, resources, event mapping, WebSocket), `assets/tokens.css` (design tokens),
`assets/app.css` (shared styling), `assets/markdown.js` (the hardened renderer),
`web/dist/` (built app served by FastAPI).

## Handoffs

- Every backend doc owns the behaviour its screen projects — **Tickets & the gates**
  (`tickets-and-gates.md`), **Days** (`days.md`), **Sprints** (`sprints.md`),
  **Backlog & Ideas** (`backlog-and-ideas.md`), **Chat** (`chat.md`).
- **Projects** (`projects.md`) — the shared project selector resource.
- **`DESIGN.md`** (repo root) — the visual language the tokens implement.

## Deferred

- **Build artifacts are checked in for the local server.** The server serves
  `web/dist`; remember to rebuild it after Svelte changes. Trigger: a packaging
  change that builds before serving.

---

_Last verified: 2026-07-10._
