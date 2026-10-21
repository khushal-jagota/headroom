# The front end

The front end is the web page the human uses — where every decision that matters
lives. It is a Svelte app built by Vite. FastAPI serves the built `web/dist`
document at `/`; the Vite chunks are mounted under `/_app/`. Shared tokens and
application CSS live in `assets/`. The Markdown pipeline and managed rendering
lifecycle live under `web/src/lib/`.

Answers travel compressed. The server gzips responses over 1 KB. Conversation opens
request a bounded selected view instead of the complete history. A live event stream
never reaches the compression at all: the server decides from
the address, before the request is answered, and sends a stream down a route that has no
compression in it. That is not fussiness. Compression cannot say anything about a
response until it has seen some of the body, so it holds the response's headers back
until then — and a stream with nothing to report yet has no body to release them with.
The browser would sit there waiting to be connected, and the page would never learn that
anything had changed.

## The screens

One screen per part of the system:

- **Historical Days** — dated Day links remain readable. The primary navigation no longer
  supplies a Today planning screen. A bare former Home address opens Chief.
- **Review** — the human chamber for owner-addressed Ticket proposals: one oldest-first
  walk with a centred item, its Ticket title, and Skip and Open Ticket top-right.
  Proposal items add their labelled recap, ask, approval, and send-back controls.
  A proposal routed to a Sprint Item manager also creates a durable manager wake. Other
  non-owner holders inspect canonical Ticket state through the normal Chief and Ticket views.
  Keyboard shortcuts drive the actions that apply to the current item when the
  cursor is not in a text field, and each item fades in as it arrives. A proposal's
  approve button sends the next ceiling and owner holder together. Send-back
  stores the owner's exact attributed comment for the next worker-step prompt while
  clearing the proposal and returning the Ticket to its resting control status.
  A reply in the Ticket conversation leaves the proposal in
  Review until a decision.
