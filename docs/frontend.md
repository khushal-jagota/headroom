# The front end

The front end is the web page the human uses — where every decision that matters
lives. It is a Svelte app built by Vite. FastAPI serves the built `web/dist`
document at `/`; the Vite chunks are mounted under `/_app/`. Shared tokens,
application CSS, and the hardened markdown renderer still live in `assets/` so the
look and written-text rendering stay centralized.

## The screens

One screen per part of the system:

- **Day** — the day overview: focus, brief take, watchout, and what makes the day
  land. Read top to bottom in the serif voice, flat, with no boxes.
- **Review** — the one-at-a-time approval chamber: one centred decision with Skip and
  Open-ticket top-right, a labelled recap, the ask surface, and a send-back row.
  Keyboard shortcuts drive it (skip, open, approve) when the cursor is not in a text
  field, and each decision fades in as it arrives. The approve button physically
  refuses to work until "how far may the worker go next" has been answered, both
  halves.
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
- **Ticket** — the whole story of one piece of work: a serif title, a single facts
  line (status, priority, its **type** pill, due, project, sprint, take-over/copy), the
  leash written as one sentence, the recap, then the spine of stages — which stages that
  spine shows is the ticket's type's, derived from the served manifest (see below and
  `ticket-types.md`); the kickoff user note sits first in that spine, collapsed. The one raised ask surface, live status markers, the
  employee chat in serif alongside, and a copy button that produces a plain-text block
  for pasting anywhere. Its project picker is backed by the shared `projects` resource.
- **Sprint** — one tracking page that scrolls (name, a meta line, the bet, then the
  work grouped by project with loose tickets as the same group), plus a separate
  documents page for the kickoff/mid/review record (see `sprints.md`).
- **Backlog** and **Ideas** — the two catch surfaces; both capture through the same
  unboxed serif idiom (see `backlog-and-ideas.md`).

The shell itself carries a presence readout — a small spinner and "N working" — from
the running-agent count, alongside the amber Review badge.

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
- **Shared scroll areas keep their place.** Panels reserves stable scrollbar space on
  its shared vertical and horizontal scroll areas, so content does not move when a
  scrollbar appears. On a mouse or trackpad the thumb stays quiet until hover, focus,
  or active use. Touch and forced-colors modes keep the platform's visible scrollbar
  behavior.
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
  preview cards. Embedded HTML files render as cards with a fetched `srcdoc` iframe
  in an empty sandbox and a new-tab action to the Panels preview route. On that
  route, HTML is no longer wrapped in preview-card chrome: the route fetches the
  document, gives the iframe a short-lived Blob URL, revokes the old URL when the
  target changes or unmounts, and fills the available page with an empty-sandbox
  iframe. Images, video, and audio render inline; unknown files stay as download
  cards; ordinary external links stay external-link cards with deterministic host
  text. The full preview route is
  `#/preview?source=ticket&ticket=<id>&path=<path>`. Chat images under
  `/files/chats/<entity-id>/...` use this same component and resolver rather than a
  chat-only renderer.
- **Editable Markdown stays one surface.** Ticket notes, recaps, passed fields,
  approval drafts, and future Markdown surfaces remain directly editable with their
  existing focus, blur/save, keyboard, paste, and Escape behavior. Links stay mounted
  as atomic preview blocks while the surrounding text is edited. Each block retains
  its original Markdown link token, so saving emits ordinary Markdown and ignores
  generated images, media controls, nested Markdown, and iframe content. There is no
  separate source mode and no Edit/Save/Cancel control set. Browser edits may move an
  atomic block within the editable DOM; that move keeps its mounted component alive,
  while actual deletion still unmounts it and cancels pending work.

## The shared component set

The screens are assembled from a small kit of shared pieces rather than
hand-rolling the same shapes per screen. Each does one job:

- **Disclosure** — the one expand/collapse surface (a native details/summary with a
  chevron): ticket notes and recaps, sprint phases and items, ideas with bodies, the
  backlog compose form, board project sections.
