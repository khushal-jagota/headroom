# The front end

The front end is the web page the human uses — where every decision that matters
lives. It is a Svelte app built by Vite. FastAPI serves the built `web/dist`
document at `/`; the Vite chunks are mounted under `/_app/`. Shared tokens and
application CSS live in `assets/`. The Markdown pipeline and managed rendering
lifecycle live under `web/src/lib/`.

Answers travel compressed. The server gzips any response over 1 KB, which matters most
for a conversation open: the largest thread is 9.4 MB of JSON and goes over the wire as
1.8 MB. A live event stream never reaches the compression at all: the server decides from
the address, before the request is answered, and sends a stream down a route that has no
compression in it. That is not fussiness. Compression cannot say anything about a
response until it has seen some of the body, so it holds the response's headers back
until then — and a stream with nothing to report yet has no body to release them with.
The browser would sit there waiting to be connected, and the page would never learn that
anything had changed.

## The screens

One screen per part of the system:

- **Home** — the daily hub. It combines focus, what makes the day land, brief take,
  and watchout with one progress mark per Ticket. Action tiles lead to work that needs
  the user, needs review, is working, is paired, or is done. Day fields are written by
  the planning Workers, not edited on this page.
- **Review** — the human chamber for owner-addressed Ticket proposals and today's Worker
  help requests: one oldest-first walk with a centred item, its Ticket title, and Skip
  and Open Ticket top-right. Proposal items add their labelled recap, ask, approval, and
  send-back controls; needs-user items direct the human to the Ticket conversation
  without those proposal controls. Proposals addressed to another holder do not appear
  here. Keyboard shortcuts drive the actions that apply to the current item when the
  cursor is not in a text field, and each item fades in as it arrives. A proposal's
  approve button sends the next ceiling, cap, and owner holder together. Send-back
  delivers the owner's comment to the exact Ticket worker conversation before it clears
  the proposal. A refused delivery leaves the proposal in place. A reply in the Ticket
  conversation leaves the proposal in Review until a decision.