- **Workspace** — an adjustable rail of orchestrator cards beside the selected conversation
  or Worker. Chief leads, keeps its portrait, and never settles. Ordinary orchestrators
  have Project and Settle above the title. Only the owner settles or reopens one.
  Existing orchestrators start active. Settlement does not change Worker eligibility.

  Each active card shows attention and ongoing work in the shared group order: Needs your
  approval, Needs your answer, Yours, Errored, Agent, Waiting on Consequences,
  Awaiting an agent's approval, then Other. Other is the sidebar name for the shared Empty
  group. Done, Backlog, and Blocked stay outside this list, even if they carry
  attention facts. Unread alone does not change a row’s category, order, or tone.
  The rail omits the Messages group. The sidebar preserves all facts, global unread
  discovery, and the shared classifier on other surfaces.
  Empty cards say “Nothing in this list.”

  Attention rows are bright. Ongoing rows and their working spinners are dim.
  Unread message dots stay bright and take precedence over working spinners.
  Single-line titles clip before a reserved mark column. Existing category members keep
  their displayed order across refreshes and selection. Entries and re-entries append
  within their category. Ordinary orchestrators use oldest-created order, then identifier.
  Active and Settled remain separate categories. Worker groups use most recent activity
  first, then title and identifier, before the rail preserves displayed positions.

  A card opens its orchestrator. A row opens its Worker. The address preserves selection
  across reload and Back. Opening a detail pane never replaces rail classification.
  Selection uses the existing bright accent outline without a background fill. The
  outline stays bright through hover and focus. Hover lights unselected card edges and
  titles. Worker rows retain the existing hover wash.
  The four utility links open Usage and Backends, Settings, Ideas, or Feedback in the
  right pane. The workspace address preserves these selections across reload and Back.
  Standalone utility routes remain available. The open utility uses the same accent-bright
  border as a selected orchestrator card.
  Settled orchestrator titles have no popup. They remain selectable and retain Reopen and conversation marks.
  Fine pointers with hover capability reveal other rail popups after 200 milliseconds. Visible
  keyboard focus reveals it immediately. Touch reveals it after a 400 millisecond hold.
  Movement, release, or cancellation stops an unfinished hold. Taps and clicks select.
  A completed hold suppresses its click. One popup swaps targets without another delay.

  Worker popups show their orchestrator, full title, direct conversation mark, existing
  full recap, and one sentence from the served Worker manifest and current facts.
  “Currently doing” requires an actual working signal. Idle Workers say “At” their stage.
  Approval, answer, assignment, error, and blocked facts use their specific wording.
  The popup has no stage ladder or named blockers. Small recaps arrive with navigation.
  A larger recap carries a version-bound reference; the visible popup reads that body
  and releases it when its owning surface closes. A changed version refuses the read.
  Orchestrator popups show their full recap in a recessed recap.
  The Chief popup states the eligible Ticket count.
  Empty orchestrator recaps use that same factual fallback. The panel stays
  inside the viewport. It takes the pointer only where it sits clear of the rail, so the
  owner can move onto it and press what is inside; over the rail rows it stays
  transparent, because the row the pointer is travelling to wins. Moving onto the panel
  or tabbing into it keeps it open. Recaps wrap to the available
  space, up to five lines, and show an ellipsis only at actual visual overflow. The title and current
  stage stay visible. Dense list rows retain their concise recap preview.

  The recessed recap also holds the ask for a fresh one, under the line it replaces.
  Pressing it empties that line, so the popup closes the space and the agent's working
  mark is what says a new recap is coming. Where there is no room for a recap at all,
  only the ask remains.

  Rows, headings, and cards move from measured old positions to new positions. Cross-group
  moves take 360–560 milliseconds according to distance. A new row opens space before
  its fade. An exit fades before its space closes. Each moving surface uses its effective
  background before motion starts. Its opaque edge uses the surrounding background,
  so an active row keeps its shape. Selected cards keep their accent border through motion.
  The travelling row and its card stay above their neighbors. Row and spinner colors blend
  across group changes. Structural updates wait while a pointer rests on a Worker row.
  Exact conversation signals and their conversation IDs stay live during that wait.
  Updates stay serial and retain the latest pending answer. Reduced motion stops CSS
  animation and scripted travel, including travel already in progress.

  The rail defaults to `clamp(17rem, 22vw, 20rem)`. Its edge resizes from 232 to 520
  pixels with a pointer or arrow keys. Double click resets it. The preference persists
  in the browser. The existing narrow layout stacks the rail and hides this splitter.

  Settled stays at the bottom of the rail, above fixed Config and Usage/Backends
  controls. These controls open the existing Config and Backends screens. Its label shows the ordinary orchestrator count. The expanded fold grows
  upward within half the space above those controls. Its header stays visible while
  active work and settled rows scroll separately. Both lists remain usable in short
  viewports. The height share is a layout value, without a preference or settings UI.
  Manual fold toggles respond immediately, independently of queued rail updates. Height,
  chevron, and body fade share one 340 millisecond beat. Late rows mount directly without
  another arrival animation. Closing returns focus to the header when necessary.
  Reduced motion skips that beat. Settle/Reopen retains its separate row choreography.

  Settling fades the active card and its rows out over 160 milliseconds before
  the Settled row arrives with the usual 140 millisecond delay and 320 millisecond
  fade. Reopening uses the same sequence in reverse: the card and headings arrive
  first, then its Workers. Layout changes share the existing 340 millisecond
  space adjustment. A closed Settled section stays closed and its count crossfades.
  Manual fold requests bypass that structural queue. Coherent settled page updates
  do not restart the fold animation. The latest manual choice wins during rapid reversal.

  Orchestrator pages show active groups first, then Backlogged and Done headers.
  Backlogged contains unfinished deferred Tickets. Done contains every completed Ticket,
  including completed Tickets that retain a backlog flag. Both headers start closed.
  Active groups start open. The sidebar excludes Backlog without a change to the shared
  classifier or unread index. Resume restores ordinary classification and readiness checks.
  Historical Day counts and Feedback labels keep their separate rules.

  Manual choices survive ordinary data refreshes in that page, including a group that
  disappears and returns. They do not persist across visits. These rounded group headers
  show ordinary Worker counts without unread suffixes. The unread facts and row dots
  remain independent.

  Settled orchestrators keep their own rows below the fold header. Their own rows can show
  their exact supervisor conversation marks. A child message never marks its parent.
  Other closed message folds expose distinct unread-conversation counts as text and
  retain an expand control. The Messages group ignores settlement. Each unread Worker, supervisor, or
  Chief conversation has its own linked row and dot. Workers retain quiet parent
  context. These rows reuse durable unread facts and normal read acknowledgments.
  Reading a conversation refreshes its dots and counts. Later messages make it
  discoverable again without reopening an orchestrator. Counts describe conversations,
  not individual messages or repeated appearances of the same conversation.

  Worker, supervisor, and Chief rows, pages, and hover popups share the same pair of
  exact-conversation marks. A spinner means that conversation is working. A dot means
  an explicit message sent to the owner remains unread in that conversation. Both can
  appear together. Ending a turn stops the spinner and never creates a dot. Approval,
  questions, assignment, errors, and child activity create neither mark. Missing signal
  data stays quiet. Historical Day, Sprint, and Feedback references use these same marks
  when their served record includes the conversation signal. Lifecycle progress stays
  separate from conversation marks.
  Fold summaries use text counts, never aggregate dots. Status group membership and
  ordering remain unchanged, including precedence for unread completed Workers.

  An open hover popup resolves its Worker, supervisor, or Chief from the latest query
  records by identity, so its title, recap, stage, and marks refresh while it stays open.
  This includes children absent from the top-level cards. Pointer presence freezes
  row positions, while the popup continues to use fresh content. Settling
  an orchestrator changes its location without changing either conversation fact.

  The Chief of Staff keeps its own exact conversation marks. A fresh pane opens in Focus. Focus shows the owner's prompts, explicit messages to the owner,
  permission requests, agent questions, and their settled answers. Failed turns, stopped turns,
  and missing explicit replies remain visible as compact system rows. Full shows every
  notebook row, every held prompt, all live
  agent text, and every tool call without a Focus fold. One header toggle switches the
  lens, and the unmodified `f` key does the same when no editable control owns it. The
  browser keeps an explicit choice across conversation switches and New within that pane.
  A fresh pane, including a fresh notification-link visit, starts in Focus.

  Complete turn boundaries settle both the Focus transcript and its rest line. A turn
  with a hidden automatic prompt has no Focus turn head. Its visible owner result rows
  still appear.

  The client advances the read position while either lens is selected, the conversation
  pane is open, the document is visible, and the window has focus. It advances through
  the newest delivered row that this browser received, even when a newer conversation
  snapshot arrived first. The client checks document focus again for each advance, so a
  click into a preview frame cannot leave stale focus permission. Each focus, blur, or
  visibility event retries the check, so focus return credits rows that arrived while
  the owner was away. The server keeps the position monotonic and clamps oversized
  values. The position is per conversation, so pressing New starts unread rather than
  inheriting the old conversation's reading. A failed advance over-shows attention rather
  than hiding a reply.

  On screens wider than 960px, the right side starts with a quiet invitation. An Item
  title opens the existing Sprint Item workspace at `#/workspace/item/<item-id>`.
  A Ticket opens the complete Ticket screen at `#/workspace/<ticket-id>`. A Ticket
  opened from inside an Item, in the rail or in the Item workspace, writes
  `#/workspace/item/<item-id>/<ticket-id>`, which is how the Item stays open behind it.
  Which of the two views the rail shows rides along as `?view=tickets` or `?view=items`,
  and only when the reader asked for the view the selection does not already imply. All
  of these addresses survive refresh, sharing, and browser history. A stale Item drops
  out of the address, keeping a Ticket that was open beside it. At 960px or less, an
  Item workspace or a Worker replaces the rail. Its inline eyebrow supplies the way back.

  A Ticket never opens as a full-screen page. Every Ticket link in the app, including
  the ones on the Sprint page and in Review, uses the Workspace address. The old
  `#/ticket/<ticket-id>` address redirects there, so shared links and stored
  notifications still work. A Ticket opens this way even when it is not on today's
  board, because the Ticket resource answers for it rather than the board card.
