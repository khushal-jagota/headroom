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
  tickets into collapsible boxed **status groups** — one group per ticket status, in a
  fixed order that puts what needs the user first: Errored, Needs user, Empty, User,
  Paired, Agent, Awaiting approval, Blocked, Done. A group with no tickets is not
  rendered; Blocked and Done start collapsed. Every ticket sits in exactly one group,
  and the rule is that plain: a done ticket goes to Done, every other ticket goes to
  its own status. The heading is the status name itself, so the screen holds no second
  vocabulary that could drift from the statuses, and a status it has never seen still
  gets its own group at the end rather than being dropped. Rows carry only the ticket
  title and one mark, sorted by recent activity.

  The mark carries three signals in one order of precedence, and each is one
  system's own fact rather than a blend of several. A **pure white dot** means the
  worker is waiting on a permission only the user can grant — it wins outright,
  because a turn waiting on an ask is still running and the ask is the part only the
  user can clear. Below it, a **spinner** means the worker is running right now. With
  neither, the mark shows the **reply state**: a filled accent dot for a reply the
  user has not seen, the same dot greyed once the user has opened the ticket since
  that reply, and a faint ring when nothing is waiting.

  All three come from the conversation the ticket is linked to. The first two are asked
  of the conversation system directly. The third is a comparison: the row carries where
  its conversation last had a turn end, and this browser keeps how far the reader has
  got in that conversation. A reply is waiting when the ending is past the reading.

  How far somebody has read is about that person at that screen, not about the ticket,
  so it is kept in their own browser and the server is never told. Nothing is written
  when a reply is read, which is why opening a ticket clears its dot straight away
  rather than after something refetches. The position is kept per conversation, so
  pressing New starts unread rather than inheriting the old conversation's reading. A
  browser that has never seen a conversation has read none of it, so a reply shows —
  every failure path over-shows attention rather than hiding a reply.

  **Chief of Staff** sits first in the rail above the groups.

  The right side opens on the Chief of Staff conversation. Selecting a ticket switches it to
  the same complete ticket screen used by a direct ticket link while leaving the
  Workspace rail in place, and records the selection at `#/workspace/<ticket-id>`.
  That address can be loaded, refreshed, shared, or revisited with browser history;
  a missing ticket safely leaves the Chief of Staff view open.
- **Ticket** — the whole story of one piece of work: a serif title, a single facts
  line (status, priority, its **Worker type** pill, due, project, sprint, take-over/copy), the
  exact backend Worker failure reason directly below that line when one exists, the
  leash written as one sentence, the recap, then the spine of stages — which stages that
  spine shows is the Ticket's Worker type's, derived from the served manifest (see below and
  `worker-types.md`); the kickoff user note sits first in that spine, collapsed. The one raised ask surface, live status markers, the
  worker conversation in serif alongside, and a copy button that produces a plain-text block
  for pasting anywhere. During pristine Kickoff, the facts line also shows a restrained
  **Worker** pill whose choices come only from the backends this machine actually has —
  the same answer the conversation composer's model and effort pickers read. Changing
  it writes the stored Ticket choice but does not create a session. The first prompt attaches
  through that choice; accepting Kickoff may eagerly attach. Once Kickoff advances or the
  Ticket has a conversation, the pill becomes read-only. Its project picker is backed by the shared
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
connection health is the compact Connected / Reconnecting readout beside it, which
says whether the change stream below is open.

Each screen is a projection of a backend; the behaviour behind it is documented with
that backend, not here. This doc owns the shell and the rendering rules the screens
share.

## The two rules that shape it