- **Workspace** — today's tickets in a left rail backed by the board resource. “Today”
  follows the same 5am planning-day boundary as the Day screen; dropped tickets never
  appear. The Chief of Staff row leads, and under it a selector chooses one of two views
  over the same tickets: **Tickets** or **Sprint Items**.

  The rail holds every status group, in one order: Errored, Needs you, User, Paired,
  Agent, Waiting to Closeout, Awaiting approval, Waiting for Kickoff, Empty, Blocked,
  Done. Every parked proposal sits under Awaiting approval, because there is one
  approval gate; a proposal still gated on its kickoff splits out into its own group. A
  ticket whose own run errored reaches the rail and Sprint Item page as Errored. A group
  with no tickets is not drawn.

  Three groups arrive shut: Waiting for Kickoff, Blocked and Done. Every other group
  arrives open. A shut group is still its own group, with its own name and count, and
  the reader opens it — nothing hides behind a "+n more", and no status is missing from
  the rail. Rows inside a group are ordered by activity, newest first.

  The Tickets view is every ticket on today, in a box per group.

  The Sprint Items view is one box per Sprint Item with a ticket on today. A shut Item
  shows a line of counts — how many tickets it has in each group, quiet groups included;
  an Item with no tickets on today shows no line. Clicking anywhere in the box selects
  the Item and opens its workspace beside the rail. That is all a click on an Item ever does: no click shuts
  an Item, so a reader inside one of its tickets clicks the Item to come back to it. An
  open Item shows the same status groups nested inside it, without their own boxes.
  Folding one of those groups, or opening a ticket, leaves the Item open. Items are
  ordered by priority and then by age, so an Item holds its place while its tickets move
  under it.

  Hovering a row or an Item box changes its background and nothing else. Selection is a
  background too — the same language the rest of the app uses for a selected thing — and
  it moves the other way: hover lifts a row off the rail, selection sinks it into one, so
  the two never read as more and less of the same state. A selected Item is a lifted
  surface and nothing more; its edge is the same hairline it always had. A selected
  ticket takes the larger step of the two, so a selected row inside a selected Item is
  still the strongest thing in the rail. Nothing but colour changes, so selecting
  anything moves nothing on the screen.

  What is open in the rail is the address, and nothing else. The Workspace keeps no
  memory of what you looked at before, so the same address always draws the same rail: on
  a click, on a reload, on Back, and while a change lands underneath it. Going to the
  Chief of Staff, or opening a ticket from the Tickets view, leaves no Item open.
  Selection in the rail is exclusive: what the workspace beside it is showing is the one
  thing that looks selected, and nothing else does. A ticket opened from inside an Item
  leaves that Item open around it — the address names the Item as well as the ticket —
  and the mark moves to the ticket, because being open carries no mark of its own.

  The Item title carries a mark for the Item's own supervisor, read exactly the way a
  ticket row's mark is read and fed by the same fact: an unseen completed turn. Its
  transcript shows a system marker when no explicit Send Message answered the principal
  who prompted that turn.
  A ticket without a Sprint Item appears in the Tickets
  view like any other. Every Ticket row is the shared Ticket row and keeps the existing
  conversation mark; it carries its priority tile in the Tickets view and drops it inside
  a Sprint Item, where the Item is the thing being read.

  The Chief of Staff row starts with its bundled portrait. The portrait is an agent
  identity on this row only; ticket rows and Worker types do not use it.

  The mark carries three signals in one order of precedence, and each is one
  system's own fact rather than a blend of several. A **pure white dot** means the
  worker is waiting on a permission decision or answers only the user can give — it wins
  outright, because that turn is still running and only the user can clear the wait. On
  an Item the same dot also means an unseen ping, which says the same thing: only the
  user can answer this. Below it, a **spinner** means the worker is running right now. With
  neither, the mark shows the **turn state**: a filled accent dot for a turn ending the
  user has not seen, the same dot greyed once the user has opened the ticket since
  owner has not seen, the same dot greyed once the owner has opened the conversation
  since that ending, and a faint ring when nothing is waiting.

  All three come from the conversation the row is linked to — the ticket's worker for a
  ticket row, the Item's supervisor for an Item. The first two are asked
  of the conversation system directly. The third is a comparison: the row carries a
  position in its conversation, and the server keeps how far the owner has got in that
  conversation. Something is waiting when the position is past the reading. Every row
  carries where its own conversation's last turn ended, so opening the ticket or the
  Item is what puts its mark out.

  The owner read position is durable and server-side, so reading a conversation clears
  the same dot on every browser. The client advances it only while the conversation pane
  is open, the document is visible, and the window has focus. It advances only through
  the newest transcript row that this browser received, even when a newer conversation
  snapshot arrived first. The client checks document focus again for each advance, so a
  click into a preview frame cannot leave stale focus permission. The server keeps the
  position monotonic and clamps oversized
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
  Item workspace or a Ticket replaces the rail, and a link back to the Workspace appears
  above the Ticket.

  A Ticket never opens as a full-screen page. Every Ticket link in the app, including
  the ones on the Sprint page and in Review, uses the Workspace address. The old
  `#/ticket/<ticket-id>` address redirects there, so shared links and stored
  notifications still work. A Ticket opens this way even when it is not on today's
  board, because the Ticket resource answers for it rather than the board card.
- **Ticket** — the whole story of one piece of work. A quiet identity eyebrow puts
  priority, effective project, Sprint Item, and Worker above a serif title. The Sprint
  Item appears only when the Ticket has one. Project and Sprint Item are static facts.
  The eyebrow states no Sprint and has no placement controls. The Ticket details
  disclosure contains only the ceiling and cap selects. Direct blockers get
  their own **Blocked by** line in the masthead, and the exact backend Worker failure
  reason remains visible when one exists. The inline-editable recap is always open on a
  recessed surface, without another label.

  A done Ticket offers an optional verdict above its Stage history. The user can choose
  one of five ratings, add text, use both, or clear the verdict. A saved verdict remains
  visible without edit controls if the Ticket returns to an earlier Stage. See
  `judgments.md`.

  When a worker records trouble during its claimed step, a read-only section appears
  next to the verdict. It shows each short note and its recorded time in creation order.
  The section stays absent when no trouble was recorded.

  The stages and their workflow remain the Ticket's Worker type's, derived from the
  served manifest (see below and `worker-types.md`). Guidance and the archive stay off
  this page. Review still shows Guidance with an approval. The current Stage mark speaks without a second status pill.
  Its summary adds words only where the mark would otherwise be ambiguous:
  **you're on it** for user-owned or taken-over work, with **Release**, and
  **awaiting approval** for a parked proposal. Running,
  completed, and upcoming marks need no
  extra label. Stage bodies, editing and approval behavior, and the worker conversation
  in serif along the bottom remain in place. During pristine Kickoff, the approval context also shows a restrained
  **Worker** picker whose choices come only from the backends this machine actually has —
  the same answer the conversation composer's model and effort pickers read. Changing
  it writes the stored Ticket choice but does not create a session. The first prompt attaches
  through that choice; accepting Kickoff may eagerly attach. Once Kickoff advances or the
  Ticket has a conversation, the pill becomes read-only.

  A compact artifact strip is the first element under the header when lifecycle fields
  link to managed Ticket or Sprint Item files. It reads the pending proposal first, then
  the lifecycle fields from latest to earliest. It removes duplicate links.