- **ListRow** — the one row shape (title on the left, metadata on the right, hover):
  sprint tickets, backlog items, flat ideas, board cards. Renders as a link, a button,
  or a plain non-interactive row.
- **SectionHeading** — a quiet "Label · count" group heading.
- **ScreenHeader** — a screen's title row plus an optional meta pill.
- **Button** — the one button (or link), in a primary, quiet, or pill look.
- **Pill** — a small static tag with an optional key label (dates, counts, due, sprint).
- **Chip** — the coloured status/priority/project tags, including "blocked by".
- **StageMark** — the single stage dot showing a field's progress.
- **ApprovalBlock** — the one approval surface: an editable proposal draft, the scope
  picker, and the approve/accept action, plus a read-only mode for dropped tickets.
- **ResourceState** — the shared error / loading scaffold; shows an error line, a
  loading line, or the content. Data-empty states ("No ideas yet.") stay in the screens.
- **InlineEdit** — the one editable-markdown surface (notes, recaps, drafts).
- **MarkdownBlock** — read-only rendering through the hardened markdown renderer.
- **FilePreview** — the one file preview card/inline renderer (see the file-preview rule).
- **ChatPanel / ChatComposer** — the ticket and Chief-of-Staff chat rail and its input,
  including ordered pending image previews for picker, paste, and drop intake.
- **EnumPill** — a pill whose value is chosen from a menu (project, sprint, scope).
- **SegmentedControl** — a small set of toggle options (backlog project/priority).
- **ScopePairPicker** — the "approve until … then …" scope control.
- **ErrorLine** — a single error message line.

A ticket's stage labels and order are not baked into the frontend: they come from the
server's per-type manifest through `web/src/lib/lifecycle.ts`, keyed by each ticket's
own type (see `ticket-types.md`). `labelize` in `web/src/lib/ui.ts` remains only as the
fallback that turns a raw field/state or type id into a readable label before a manifest
has loaded. `web/src/lib/dates.ts` holds the date formatting the Day and Sprint screens
share — the short-month day label the redesign speaks in, plus the weekday name. (The two visible native selects were left un-unified on purpose —
they share almost nothing real; see `decisions.md`, D77.)

**The voice.** Every screen now speaks in the serif/sans split, amber-only accent, line
diet, and single depth-bearing ask surface that `DESIGN.md` defines — see it there, not
restated here. One caveat lives in the Sprint tracking page: its "day N of M" readout is
derived from the browser's own clock against the sprint dates, so it follows the reader's
local day, not the server's planning-day boundary.

_Code paths:_ `web/src/App.svelte` (the shell and router), `web/src/routes/`
(one route per screen), `web/src/components/` (shared pieces), `web/src/lib/`
(API, resources, event mapping, WebSocket, `labelize`, dates), `assets/tokens.css`
(design tokens), `assets/app.css` (shared styling), `assets/markdown.js` (the
hardened renderer), `web/dist/` (built app served by FastAPI).

## Handoffs

- Every backend doc owns the behaviour its screen projects — **Tickets & the gates**
  (`tickets-and-gates.md`), **Days** (`days.md`), **Sprints** (`sprints.md`),
  **Backlog & Ideas** (`backlog-and-ideas.md`), **Chat** (`chat.md`).
- **Ticket types** (`ticket-types.md`) — the served manifest the ticket screen turns
  into a per-type lifecycle to render each ticket's stages and type pill.
- **Projects** (`projects.md`) — the shared project selector resource.
- **`DESIGN.md`** (repo root) — the visual language the tokens implement.

## Deferred

- **Build artifacts are checked in for the local server.** The server serves
  `web/dist`; remember to rebuild it after Svelte changes. Trigger: a packaging
  change that builds before serving.

---

_Last verified: 2026-07-13 (per-type ticket rendering and shared scrollbar behavior verified)._