- **The server says "something changed"; the browser refetches what it is showing.**
  Every live-updated read the browser makes is listed in one place — its name and
  the address it comes from — so a screen asks for a resource by name and gets both.
  The reads are cached and shared: two screens asking for the same thing make one
  request. (A few one-shot reads, like the VPS status check and the worker
  configuration probe, are plain fetches and sit outside the list.)

  The server holds open a change stream and sends one line down it every time a write
  is committed through the database door. (One internal conversation cache writes
  outside that door and stays silent; it feeds no screen.) The line says nothing
  about what changed — there is no vocabulary here to keep in step with the backend. The browser answers by marking every cached
  read stale, and only the reads a screen is currently using are fetched again;
  everything else waits until something needs it. A burst of writes collapses into
  one round of refetching. A refetch that comes back the same leaves the page alone,
  so a busy stream does not make the screen flicker. A successful write from the
  browser does the same thing itself, without waiting for the stream to say so.

  There is no client-side copy of canonical state and no whole-screen reload — the
  server remains the source of truth, and the browser holds only the answers it has
  been given.

  Conversation state is deliberately not one of those cached reads. A pane reads the
  rows of its conversation after the position it holds, then keeps up over a live tail
  of the same rows — one seam, whether the pane is opening, reloading, or a second tab.
  A conversation's rows, what it is running on, and what it is waiting for are the
  record's own account rather than cached REST resources.

  The change stream is also the browser's connection-health owner. The shell starts at
  Reconnecting and says Connected while the stream is open. When the stream drops, the
  browser retries on its own and the shell says Reconnecting until it is back. Because
  anything that changed during the gap went unheard, opening the stream refetches
  what is on screen — that, plus the same refetch when the window is focused again, is
  the whole recovery story. The server sends an occasional invisible keep-alive line
  down a quiet stream, which changes nothing on screen.

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
- **File previews use one contract.** Markdown turns a link that names a managed file,
  such as `/files/tickets/t_123/notes/plan.md`, into the shared file preview component,
  and every image into that same component wherever the image is hosted. Any other link
  stays an ordinary link: a same-page anchor is still an anchor, one of this app's own
  routes still navigates inside the app, and an off-site address still goes off-site. An
  image sitting inside such a link is left alone with it.
  A preview shows the thing itself, softly rounded, with nothing drawn around it. An image
  is an image, SVG among them; a video is its own player; audio is its own control. None of
  the three carries a title, a caption, or anything to click.
  Markdown and HTML are the only two that keep a header, and the header sits above the
  document rather than around it: a strip curving over the top, carrying one accent-coloured
  link that reads "Open plan.md", with the document scrolling underneath it. Markdown renders
  inline through the same markdown renderer, including nested managed links until a fixed
  depth or self-link bound turns them into that same "Open" line on its own. HTML renders in
  a fetched `srcdoc` iframe whose sandbox permits the file's scripts but does not grant
  same-origin or other host-page privileges. Before HTML enters either iframe lifecycle, the
  shared preview helper anchors the document's base URL to that HTML file's absolute managed
  URL, so relative stylesheet, script, image, and root-relative managed-file
  references resolve as they would if the file were opened from its `/files/...` URL.
  Every other kind is a line of text in the accent colour, drawn where it was written: a
  managed file of no recognized kind is "Download archive.bin", and a bare URL handed to the
  component by a caller with nothing else to show — an agent's resource link — is "Open" that
  link, unless the address names an image, video, or audio file, in which case it is shown
  like any other. A markdown or HTML file that turns out not to be there is also a line,
  saying what the fetch found; nothing else is fetched, so nothing else can say. One file
  extension names one kind, so no file is offered as two.
  The header link opens the Panels preview route, whose targets are
  `#/preview?source=ticket&ticket=<id>&path=<path>`. On that route HTML is fetched as a
  document, given to the iframe through a short-lived Blob URL, revoked when the target
  changes or unmounts, and fills the available page with the same isolated script-enabled
  iframe. Markdown is fetched as source and rendered directly through `MarkdownBlock` in the
  full-page document area, so managed links keep the same nested-preview and self-link
  bounds as embedded Markdown, with no header of its own. ACP message and tool links also use
  the shared generic/Ticket preview adapter. Conversation images are inline ACP
  content, not managed files.