- **Sprint** — one tracking overview that presents Projects and their Sprint Items,
  plus a dedicated view for each Item and a separate documents page. The overview shows
  Item progress as `done/total`. One collapsed **No Outcome** row follows all Projects
  when unclassified Tickets exist. It opens their canonical Ticket links. An Item view joins today's Day membership
  to split its Tickets into Today and Other Tickets. Each section uses the same status
  groups and includes its own Done group. Project priority
  orders the Project folds. The documents page presents Kickoff, Checkpoint, and Sprint
  Review. See `sprints.md`.
- **Backlog** — active unscheduled Tickets as bounded summaries. Each row shows only its
  title and Project chip, and opens its canonical Workspace screen. The page does not
  request Outcomes. Its compact form creates an ordinary explicitly unscheduled Ticket.
- **Ideas** — remembered possibilities with an optional note and Project. See
  `backlog-and-ideas.md`.
- **Feedback** — open notes and handled history. Open notes link to their source page.
  Handled notes are grouped by their Ticket, with dismissed notes last. See `feedback.md`.
- **Chief of Staff** — the Chief is the first row in Workspace. On wide screens,
  selecting it opens the canonical Chief conversation beside the Workspace rail at
  `#/workspace/chief-of-staff`. On narrow screens, the same address opens the focused
  conversation. The former `#/chief`, `#/agents`, and
  `#/agents/chief-of-staff` addresses redirect into Workspace.
- **Config** — the management surface at `#/config`. It contains Chief of Staff,
  the Sprint Item supervisor skill, the shared Worker skill, and every configured
  Worker type. Chief settings open at `#/config/chief-of-staff`, Sprint Item supervisor
  skill at `#/config/sprint-item-supervisor`, Worker skill at `#/config/worker-skill`,
  and a Worker at
  `#/config/workers/<worker-type>`. Those detail screens provide the applicable
  launch defaults, suggested Kickoff ceiling controls, Stage ownership controls, and
  skill editors. Worker and skill
  identities and lifecycle structure stay read-only. Each editable value saves
  independently; a failed save keeps the attempted value and a useful error so it can
  be corrected or retried. The former `#/agents/worker-skill` and
  `#/agents/workers/<worker-type>` paths, plus legacy `#/workers` paths, redirect to
  Config.
- **Notifications** — the personal notification settings at `#/notifications`.
  “What counts” renders the Ticket and Chief of Staff groups from the server's
  notification catalogue. Tickets have five switches, and the Chief has four. Each
  subject-and-type switch saves and reports errors independently. “This device” asks
  for browser permission only after the user
  presses Enable, registers the browser's Web Push subscription, and can remove it
  again. On iPhone or iPad, Panels explains that the site must first be added to the
  Home Screen.
- **Scheduled tasks** — the internal schedule editor at `#/scheduled-tasks`. It lists
  schedules, creates and edits them, and enables or disables future occurrences. It
  does not start Workers or delete schedules. See `scheduled-tickets.md`.