- **Worker** — one document with a clickable Project and human-readable Ticket type above its title and
  recap. The title is 28px, recap 17px, body 15px, labels 13px, and quiet text 11px.
  The ask for a fresh recap sits with the lifecycle controls. Pressing it empties the
  recap, which brings back the ordinary placeholder for a page with no recap yet, and the
  space moves to its new size rather than jumping.

  One card holds the current step. A pending proposal names its reviewer and keeps
  one editable ceiling/reviewer picker that opens directly with one click. A ceiling choice followed by a reviewer choice completes the gesture. An explicit raise past the visible pending stage accepts that exact
  submission through atomic Accept, with the selected onward ceiling and reviewer.
  Same/lower scope preserves the proposal. Reviewer-only changes reroute it without
  approval. Approve is a direct entry to the same picker and approval action. Send it back opens
  a note and uses the existing revision action. Pristine Kickoff keeps the existing
  Review exception: edit its proposal before approval instead of sending revision
  guidance to a Worker that does not yet have a conversation. A running step states its approved
  ceiling and reviewer in one sentence that opens the same control. Approval headings and picker options use only User / Orchestrator / Chief roles. Principal identities
  stay intact in requests.

  Steps and Artifacts are closed bands below the live card. Steps retain their circular progress marks. Each step opens its own
  text. Future stages and a separate historical record do not appear.
  Artifacts remain accessible even when no inline link exists,
  and their preview opens over the page. Blockers, backend errors, kickoff
  configuration, and conversation behavior remain available in their existing states.
  Backlog and Resume remain the deferral controls.
- **Orchestrator** — project and parent navigation, title, goal, labelled recap, the ask
  for a fresh recap, and a Settle control
  above grouped Workers. Worker rows show title and an exact-conversation dot.
  Hover or keyboard focus reuses the rail's recap and stage sentence panel. Groups keep the shared names
  and order, including exceptional states. The local page omits only the duplicate
  Messages group: unread Workers appear in their ordinary group with their own dot.
  Global Messages and rail discovery remain unchanged. Closed group headers include
  distinct unread-conversation counts as text. Artifacts occupy their own closed band
  below the Workers. Chief shares the same header, reading column, and conversation layer.
  Chief keeps an editable recap in its managed settings, through the same header control.
  Chief cannot settle and has no Project or parent navigation.
- **Historical Sprint** — retained dated links and documents remain readable. These
  screens do not supply planning or change Worker continuation. See `sprints.md`.
- **Chief children** — the Chief and ordinary orchestrators show one scoped Worker list.
  Active groups stay visible. Backlogged and Done have independent collapsible headers.
  Each Chief section retains its own bounded pages and reader choices. Ordinary
  orchestrators retain exact group counts and independent reader choices. Open groups
  request bounded scrolling windows; closed groups request counts without row bodies.
  The standalone Workers page is removed. Former `#/backlog` addresses redirect to
  Workspace. Project facts remain text. The browser New Ticket form and global
  project/type list are removed; API and CLI creation remain available.
- **Ideas** — remembered possibilities with an optional note and Project. See
  `backlog-and-ideas.md`.
- **Feedback** — open notes and handled history. Open notes link to their source page.
  Handled notes are grouped by their Ticket, with dismissed notes last. See `feedback.md`.
- **Chief of Staff** — the Chief is the first row in Workspace. On wide screens,
  selecting it opens the canonical Chief conversation beside the Workspace rail at
  `#/workspace/chief-of-staff`. On narrow screens, the same address opens the focused
  conversation. The former `#/chief`, `#/agents`, and
  `#/agents/chief-of-staff` addresses redirect into Workspace.
- **Settings** — one small route shell at `#/settings` contains Config, Scheduled
  tasks, and Notifications. It reuses their existing feature components.
  `#/settings/config` retains Config details for Chief, supervisor skills, shared Worker
  skill, and Worker settings. `#/settings/scheduled-tasks` retains schedule management.
  `#/settings/notifications` retains notification preferences and device enrollment.
  Existing feature routes and legacy Config detail aliases redirect to their exact
  sections. Section/detail route keys prevent stale Config instances.

  Schedule creation, edits, disable behavior, and retired-type restrictions remain.
  Notification preferences and browser permission behavior remain. Settings does not
  change feature authority or introduce another configuration framework.

  Config shares Usage's restrained rows and section spacing. Its Agents list opens
  launch defaults and role skills. Its Workers list shows the actual number of Stages
  from each supported manifest, in two columns on desktop and one on compact screens.
  The Usage Config tab mounts this same Config component, with links that stay under
  Usage. Settings retains its Config section and links back to Usage.

  Config details retain the shared launch picker, per-Stage owner controls, and skill
  editors. Owner writes remain serialized whole-record updates. Each skill field saves
  independently, and a failed save retains its error and retry behavior. These screens
  use one owner-only skill endpoint and the displayed skill name, including a candidate skill.