- **Editable Markdown stays one surface.** Ticket notes, recaps, passed fields,
  approval drafts, and future Markdown surfaces remain directly editable with their
  existing focus, blur/save, keyboard, paste, and Escape behavior. What is being typed
  belongs to the editor, not to the cache: a refetch that lands mid-composition never
  writes over it, so the caret, the text, and the place on the page all stay put. Links stay mounted
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
- **LiveConversation** — what makes a conversation live, and the only thing that does:
  it opens one by id, replays the rows after the one it holds and keeps going, keeps the
  messages this browser has sent that the record has not caught up with, and turns send,
  stop, answer and New into calls. The Ticket screen, the Chief of Staff and the
  development pane all mount it.
- **ConversationPane / ConversationTranscript / ConversationComposer** — what a
  conversation looks like: the rows, the one raised ask, the status line, and the
  composer with its model, effort and skill choices.
- **EnumPill** — a pill whose value is chosen from a menu (project, sprint, scope).
- **SegmentedControl** — a small set of toggle options (backlog project/priority).
- **ScopePairPicker** — the "approve until … then …" scope control.
- **ErrorLine** — a single error message line.

A ticket's stage labels and order are not baked into the frontend: they come from the
server's per-Worker-type manifest through `web/src/lib/lifecycle.ts`, keyed by each
Ticket's own Worker type (see `worker-types.md`). `labelize` in `web/src/lib/ui.ts`
remains only as the fallback that turns a raw field, Stage, or Worker type id into a readable label before a manifest
has loaded. `web/src/lib/dates.ts` holds the date formatting the Day and Sprint screens
share — the short-month day label the redesign speaks in, plus the weekday name. The two
visible native selects remain separate because they share almost nothing beyond being
native selects.

**The voice.** Every screen now speaks in the serif/sans split, amber-only accent, line
diet, and single depth-bearing ask surface that `DESIGN.md` defines — see it there, not
restated here. One caveat lives in the Sprint tracking page: its "day N of M" readout is
derived from the browser's own clock against the sprint dates, so it follows the reader's
local day, not the server's planning-day boundary.

_Code paths:_ `web/src/App.svelte` (the shell and router), `web/src/routes/`
(one route per screen), `web/src/components/` (shared pieces),
`web/src/lib/queryCatalogue.ts` (every server read, by name and address),
`web/src/lib/queryClient.ts` (the one shared cache), `web/src/lib/changeStream.ts`
(the change stream and connection health), `web/src/lib/mutate.ts` (a write, then the
refetch it earns), `web/src/lib/conversation/` and `web/src/components/conversation/`
(the conversation wire, feed, transcript and composer), and the
remaining `web/src/lib/` helpers (API, Managed Markdown, `markdownPipeline.ts`,
`labelize`, dates), `assets/tokens.css` (design tokens), `assets/app.css` (shared
styling), `web/dist/` (built app served by FastAPI).

## Handoffs

- Every backend doc owns the behaviour its screen projects — **Tickets & the gates**
  (`tickets-and-gates.md`), **Days** (`days.md`), **Sprints** (`sprints.md`),
  **Backlog & Ideas** (`backlog-and-ideas.md`), **the conversation system**
  (`conversation-system.md`).
- **Worker types** (`worker-types.md`) — the served manifest the Ticket screen turns
  into a per-Worker-type lifecycle to render each Ticket's Stages and Worker type pill.
- **Projects** (`projects.md`) — the shared project selector resource.
- **`DESIGN.md`** (repo root) — the visual language the tokens implement.

## Deferred

- **Build artifacts are checked in for the local server.** The server serves
  `web/dist`; remember to rebuild it after Svelte changes. Trigger: a packaging
  change that builds before serving.

---

_Last verified: 2026-07-25 (Workspace groups by Ticket status and its row mark carries three signals; single ACP conversation pane, GFM rendering, the change stream feeding cached reads, and shared file previews)._
