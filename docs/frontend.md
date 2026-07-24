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
- **Review** — the human chamber for parked Ticket proposals and Worker help requests: one centred decision with Skip and
  Open-ticket top-right, a labelled recap, the ask surface, and a send-back row.
  Keyboard shortcuts drive it (skip, open, approve) when the cursor is not in a text
  field, and each decision fades in as it arrives. The approve button physically
  refuses to work until "how far may the worker go next" has been answered, both
  halves.
- **Workspace** — today's tickets in a left rail backed by the board resource. “Today”
  follows the same 5am planning-day boundary as the Day screen; dropped tickets never
  appear. One project selector narrows the roster by each ticket's effective project,
  including **All projects** and **No project**. A ticket parented by a sprint item uses
  that item's project; a standalone ticket uses its own project. The selector does not
  close or replace an already-open ticket inspector. The rail groups the visible
  tickets into collapsible boxed **status buckets**, in a
  fixed order that puts what needs the user first: Errored, Needs you, Kickoff,
  Stopped, Taken over, Paired, Agent working, Needs approval, Closing out, Blocked,
  Done. A bucket with no tickets is not rendered; Blocked and Done start collapsed.
  Every ticket sits in exactly one bucket: its status decides first, Blocked claims
  only idle tickets, and a kickoff-stage ticket with a parked proposal sits in
  Kickoff rather than Needs approval. Paired holds both paired work and proposal
  discussion; Stopped is a ticket whose turn ended with nothing running and nothing
  asked of the user; Taken over is that same stopped condition while the user holds
  the stage. Rows carry only the ticket title and one mark, sorted by recent
  activity.

  The mark carries exactly two signals. An agent working right now spins. Otherwise
  the mark shows the reply state: a filled accent dot for a Worker reply (or
  pending permission ask) the user has not seen, the same dot greyed once the user
  has opened the ticket since that reply, and a faint ring when nothing is waiting.
  The signals combine Ticket facts with a small durable Ticket-linked ACP
  projection, so a browser reload or an unopened conversation does not invent or
  retain stale activity. A completed response stays unseen through reconnect/load
  until a new turn, explicit reset, or the user opens that Ticket; the projection
  also remembers that a reply ever completed, which is what keeps a seen reply
  distinguishable from a ticket that never had one. A response that completes while
  its Ticket is open is already seen; there is no response-generation or
  message-visibility tracking. **Chief of Staff** sits first in the rail above the
  buckets.

  On screens wider than 960px, the right side opens on the Chief of Staff conversation.
  Selecting a ticket switches it to the same complete ticket screen used by a direct
  ticket link while leaving the Workspace rail in place, and records the selection at
  `#/workspace/<ticket-id>`. That address can be loaded, refreshed, shared, or
  revisited with browser history; a missing ticket safely leaves the Chief of Staff
  view open. At 960px or less, selecting a ticket opens its standalone
  `#/ticket/<ticket-id>` page, and selecting Chief of Staff opens the standalone
  `#/chief` page.
- **Ticket** — the whole story of one piece of work: a serif title, a single facts
  line (status, priority, its **Worker type** pill, due, project, sprint, take-over/copy), the
  exact backend Worker failure reason directly below that line when one exists, the
  leash written as one sentence, the recap, then the spine of stages — which stages that
  spine shows is the Ticket's Worker type's, derived from the served manifest (see below and
  `worker-types.md`); the kickoff user note sits first in that spine, collapsed. The one raised ask surface, live status markers, the
  employee conversation in serif alongside, and a copy button that produces a plain-text block
  for pasting anywhere. During pristine Kickoff, the facts line also shows a restrained
  **Worker** pill whose choices come only from the served Employee-backend catalog. Changing
  it writes the stored Ticket choice but does not create a session. The first prompt attaches
  through that choice; accepting Kickoff may eagerly attach. Once Kickoff advances or Employee
  demand exists, the pill becomes read-only. Its project picker is backed by the shared
  `projects` resource.
- **Sprint** — one tracking page that scrolls (name, a meta line, the bet, then the
  work grouped by project with loose tickets as the same group), plus a separate
  documents page for the kickoff/mid/review record (see `sprints.md`).
- **Backlog** and **Ideas** — the two catch surfaces; both capture through the same
  unboxed serif idiom (see `backlog-and-ideas.md`).
- **Agents** — the browser navigation and page at `#/agents`. The page has exactly two
  stacked sections: **Agents**, then **Workers**. Agents contains **Chief of Staff** and
  the shared **Worker skill** (`panels-worker`). Chief of Staff opens at
  `#/agents/chief-of-staff`; it has launch defaults and its canonical editable skill,
  but no Ticket Stage table. Worker skill opens at `#/agents/worker-skill`; its name is
  read-only and its description and Markdown body edit the canonical shared role skill.
  It is presented as an Agent-like configurable role, but it has no independent launch,
  model, or Stage controls.

  Workers remains a compact list of configured Worker types. A Worker opens at
  `#/agents/workers/<worker-type>` with the same launch defaults, Stage ownership
  controls, and specialist skill editor as before. Worker and skill identities and
  lifecycle structure stay read-only. Each editable value saves independently; a failed
  save keeps the attempted value and a useful error so it can be corrected or retried.
  The layout collapses cleanly on mobile. Legacy `#/workers` and
  `#/workers/<worker-type>` addresses redirect to their Agents-page equivalents.