The workspace keeps its resize handle within its own visual layer. Open shell menus
cover that handle and receive pointer actions above it. The handle keeps its pointer
and keyboard controls when unobscured.

The shell retains Review and Workspace navigation. The Workspace footer supplies Settings,
Ideas, Feedback, and Usage/Backends icons. Each has an accessible name, tooltip, and keyboard
focus. Feedback retains its count. More supplies fallback destinations when the footer is
absent, including compact selected panes. A visible footer suppresses duplicate More links.
Settings replaces separate Config, Scheduled tasks, and Notifications entries.

The Feedback control is the last item in the status strip on every page. It opens a
desktop popover or a phone sheet and saves a text note with optional page context. A
device-local draft survives a close. The Feedback page and agent CLI read the durable
record from the server.

Each screen is a projection of a backend; the behaviour behind it is documented with
that backend, not here. This doc owns the shell and the rendering rules the screens
share.

## Installed app releases

The root document carries the deployed app SHA that booted the browser. The browser
checks `/api/meta` without cache reuse when it loads and when a retained page resumes.
If the server reports a different SHA, Panels shows an **Update now** action. Panels
never reloads for a new release without that action.

Conversation composers publish `panels:composition-state` on `document`, with an
`active` boolean. If the user requests an update during active composition, Panels
waits. It checks the server identity again when composition becomes inactive, then
applies the requested update. The push-only service worker and its subscription remain
in place across this page reload.

**Usage** at `#/backends` offers Tokens, Cost, Limits, and Config as tabs. The explicit
addresses are `#/backends/tokens`, `#/backends/cost`, `#/backends/limits`, and
`#/backends/config`. Limits opens first. It retains Codex and Claude identity, sign-in,
model controls, supported updates, and a backend filter. Hermes does not appear on Usage.
Config index and detail addresses retain the Usage header and tab strip. They use the
same Config editor as Settings, and detail links survive reloads. The Config tab does not
load usage records or allowance-page backend snapshots. Its shared model picker retains
its metadata reads. The first Limits visit reads its cached backend facts.
Allowance cards show the percentage left at observation, reset time, and observation
time. Cards mark snapshots that expired before the page's last check as cached and
leave current allowance unavailable.

Tokens and Cost show local Codex and Claude activity, including work outside Panels.
They offer 24-hour, 7-day, 30-day, and 90-day periods in the browser's time zone.
The chart and breakdown show observed hours or days, provider totals, and model details.
Unknown values display as a dash. Unrecorded intervals remain chart gaps.

The coverage fold exposes partial sources, import progress, and unavailable prices.
Cost follows T3's model-price calculations and prefers source-reported USD where present.
Its breakdown includes input, cache, output, cache savings, and observed speed premiums.
Unknown model prices remain unpriced. The stored price snapshot and source-reported
amounts remain distinct in the API and evidence.