The shell has three primary destinations in order: Home, Review, and Workspace. More
groups Sprint, Backlog, Ideas, and Feedback under Planning. It groups Config, Backends,
Notifications, and Scheduled tasks under System. On desktop, the three destinations
and More live in the top bar. On mobile, the same four controls form a fixed,
full-width bottom bar and More opens a bottom sheet. A slim mobile top bar
shows the current screen and the same quiet connection and worker-presence cluster used
at desktop. The cluster is a live status label, not a control. It normally says
**Connected** or **Reconnecting**. During a deployment
it can instead say **Preparing**, **Restarting**, **Back up**, or **Problem**. These
words combine the server's durable deployment account with whether the browser's
change stream is connected; the stream itself still reports only its own connection.
A planned phase expires locally at the time the server supplied, even if no new event
arrives. A deployment problem remains visible through a dropped connection.
Worker presence is the adjacent spinner and "N working" readout from the global
running-worker count. Review's count is the only navigation accent; active destinations
use strong text. The shell uses the dynamic viewport and never scrolls the navigation
horizontally.

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

**Backends** at `#/backends` shows the conversation backends installed on this machine,
their account and model facts, and any update Panels can run. Hermes shows its version
and model catalogue without update discovery or an update action. The first catalogue
paints before the page refreshes catalogue and Codex and Claude update advice in the
background. An ordinary
cached read remains immediate during that work. The page's **Refresh** action reads Codex
and Claude usage only, then uses the current backend catalogues. A successful refresh
shows each provider window as its remaining percentage, reset time, and
observation time. The backend source still reports the used percentage. The shared
ring derives the remaining value and empties counter-clockwise from the top as the
allowance falls. One provider failure does not discard the other's new answer. Hermes
has no usage source. Codex uses its app-server rate-limit read, so Refresh starts no
model turn and consumes no allowance.

The shared model picker offers the same Refresh action beside Reasoning. It replaces
the picker owner's backend snapshots with that same refresh response, so model choices
and usage rings update together. The picker stays open while it reads and reports a
provider or transport failure in its existing feedback line.

## The two rules that shape it

- **The server says "something changed"; the browser refetches what it is showing.**
  Every live-updated read the browser makes is listed in one place — its name and
  the address it comes from — so a screen asks for a resource by name and gets both.
  The reads are cached and shared: two screens asking for the same thing make one
  request. The shell's deployment status is always mounted. A few one-shot reads, like
  the worker configuration probe, are plain fetches and sit outside the list.

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

  The change stream is also the browser's connection-health owner. It has exactly two
  states: connected and reconnecting. The shell combines that transport fact with the
  separate deployment-status read described above. When the stream drops, the browser
  retries on its own. Unless a durable deployment problem takes precedence, the
  label says Reconnecting until it is back. Because anything that changed during the gap went
  unheard, opening the stream refetches
  what is on screen — that, plus the same refetch when the window is focused again, is
  the whole recovery story. The server sends an occasional invisible keep-alive line
  down a quiet stream, which changes nothing on screen.

  There is no second transcript to merge: the record's rows are what a pane shows, read
  after the position it already holds and then kept up over a live tail of those same
  rows. Opening a Ticket attaches to nothing and spawns nothing — an agent starts when a
  message is sent to it — so merely looking at a Ticket during Kickoff cannot freeze its
  backend choice.
- **Markdown is GFM and sanitized.** Written text (briefs, notes, ideas) renders
  through a Vite-owned unified pipeline. It supports CommonMark and ordinary GFM,
  including tables, task lists, strikethrough, autolinks, reference links, fenced
  code, block quotes, thematic breaks, and nested mixed lists. Raw HTML stays visible
  as text. A strict sanitizer removes scripts, event handlers, unsafe URLs, ids,
  styles, and DOM-clobbering attributes before any DOM node is created.
- **Ticket conversations expose loopback dev servers through Panels.** A Markdown link
  in a Ticket conversation whose address is `http://localhost:<port>/...` or
  `http://127.0.0.1:<port>/...` is rendered as
  `/dev/tickets/<ticket-id>/<port>/...`. The link keeps its path, query, and fragment;
  the port stays in the link rather than becoming Ticket state. The rewritten address is
  a preview like any other one written that way. This context belongs
  only to the Ticket conversation, so the same Markdown on another surface remains an
  ordinary loopback link. The first proxy contract carries pages and relative resource
  or navigation paths under that prefix. Applications that hard-code root-origin URLs
  must be configured with a compatible base path. Panels credentials and cookies do not
  cross into the dev server, and dev-server cookies or authentication challenges do not
  become state on the Panels origin.