The shell itself carries two separate live signals. Worker presence is the small
spinner and "N working" readout from the global running-worker count. Server
connection health is the compact Connected / Reconnecting / Offline readout beside
it, driven only by the browser's event WebSocket.

Each screen is a projection of a backend; the behaviour behind it is documented with
that backend, not here. This doc owns the shell and the rendering rules the screens
share.

## The two rules that shape it

- **One Resource Catalogue, no canonical client store.** The catalogue names every
  cached server read, its endpoint and type, and the events that affect it. The cache
  engine only manages loaded values, subscribers, and overlapping requests; it knows
  nothing about Tickets or Projects. The event log is a doorbell. Events invalidate
  catalogue resources such as `ticket:<id>`, `board`, `review`, `sprint:current`,
  `workers`, `worker:<id>`, and `skills-home`, and successful UI writes apply one named
  catalogue effect immediately. Only those resources refetch. There is no whole-screen
  refetch or client-side copy of canonical state — the server remains the source of
  truth.

  A Project rename refreshes Projects, Board, today's Day, backlog Sprint items,
  Ideas, current Sprint, and an already-opened Ticket when its loaded direct Project
  matches. An opened Ticket whose first load has not settled is refreshed
  conservatively. A loaded Ticket with another Project, no Project, or no direct
  Project field is excluded. Project creation and summary-only edits refresh Projects
  only. The catalogue keeps its own private process-lifetime list of opened
  parameterized resources to make this check; the cache remains generic.

  Conversation state is intentionally outside the Resource Catalogue. Each ACP pane
  owns one typed `/api/conversation` WebSocket controller. Its session replay,
  generation, sequence, queue, permissions, terminal state, and delivery receipts are
  conversation state rather than cached REST resources. The controller reduces the
  ordered replay envelopes into a private candidate and publishes the complete
  conversation to the pane once at `ready`; an existing complete transcript remains
  visible during a refresh, while post-ready updates still render incrementally. Ticket
  `employee_session_changed` still refreshes only the matching `ticket:<id>` so the
  Ticket mirror stays current without refetching unrelated product projections.

  The event WebSocket is also the browser's connection-health owner. The shell starts
  at Reconnecting and changes to Connected only after a valid event frame or heartbeat
  frame arrives; socket open alone is not enough. Quiet heartbeats are shaped like the
  event envelope with `events: []` and the current cursor. They update liveness only:
  no event keys are mapped, no flush is scheduled, and no resource is invalidated. If a
  healthy connection closes or misses heartbeats, the shell shows Reconnecting, then
  Offline after the served heartbeat grace. Retries keep going with the same capped
  backoff. When a previously healthy connection becomes healthy again, the catalogue
  refreshes the currently subscribed resources once, in addition to normal cursor
  catch-up and keyed invalidation for missed events.

  The browser does not merge a second Panels transcript with backend history. ACP
  load/replay is the one conversation projection, and reconnect uses the same strict
  employee/session/generation boundary as live delivery. A pristine-Kickoff Ticket defers
  the pane's initial attach so merely opening the page cannot freeze its backend choice.
- **Markdown is GFM and sanitized.** Written text (briefs, notes, ideas) renders
  through a Vite-owned unified pipeline. It supports CommonMark and ordinary GFM,
  including tables, task lists, strikethrough, autolinks, reference links, fenced
  code, block quotes, thematic breaks, and nested mixed lists. Raw HTML stays visible
  as text. A strict sanitizer removes scripts, event handlers, unsafe URLs, ids,
  styles, and DOM-clobbering attributes before any DOM node is created.
- **Shared scroll areas keep their place.** Panels reserves stable scrollbar space on
  its shared vertical and horizontal scroll areas, so content does not move when a
  scrollbar appears. On a mouse or trackpad the thumb stays quiet until hover, focus,
  or active use. Touch and forced-colors modes keep the platform's visible scrollbar
  behavior.
- **Ticket files are linked, not stored in fields.** Canonical notes, fields,
  proposals, and results stay as database text. Standalone files for a ticket
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
  whose sandbox permits the file's scripts but does not grant same-origin or other
  host-page privileges. Before HTML enters either iframe lifecycle, the shared
  preview helper anchors the document's base URL to that HTML file's absolute managed
  URL, so relative stylesheet, script, image, and root-relative managed-file
  references resolve as they would if the file were opened from its `/files/...` URL.
  A new-tab action opens the Panels preview route. On that route, HTML and Markdown
  are no longer wrapped in preview-card chrome. HTML is fetched as a document, given
  to the iframe through a short-lived Blob URL, revoked when the target changes or
  unmounts, and fills the available page with the same isolated script-enabled iframe.
  Markdown is fetched as source and rendered directly through `MarkdownBlock` in the
  full-page document area, so managed links keep the same nested-preview and self-link
  bounds as embedded Markdown without repeating the file title, metadata, or open
  action. Images, video, and audio render inline; unknown files stay as download cards;
  ordinary external links stay external-link cards with deterministic host
  text. Full managed preview targets use
  `#/preview?source=ticket&ticket=<id>&path=<path>`. ACP message and tool links also use
  the shared generic/Ticket preview adapter. Conversation images are inline ACP
  content, not managed files.