Ordinary reads and period changes use stored answers. The page's **Refresh** starts
provider allowance reads and a bounded local record import independently. Imports resume
across files and providers after a read budget ends. Price refresh uses a stored catalogue
with T3's cache interval. Either source
keeps its previous records when its refresh fails. Opening Limits paints cached
backend facts before catalogue and supported update advice refresh in the background.
The [conversation system](conversation-system.md#recorded-local-usage) owns source and
counting rules.

The shared model picker keeps its allowance rings and Refresh action beside Reasoning.
Its refresh reads provider allowances only and replaces the picker's backend snapshots.
The picker stays open and reports failures in its existing feedback line.

_Usage code paths:_ `web/src/routes/BackendsRoute.svelte`, `web/src/components/usage/`,
and `web/src/lib/conversation/recordedUsageWire.ts`.

## Workspace navigation

The application holds one bounded server-owned view. It requests visible rail windows,
opened groups and the selected owner, including an explicitly selected old conversation.
The server expands each admitted row's dependencies from indexed canonical projections.
It returns exact global and section counts, so hidden history never has to enter browser
memory to make a count correct. Selected owners remain available outside rail membership.

One view admits at most 512 records and 768 KiB including metadata. Scrolling windows
request at most 60 rows and Chief pages keep their existing 30-row size. Combined requests
share a 256-row budget and preserve up to 160 semantic count windows (138 for the full
ordinary workspace). Parent pages stay stable. Other bodies share remaining capacity;
explicit viewport intent prioritizes a count-only surface. Short pages accept wheel,
touch or keyboard continuation even without native scroll overflow. Overlapping windows
replace prior rows and preserve a surviving visible row's position. Stale domain cursors
explicitly rebase, and vanished suffixes return the nearest reachable edge. Canonical saved text
is not truncated: large selected or hovered recaps have a separate surface-owned body.

The indexed view and exact aggregates update in the canonical navigation transaction.
One server coordinator reads semantic changes for all subscribers. Bookkeeping-only
revisions send coverage checkpoints, which advance command-fence coverage without another
selector pass or DOM update. Changed content still requires actual selector and rendered
DOM acknowledgements. Scope replacement, authority loss and source epoch changes revoke
old evidence; late responses cannot restore an obsolete scope. The legacy whole-workspace
protocol remains available for compatibility clients, but the application does not mount it.

Ordering keys use the installed ICU version and include that version in cursor identity.
The admitted workspace orderings are English (US) and German (Germany); other browser
languages use English (US). A changed ordering generation invalidates old cursors. This
is an explicit supported comparator boundary, not a claim that every browser ICU version
or locale produces identical arbitrary-text ordering.

## The two rules that shape it

- **The server says "something changed"; the browser checks what changed on screen.**
  Screens share named, cached reads from one query catalogue. A contentless commit signal
  starts one coalesced version probe for the resources that mounted screens currently use.
  The browser fetches only bodies whose source version changed. One probe can run at a
  time, with one pending round for further commits. The signal itself carries no domain
  vocabulary, and navigation and conversation tails retain their own scope and continuity.

  Domains declare authoritative source dependencies in one registry. Database revisions
  change in the same transaction as their source. A body and its revision come from one
  coherent read. Installed configuration and deployment status use their own captured
  authorities. A restart changes the server epoch and forces reconciliation. A late probe
  cannot mark an older body current, and a stopped coordinator cannot update the next page.

  Unregistered reads retain explicit refresh behavior. A failed version probe falls back
  to one refresh of mounted reads. Direct filesystem artifact discovery stays on that
  fallback because files can change without a database write. Hidden tabs defer commit
  refreshes until return. Confirmed browser writes keep their immediate invalidation.

  Inactive decoded response bodies have a shared least-recently-used budget of 64 entries
  and four MiB. The browser counts bytes once when each response enters the cache. An
  oversized body leaves immediately after its last observer leaves. The age limit remains
  an additional bound. Drafts and outgoing messages do not belong to this response cache.

  A confirmed conversation read also refreshes mounted queries immediately when its
  accepted position covers a previously unread owner message. Overlapping read replies
  refresh each covered interval once. Tool-only reads keep normal stream coalescing.
  This refresh asks the server for current actual-work and owner-message/read facts.
  It does not invent reply-required metadata or edit sidebar marks locally.

  There is no client-side copy of canonical state and no whole-screen reload — the
  server remains the source of truth, and the browser holds only the answers it has
  been given.

  Conversation state is deliberately not one of those cached reads. A pane reads the
  rows of its conversation after the position it holds, then keeps up over a live tail
  of the same rows — one seam, whether the pane is opening, reloading, or a second tab.
  A conversation's rows, what it is running on, and what it is waiting for are the
  record's own account rather than cached REST resources.

  The change stream is also the browser's connection-health owner. It has exactly two
  states: connected and reconnecting. The shell combines that transport fact with the
  separate deployment-status read described above. When the stream drops, the browser
  retries on its own. Unless a durable deployment problem takes precedence, the
  label says Reconnecting until it is back. Because anything that changed during the gap went
  unheard, opening the stream reconciles versions for mounted resources. Window focus
  also refreshes active reads. Unchanged versioned bodies stay in place. The server sends an occasional invisible keep-alive line
  down a quiet stream, which changes nothing on screen.

  There is no second transcript to merge: the record's rows are what a pane shows, read
  after the position it already holds and then kept up over a live tail of those same
  rows. Opening a Ticket attaches to nothing and spawns nothing — an agent starts when a
  message is sent to it — so merely looking at a Ticket during the Brief cannot freeze its
  backend choice.
- **Markdown is GFM and sanitized.** Written text (briefs, notes, ideas) renders
  through a Vite-owned unified pipeline. It supports CommonMark and ordinary GFM,
  including tables, task lists, strikethrough, autolinks, reference links, fenced
  code, block quotes, thematic breaks, and nested mixed lists. Raw HTML stays visible
  as text. A strict sanitizer removes scripts, event handlers, unsafe URLs, ids,
  styles, and DOM-clobbering attributes before any DOM node is created.
  Large read-only documents use that exact parser in one cancellable worker. FIFO jobs
  prevent a replaced preview from canceling another reader. Deferred conversation JSON
  uses the same worker and the generated canonical body schema. Replies retain their
  job identity and cannot revive an obsolete surface. Visible Markdown block groups keep
  measured placeholders when they leave the viewport. Full-text copy includes unmounted
  groups. Tables and lists retain their complete logical container, intrinsic layout, and
  numbering. An unusually large selected container can therefore exceed the ordinary DOM
  window. Editable serialization keeps its synchronous exact-source behavior.
  The worker parse wait uses the shared document skeleton. Its completed document or
  error line replaces the skeleton through the same cross-fade. Replacement and removal
  cancel the parse and release the mounted transition region.
  Images, HTML frames, PDF frames, and media players mount automatically near the viewport.
  Distant or hidden previews release native resources and text reads. Measured placeholders
  preserve their place. A custom visibility gate owns image admission, so native lazy
  loading does not leave a zero-height admitted image permanently unloaded.
- **Loopback links stay ordinary links.** Markdown does not rewrite `localhost` or
  `127.0.0.1` addresses. Panels does not expose development servers through its origin.
  Workers use durable Ticket-owned files for reviewable work.
- **Shared scroll areas keep their place.** Panels reserves stable scrollbar space on
  its shared vertical and horizontal scroll areas, so content does not move when a
  scrollbar appears. On a mouse or trackpad the thumb stays quiet until hover, focus,
  or active use. Touch and forced-colors modes keep the platform's visible scrollbar
  behavior.
- **Ticket files are linked, not stored in fields.** Ticket fields,
  proposals, and results stay as database text. Standalone files for a ticket
  live beside the database under `files/tickets/<ticket_id>/`, so the default local
  path is `data/files/tickets/<ticket_id>/...`. The browser reads them through
  `/files/tickets/<ticket_id>/<relative-path>`. The contract is read-only: a worker
  writes the file into that directory itself, and Panels serves it. The server sends
  `nosniff`; only explicit image, audio, and video types are inline. Markdown, HTML,
  SVG, and unknown files are attachments when opened directly.
- **Sprint Item files use an isolated sibling root.** Item artifacts live under
  `files/sprint-items/<sprint_item_id>/` and use
  `/files/sprint-items/<sprint_item_id>/<relative-path>`. This contract is read-only and
  applies the same safe-path, symlink, media-type, and `nosniff` response policy.
- **File previews use one contract.** Markdown turns a link that names a managed file,
  such as `/files/tickets/t_123/notes/plan.md`, into the shared file preview component,
  and every image into that same component wherever the image is hosted. Any other link
  stays an ordinary link: a same-page anchor is still an anchor, one
  of this app's own routes still navigates inside the app, and an off-site address still
  goes off-site. An image sitting inside such a link is left alone with it.
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
  `#/preview?source=ticket&ticket=<id>&path=<path>`. Wherever that document is drawn, HTML
  is fetched and given to a sandboxed iframe through a short-lived Blob URL, revoked when
  the file changes or unmounts, and fills the space it is given. Markdown is fetched as
  source and rendered directly through `MarkdownBlock`, so managed links keep the same
  nested-preview and self-link bounds as embedded Markdown, with no header of its own.
  ACP message and tool links also use the shared generic/Ticket preview adapter.
  Conversation images and files use conversation-owned managed bytes. Images render
  inline. Supported documents and data files render as named cards with preview and
  download actions. PDF uses a document frame. Text and data use a bounded text preview.
- **On a Ticket or Sprint Item screen, that link opens the file in place.** A managed
  file clicked inside either reading area does not go to the preview address. The screen catches the click
  and draws the file over its own reading area, with a strip carrying the file's name and
  a way out. The Ticket keeps its layout and its scroll place underneath, so closing gives
  back the page the reader left, and the conversation keeps its own section at the bottom
  of the page — the artifact and the worker are on screen together, which is the point.
  An opened conversation is the whole page, so opening a file steps it back to peeked.
  Escape closes the file. This is the same on every screen width. A click asking for a
  new tab or window is left alone. On the Workspace the open file rides in the address
  beside the Ticket, so a reload, Back, and a shared link all show it. The
  `#/preview` address remains the way in from anywhere else — a shared link, a
  notification, or another screen — and it draws the same document.
- **Artifact strips use one component.** Sprint Items list their files newest first.
  Tickets list only managed files linked from their lifecycle fields and pending proposal.
  A chip shows the file name without its extension and then the file type. Matching names
  add their parent folder. More than six files collapse to five chips and a count on a
  wide pane. At 720 pixels or less, every chip stays on one horizontally scrolling line.
  The strip is absent when it has no files.
- **Editable Markdown stays one surface.** Ticket recaps, passed fields,
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
  Saving an approval draft on either the Ticket or Review screen keeps the edited text
  with that visible proposal. Approval sends it together with the proposal revision,
  so it cannot accept an unseen replacement.
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
  chevron): Ticket recaps, sprint phases and items, ideas with bodies, the
  Backlog Ticket form, board project sections.