- **Shared scroll areas keep their place.** Panels reserves stable scrollbar space on
  its shared vertical and horizontal scroll areas, so content does not move when a
  scrollbar appears. On a mouse or trackpad the thumb stays quiet until hover, focus,
  or active use. Touch and forced-colors modes keep the platform's visible scrollbar
  behavior.
- **Ticket files are linked, not stored in fields.** Ticket guidance, fields,
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
  and every image into that same component wherever the image is hosted. A link to a
  Ticket's dev server, `/dev/tickets/t_123/8791/`, joins them: it is a Panels address the
  user can open, so it gets the same treatment and reads as "Open" plus the link's own
  text. Any other link stays an ordinary link: a same-page anchor is still an anchor, one
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
  new tab or window is left alone, and so is a link to a Ticket's dev server, which is a
  page rather than a file. On the Workspace the open file rides in the address beside
  the Ticket, so a reload, Back, and a shared link all show it. The
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
  Saving an approval draft on either the Ticket or Review screen replaces the pending
  proposal text immediately. Approval then uses that stored text without a second edit.
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
- **Chip** — the coloured status/project tags, including "blocked by".
- **PriorityTile** — the shared always-coloured P0–P3 square. It appears in the
  Workspace Item's eyebrow, the editable Ticket and Review identity control,
  both Sprint priority positions, and once in each Backlog priority group heading.
  Priority never borrows the slate-blue attention accent or the status-mark colours.
- **StageMark** — the single stage dot showing a field's progress.
- **ApprovalBlock** — the owner approval surface: an editable proposal draft, the scope
  picker, and the approve/accept action, plus a read-only mode for dropped tickets. Its
  approval addresses the next ceiling proposal to the owner.
- **ReviewProposalCard** — one waiting proposal as a card: the ticket's title and recap,
  the kickoff priority, the approval control, and the send-back box. It is named by a
  ticket id and a field and reads that ticket itself, so any screen can raise the same
  ask. Which ask is current — walking, skipping, the keyboard shortcuts — stays with the
  screen. The Review screen mounts it.
- **ResourceState** — the shared error / loading scaffold. It asks what the screen has
  to show before it asks what went wrong: a screen that has data keeps showing it and
  puts a failed read as a line above it, and only a screen with nothing yet is given
  over to the error line or the loading line. Data-empty states ("No ideas yet.") stay
  in the screens.
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
  composer with its model, effort and skill choices. The composer also owns pending
  pictures, documents, and data files from the pickers, clipboard, and drag-and-drop.
  It removes attachments individually and hands one content run to `LiveConversation`.
  Messages that contain only attachments use that same path. A file-bearing message that
  still waits for its durable row keeps its bytes in browser database storage, so a reload
  restores it across the full supported file-size range.
  Its delivery control defaults to Steer and also offers Queue and Send now. A held row
  names why Panels queued a steer fallback.

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
  screens — a move changes how tall it is and nothing else, so the reader keeps their
  place in the transcript and keeps whatever they had half-typed.

  Which state it opens in is the page's to choose, and the page can change it later. A
  page that says nothing gets no layer at all: the Chief of Staff, the Workspace desk and
  the development pane each keep a conversation that simply fills the space it is given.
- **ComposerCatalogMenu** — the typed list that opens when a composer line starts with
  `/`, `$`, or `@`. Slash offers commands, dollar offers skills, and at offers apps and
  plugins. The list narrows as text is typed. A choice inserts its exact catalog text
  into the draft. Sending and transcript display still use ordinary text. Codex resolves
  that text at its adapter boundary. The browser does not store vendor identifiers or
  construct structured Codex input.
- **EnumPill** — a pill whose value is chosen from a menu (project, sprint, scope).
- **SegmentedControl** — a small set of toggle options (Backlog Project/priority).
- **ScopePairPicker** — the "approve until … then …" scope control.
- **ErrorLine** — a single error message line.

The Ticket page shows its leash only while scope is editable. A pending proposal hides
the leash entirely, matching the server rule that the proposal's holder and scope remain
stable until the proposal is decided.

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

_Last verified: 2026-08-15._