- **Editable Markdown stays one surface.** Ticket notes, recaps, passed fields,
  approval drafts, and future Markdown surfaces remain directly editable with their
  existing focus, blur/save, keyboard, paste, and Escape behavior. Links stay mounted
  as atomic preview blocks while the surrounding text is edited. Each block retains
  its original Markdown link token, so saving emits ordinary Markdown and ignores
  generated images, media controls, nested Markdown, and iframe content. There is no
  separate source mode and no Edit/Save/Cancel control set. Browser edits may move an
  atomic block within the editable DOM; that move keeps its mounted component alive,
  while actual deletion still unmounts it and cancels pending work.
- **Managed Markdown has one DOM owner.** `managedMarkdown.ts` alone renders Markdown,
  mounts and unmounts file previews, turns editable preview links into atomic blocks,
  reads edited Markdown, maintains empty state, and cleans up observers and components.
  `MarkdownBlock` only supplies read-only content and presentation values.
  `InlineEdit` only coordinates focus, save, retry, keyboard, paste, Escape, and the
  choice between Markdown and plain text. A failed Markdown save keeps the exact
  attempted source so a later blur can retry without another edit.

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
- **InlineEdit** — product editing and save behavior for Markdown and plain text.
- **MarkdownBlock** — the read-only product wrapper for managed Markdown.
- **FilePreview** — the one file preview card/inline renderer (see the file-preview rule).
- **AcpConversation / AcpConversationPane** — the sole Ticket and Chief-of-Staff
  conversation surface: typed replay, connection/activity status, transcript,
  permissions, and the compact work controls.
- **ConversationComposer** — ACP commands, draft text, and ordered pending image
  previews for picker, paste, and drop intake. Images become inline ACP blocks.
- **EnumPill** — a pill whose value is chosen from a menu (project, sprint, scope).
- **SegmentedControl** — a small set of toggle options (backlog project/priority).
- **ScopePairPicker** — the "approve until … then …" scope control.
- **ErrorLine** — a single error message line.

A ticket's stage labels and order are not baked into the frontend: they come from the
server's per-Worker-type manifest through `web/src/lib/lifecycle.ts`, keyed by each
Ticket's own Worker type (see `worker-types.md`). `labelize` in `web/src/lib/ui.ts`
remains only as the fallback that turns a raw field, Stage, or Worker type id into a readable label before a manifest
has loaded. `web/src/lib/dates.ts` holds the date formatting the Day and Sprint screens
share — the short-month day label the redesign speaks in, plus the weekday name. (The two visible native selects were left un-unified on purpose —
they share almost nothing real; see `decisions.md`, D77.)

**The voice.** Every screen now speaks in the serif/sans split, amber-only accent, line
diet, and single depth-bearing ask surface that `DESIGN.md` defines — see it there, not
restated here. One caveat lives in the Sprint tracking page: its "day N of M" readout is
derived from the browser's own clock against the sprint dates, so it follows the reader's
local day, not the server's planning-day boundary.

_Code paths:_ `web/src/App.svelte` (the shell and router), `web/src/routes/`
(one route per screen), `web/src/components/` (shared pieces),
`web/src/lib/resourceCatalogue.ts` (cached reads, event dependencies, and mutation effects),
`web/src/lib/resources.svelte.ts` (the generic cache engine), `web/src/lib/ws.ts`
(the event doorbell), `web/src/lib/acp/` and `web/src/components/acp/` (the typed
conversation controller, state, transport, transcript, and composer), and the
remaining `web/src/lib/` helpers (API, Managed Markdown, `markdownPipeline.ts`,
`labelize`, dates), `assets/tokens.css` (design tokens), `assets/app.css` (shared
styling), `web/dist/` (built app served by FastAPI).

## Handoffs

- Every backend doc owns the behaviour its screen projects — **Tickets & the gates**
  (`tickets-and-gates.md`), **Days** (`days.md`), **Sprints** (`sprints.md`),
  **Backlog & Ideas** (`backlog-and-ideas.md`), **Conversation** (`chat.md`).
- **Worker types** (`worker-types.md`) — the served manifest the Ticket screen turns
  into a per-Worker-type lifecycle to render each Ticket's Stages and Worker type pill.
- **Projects** (`projects.md`) — the shared project selector resource.
- **`DESIGN.md`** (repo root) — the visual language the tokens implement.

## Deferred

- **Build artifacts are checked in for the local server.** The server serves
  `web/dist`; remember to rebuild it after Svelte changes. Trigger: a packaging
  change that builds before serving.

---

_Last verified: 2026-07-23 (Agents page, single ACP conversation pane, GFM rendering, Resource Catalogue, and shared file previews)._