- **ListRow** — the one row shape (title on the left, metadata on the right, hover):
  sprint tickets, Backlog Tickets, flat ideas, and board cards. Renders as a
  link, a button, or a plain non-interactive row.
- **SectionHeading** — a quiet "Label · count" group heading.
- **ScreenHeader** — a screen's title row plus an optional meta pill.
- **Button** — the one button (or link), in a primary, quiet, or pill look.
- **Pill** — a small static tag with an optional key label (dates, counts, due, sprint).
- **Chip** — the small project tag, and the plain tag with no variant.
- **StageMark** — the single stage dot showing a field's progress.
- **WorkerProposalCard** — the single approval card on both the Ticket and Review
  screens: editable proposal, reviewer heading, Approve, and send-back action. Approve
  opens the existing compact ceiling/reviewer rail directly; there is no adjacent
  ceiling control. The reviewer heading opens the same picker for reviewer-only
  routing. A Ticket reviewer is labelled Worker, distinct from its assigned Orchestrator.
  A new proposal revision resets the draft; an unrelated refresh preserves it.
  Review supplies its existing voice revision box through the card.
- **ReviewProposalCard** — one waiting proposal as a card: the ticket's title and recap,
  the approval control and the send-back box. It is named by a
  ticket id and a field and reads that ticket itself, so any screen can raise the same
  ask. Which ask is current — walking, skipping, the keyboard shortcuts — stays with the
  screen. The Review screen mounts it.
- **ResourceState** — the shared error / loading scaffold. It asks what the screen has
  to show before it asks what went wrong: a screen that has data keeps showing it and
  puts a failed read as a line above it, and only a screen with nothing yet is given
  over to the error line or a skeleton. Each screen selects a named shape: rows,
  document, block, conversation, or inline. `loadingText` remains an accessible
  status announcement. Data-empty states ("No ideas yet.") stay in the screens.
  `SkeletonRegion` owns the shared shapes and cross-fade, including inline values.
  Waits reserve their current space immediately and show a skeleton after 200ms,
  only if work remains pending. The stable status node announces after 100ms;
  busy state follows actual work immediately. Ready content mounts at once and
  fades in over 300ms, including cached content and initial idle mounts. Existing
  child opacity entrances keep their own cadence. A shown
  skeleton fades out from its actual opacity without a minimum hold.
  Layouts size full panes. Intrinsic content keeps its shape reservation during
  the delay and any skeleton exit, then returns to natural height. This can still
  move following content; the reservation does not predict the content.
  Four skeleton tokens control the whisper fills, fade duration, and visual delay.
  The mounted region reads the inherited delay; the announcement uses half of it.
  Flow content keeps its transparent wrappers throughout the fade so flex/grid
  gaps and margins stay put. The fade reaches painted element roots through
  transparent Markdown hosts and keeps their existing transform animations.
  Flow snippets supply element roots; raw text uses an inline region.
  Reduced motion keeps the delay and replaces immediately, accepting one static
  skeleton frame when completion follows appearance. Changing that preference
  during a fade clears the transition. Worker activity marks remain separate.
  Action buttons keep their own busy labels. The model picker's Refresh button
  says “Reading…” and exposes `aria-busy` during its explicit refresh. This label
  describes the action, not a region without content, so it is outside skeleton scope.
- **InlineEdit** — product editing and save behavior for Markdown and plain text.
- **MarkdownBlock** — the read-only product wrapper for managed Markdown.
- **FilePreview** — the one file preview card/inline renderer (see the file-preview rule).
- **LiveConversation** — what makes a conversation live, and the only thing that does:
  it opens one selected server view, keeps bounded history pages and one selected tail,
  and retains this browser's messages until the durable record acknowledges them.
  It turns send, stop, answer and New into calls. The Ticket screen and Chief both mount it.
  Fresh panes request Focus. Full requires an explicit choice, which survives conversation
  switches within that pane. The server owns lens visibility, turn folds, and work groups.
  Historical page order is separate from delivered read coverage. Loading older history
  never acknowledges newer messages. During a page read, stream admission pauses and
  resumes from unchanged coverage with all retained ranges.

  A page holds at most 100 items and 128 KiB of ordinary bodies. The selected reader keeps
  at most 300 items, 512 KiB of ordinary bodies, and 20 scope windows. The viewport mounts
  at most 150 nearby message bodies. Large bodies use versioned source references and
  visible demand. They keep their full text, formatting, and copy behavior. Rest and hidden
  panes release transcript bodies, requests, and previews. Drafts, pending sends, logical
  scroll anchors, and bounded disclosure identities stay outside that expendable view.
  Reconnects reconcile pending send receipts even when durable coverage does not advance.
- **ConversationPane / ConversationTranscript / ConversationComposer** — what a
  conversation looks like: the rows, the one raised ask, the status line, and the
  composer with its model, effort and skill choices. The composer also owns pending
  pictures, documents, and data files from the pickers, clipboard, and drag-and-drop.
  It removes attachments individually and hands one content run to `LiveConversation`.
  Messages that contain only attachments use that same path. A file-bearing message that
  still waits for its durable row keeps its bytes in browser database storage, so a reload
  restores it across the full supported file-size range.
  Its delivery control defaults to Steer and also offers Queue and Send now. A held row
  names why Panels queued a steer fallback.

  The textarea grows with its content up to six whole lines, then scrolls internally.
  The same cap applies at rest, peeked, and opened. Pane height and focus do not change it.
  A hidden textarea measures each draft with the same text layout and scrollbar width.
  Only the final height and overflow reach the visible input. A same-line edit therefore
  keeps the transcript in place without a temporary collapse of the composer.
  Pending pictures and files share one horizontal strip in attachment order. Pictures
  show thumbnails; files show their type, name, and size. The footer keeps one row at
  phone widths, with a truncated model name and equal-height controls. Stop keeps its
  transparent background and error-colored outline. Send is the only filled control.

  The pane's options menu, under the `⋯` control in its head, holds New conversation and,
  for a screen that keeps earlier conversations, the picker that opens one. Nothing is
  drawn above the card for either. A screen with no earlier conversation shows no picker,
  and the Ticket page is that screen. While an earlier conversation is on screen the pane
  is read only: the actions go, the picker stays, and it is the way back to the current
  conversation.

  The transcript uses native browser scroll behavior. A reader who follows the end
  keeps the newest content in sight. A reader who scrolls back leaves that mode.
  The browser holds the top line when it can. If content above or within the view
  grows, text below it can move. Panels does not restore an exact paragraph offset.
  Opening the conversation, sending a message, and Latest each place the view once.
  An explicit send places the sent prompt near the top, even during a layer resize.
  If a shorter transcript clamps the view to the end, later content follows the end.
  Latest reflects the actual content, separately from the answer room below a sent
  message. Reply content consumes that room. A resize counts the reply already present
  and cannot recreate exhausted room. Exact text preservation during image growth
  is not a requirement for this release. Further conversation refinement is separate work.

  On the Ticket screen the conversation is a layer along the bottom of the page rather
  than a column beside it, and it has **three states**. At **rest** it is the composer and
  one line above it saying what happened last — whoever produced it, so your own message
  can be that line, and so can a request waiting on you. Clicking the input **peeks** it
  open: a card over the page, with the line gone because the transcript now says the same
  thing. A control takes it **opened**, the full height of the page, and the same control
  brings it back. Clicking the ticket behind it, or pressing Escape, drops it one state.

  Three things are true of all three. Nothing moves it but a person — a request arriving
  or a turn starting never opens it, which is why the line at rest has to carry that news.
  It is a layer and never a mode: nothing is locked, and the ticket underneath stays
  readable and scrollable. And it is one conversation at three heights, never three
  screens. A move between peeked and opened keeps the reader's place in the transcript.
  Reopening from rest lands on the newest unanswered request or the end of the conversation.
  Every state keeps whatever the person had half-typed.

  The Chief and ordinary orchestrators share one layout with this layer and the same
  outside-click dismissal. Dismissal uses the start of a primary pointer gesture.
  A text selection that starts inside the conversation can end outside without dismissal.
  Ticket views use the same rule. Both start at rest. The person controls rest, peeked, and
  opened states, and a refresh keeps that choice.
- **ComposerCatalogMenu** — the typed list that opens when a composer line starts with
  `/`, `$`, or `@`. Slash offers commands, dollar offers skills, and at offers apps and
  plugins. The list narrows as text is typed. A choice inserts its exact catalog text
  into the draft. Sending and transcript display still use ordinary text. Codex resolves
  that text at its adapter boundary. The browser does not store vendor identifiers or
  construct structured Codex input.
- **SegmentedControl** — a small set of toggle options.
- **CeilingPicker** — the whole ceiling in one control. Both halves — how far the Worker
  may go, and who reviews it — sit in a rail on the left, so either can be edited on its
  own. A half-finished choice stays in the open panel: dismissing it writes nothing and
  leaves nothing behind, and only a completed pair reaches the Ticket. The running-step card keeps its own trigger. In a pending proposal, Approve and
  the reviewer heading are the entry points to that same picker.
- **PickerRail**, **PickerRailRow** — the left rail a picker panel uses to name the parts
  of the thing it edits. The model picker and the ceiling picker share it.
- **ErrorLine** — a single error message line.

The Worker page uses the same ceiling/reviewer control while running or waiting.
A pending proposal no longer locks its stage half. The compact panel shows only the existing ceiling/reviewer rail and choices. Approval uses the visible
proposal revision as an atomic freshness check. A changed or rerouted proposal must
be read again before approval. Existing CLI accept callers remain compatible.
Historical pending proposals still require an explicit decision, including those below
the ceiling. New answers settle below the ceiling regardless of Stage ownership.

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
restated here. The Sprint tracking page derives its "day N of M" readout from the
canonical sprint day. That day changes at 05:00 local time, in step with the server's
planning-day boundary.

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

## Demand for page content

Worker Steps retains its count and approval summaries while closed. Historical field
bodies load only when their existing folds open. An unloaded body is different from an
empty field. Logical drafts and save errors survive closed-body eviction. The current
proposal, controls, and assigned gate body retain coherent admission against current facts.

Artifacts and prior conversations use separate bounded metadata pages. Artifact folders
retain the existing expansion and preview links. Filesystem artifacts precede proposal
links and historical field links. The server indexes Markdown links at source writes, so
closed fields need no body download merely to discover an artifact. The selected owner's
filesystem reconciliation preserves descendant ordering, empty-folder suppression, and
files written outside the API. It runs off the server event loop and has a bounded cache.

The conversation picker retains selection, keyboard movement, focus return, and typeahead
across unloaded history. SQL indexes scope pages and selected-position reads to the owner.
Localized typeahead scans bounded cursor batches when a label is outside the loaded page.
The browser retains a bounded window of entries, rather than every historical label.

The app keeps the workspace available initially and imports other routes and selected
inspectors on demand. Hashed Vite assets use immutable caching for one year. HTML and
unhashed assets revalidate so the next load sees the current deployment.

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

_Last verified: 2026-10-18._


## Frontend test commands

`npm --prefix web run test:vitest` runs runtime tests under Node and checks the full
TypeScript test project. Its normal footer lists the runtime files that ran and names
absent browser, Python, Svelte, and build proof. A focused successful command prints
PARTIAL and keeps exit zero. Unlisted runtime files receive no credit from that command.

`npm --prefix web run test:browser` runs the shared browser catalog. Add entry names after
`--` to select `shell-feedback`, `ticket-review`, `configuration-schedules`, `conversation`,
`composer-voice`, `workspace`, `artifacts`, or `historical-sprint`. One invocation shares
the build, static server, and Chromium process. Independent cases keep fresh contexts.

The workspace entry first runs `workspace-rail-projection.test.ts` with typechecked
Vitest. A missing, empty, skipped, failed, or invalid companion result stops the browser
setup. The compiler checks the whole frontend test project, including unrelated typed
fixtures. The retained direct `node web/tests/workspace-rail-at-rest-browser.test.mjs`
command invokes workspace and carries the same companion requirement.

Other former direct host commands move to their catalog entry. Ideas and feedback use
`shell-feedback`. Approval and Stage owner use `ticket-review`. Worker setup
and scheduled tasks use `configuration-schedules`. Pane, conversation switches,
unanswered sends, and open-state cases use `conversation`. Composer and voice use
`composer-voice`. Rail, order, Settled groups, children, and reader state use `workspace`.
File preview uses `artifacts`. Sprint documents use `historical-sprint`.

`npm --prefix web test` runs full Vitest and the browser catalog. `test:legacy` remains
an alias for `test:browser`. Browser output names executed and omitted boundaries and
never supplies backend or production build credit.

The conversation entry keeps the named position regressions from the conversation fix.
The composer entry also checks trailing-newline measurement, focus, and selection.

Run `.venv/bin/python web/tests/browser/conversation-position-controls.py OUTPUT_DIRECTORY`
to replay the same position cases against the pinned unfixed source. This explicit audit
creates a disposable worktree and its own pinned virtualenv. It requires the eight known
geometric failures and four preventative passes. An infrastructure error cannot substitute
for an expected defect. The command records diagnostics and test hashes, then removes the
worktree. The normal catalog requires all twelve position cases to pass on current source.
