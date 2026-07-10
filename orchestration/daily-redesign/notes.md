# Daily workflow redesign — same screens, a new voice

Brief from the owner: the product exists to make work frictionless — it abstracts
complexity so you can do more. Dark, colour for signalling, as minimal as possible
without losing key function, structured so the eye lands on the important thing,
nice to be in, high status, somewhat alive without overwhelming. Focus: the daily
loop — Review, Workspace, Ticket.

Mockups: `index.html` → `review.html`, `workspace.html`, `ticket.html`.

## Owner rulings across the passes (all locked)

1. **Pass 1** (polish of the existing CSS): the four-colour dot vocabulary is
   rejected — too traffic-lighty; **spinners** are the signal for "working". The
   hierarchy/depth work was good and implementable, but it was a step improvement,
   not a redesign.
2. **Pass 2** (IA experiments — the Desk merge, the Brief's in-place docket):
   **Review stays its own screen you go through** — one thing at a time: review,
   act, next. Nothing else belongs on it.
3. **The functions and shapes of the screens are already right.** Workspace is the
   only three-panel surface; Review is deliberately stripped; Ticket is the
   document. **The redesign target is the UI, not the IA.**

## The direction (pass 3)

One identity-level move, applied to the existing screens without touching their
function: **the product speaks in serif; the machine is sans.**

- **Serif (Newsreader)** for everything the system or its agents *say*: ticket
  titles, recaps, proposals, notes, field content, chat messages, the empty
  state's "Nothing needs you."
- **Inter** for everything that is a *control or a fact*: nav, labels, pills,
  buttons, filters, counts, key hints.

The split is the design: prose = a staff reporting to you; sans = the handles you
pull. It reads high-status the way a well-set document does, and it makes the
hierarchy self-evident — on any surface, the words are the content and the small
sans elements are the machinery.

Carried in from pass 1 (the approved pieces):
- **Depth rationed to the ask**: the one raised approval surface gets a top
  highlight, a long soft shadow, and a faint glow under Approve. Nothing else on
  any page is elevated.
- **Entrance rhythm on Review** (~400ms staggered fade-up as each decision
  arrives) and **keyboard hints** (`⌘↩ approve · S skip · O open`).
- **The leash as a sentence** on the ticket header ("its leash — runs up to
  *in progress*, then *proposes*").
- **The empty state as a reward**: "Nothing needs you. 3 agents working — the
  queue refills itself."
- **Presence in the shell**: the amber Review badge counts your jobs; a small
  spinner + "3 working" counts theirs. Aliveness is ambient — spinners, a
  streaming caret in chat, nothing that blinks for attention.

Signals (unchanged from the current app): amber = needs you (the only accent),
the existing arc spinner = working, green ✓ = settled, red = errored, faint ring =
not started. No new colour meanings.

## Owner feedback on pass 3 (folded in)

- **Review screen: approved as is.** ("Love the new review screen.")
- **The serif is fine; the styling of it went too far.** Italic accents removed
  everywhere — emphasis was making things harder to read, not easier. The face
  (Newsreader) stays.
- **Fewer segmentation lines.** The hairline between every block was
  over-segmentation. Blocks now separate by space; the only hairlines left on the
  ticket are the stage-spine rows (a list) and the rail seams. The chat keeps the
  live app's treatment the owner likes: no dividers at all — the thread fades
  (mask gradient) into a whisper header above and the composer below.
- **The chat composer is the real one**: the bordered box (amber border on focus)
  with the textarea and its buttons beneath — `/` slash commands, image attach,
  and the ↑ send square. These were missing from the mockups and must remain.
- **User note joins the dropdowns.** It is the kickoff notes — part of the
  collapsible group, collapsed by default, first in the spine (no stage mark).
  Recap stays above, open, as it is today.

## Fidelity audit (after the owner caught invented elements)

The mockups were corrected against the real components (`ChatPanel`,
`ChatComposer`, `TicketStageSection`, `ApprovalBlock`, `ScopePairPicker`,
`ReviewRoute`, `BoardRoute`, `TicketRoute`) so that everything shown either
exists today or is explicitly proposed below.

Corrected to match the product:
- **Every stage has Notes.** Each stage body now carries its collapsed Notes
  disclosure (open when written), including the gating stage below its approval
  and the review surface's Notes above the proposal. The invented "Note" box
  *inside* the ask block is gone — no such thing exists.
- **Chat header is an availability dot + label** (green = gateway available,
  "· offline" otherwise). There is no working indicator on the chief of staff or
  any chat header — the invented spinner state is removed.
- **The live indicator that does exist** is the in-thread pending row: three
  bouncing dots + the activity label ("Thinking" / "Working" / "Responding" or
  the turn's own activity text). The mockups now show that, in the real spot,
  and the send button flips to the pause `Ⅱ` while a turn runs — also real.
- **Scope copy is the real picker's**: "until [No further | states…] then
  [stop | propose]" on approvals; "approved until … then …" on the ticket header
  (those are the live EnumPill labels, recomposed as one line).
- **Review empty state** uses the real copy ("There is nothing to review right
  now." / "N agents in progress") and the real rising-disc mark, re-set in serif.
- **Status chip copy** is the real label ("awaiting approval").
- **Skip lives at the bottom** with Open ticket, as in the live outside-actions
  row.

## Proposed additions (not in the product today — each needs a decision)

1. **Shell presence — "N working" with a small spinner in the nav.** Data
   already exists: `/api/queues` returns `running_agents` and the shell already
   polls it for the Review badge. Zero backend work; one line of UI. Reason: the
   counterpart to the amber badge — the badge counts your jobs, this counts
   theirs, and it makes the whole app feel staffed from any screen.
2. **Review keyboard shortcuts** (⌘↩ approve · S skip · O open ticket). Today
   only ⌘↩ exists, and only inside the send-back textarea. This is new
   frontend-only keybinding work. Reason: review is the highest-frequency loop;
   it should be drivable without the mouse.
3. **The leash as one sentence line** instead of two labelled pills — same data
   (`ceiling`, `at_cap`), same options, copy change only.

Rejected by the owner: the "1 of N" queue position ("isn't useful"). Skip and
Open ticket moved to the top of the Review page in its place, both with right
chevrons so they read as clickable; the bottom action row is gone.

## Per-surface notes

- **Review** — one centered ~680px column, exactly the current function: queue
  position, title, recap, the ask, the send-back composer, open-ticket, next.
  The serif voice does the work of making it feel like a considered decision
  rather than a form.
- **Workspace** — the current rail + right pane. Rows stay sans instruments with
  the existing mark vocabulary; the chief's messages are serif. Filters compress
  to one quiet line.
- **Ticket** — the document earns the name: serif title (28px) and serif prose
  throughout; one sans facts line (status · priority · due · project · sprint ·
  take over/copy); the leash sentence; recap open at the top; then the spine of
  dropdowns — user note (collapsed, no mark), success, approach, plan, result —
  with the identical ask block as Review inside the gating stage; the employee
  chat in serif with the fade treatment and the real composer.

The remaining screens, mocked in the same voice after the daily set was approved
(each faithful to its route — DayRoute, SprintRoute, BacklogRoute, IdeasRoute):

- **Day** (`day.html`) — the locked read, re-voiced: sans dateline, serif focus
  hero (30px), serif bodies for Brief take / Watchout / If today lands; the
  amber and green labels stay the page's only colour. Still flat — no boxes, no
  seams.
- **Sprint** (`sprint.html`, rev 3 — the owner rejected the tabbed organisation,
  then directed project grouping and the documents off the page) — the tracking
  page is now: serif name, a quiet meta line (dates · day N of M · X of Y done ·
  a "Sprint documents ›" link), the bet as the serif lead, then the items
  **grouped by project** (the workspace roster's grouping, so both surfaces
  organise the same way). Project groups are collapsible disclosures, and
  **Loose tickets is the same group primitive** — a project-style collapsible
  heading with its ticket rows directly inside, not a bordered odd-one-out.
  Each item row: chevron · title · status word (amber
  "in progress", faint "todo"/"blocked", green "done" with the row dimmed) · a
  done-fraction derived client-side from ticket states. Ticket rows read
  priority · title · state (owner-directed order). Chips (priority · due ·
  blocked-by; project now implied by the group) live inside the expansion.
  Loose tickets collapse at the end.
- **Sprint documents** (`sprint-docs.html`) — their own page, reached from the
  meta-line link, with a back link to the sprint: Kickoff / Mid-sprint Review /
  Sprint Review disclosures, sans labels, serif values. Routing note for
  implementation: the old `#/sprint/overview` tab route becomes this documents
  page; `#/sprint/tracking` becomes the sprint page itself. The "day 4 of 10"
  and per-item done-fractions are derived client-side from data already on the
  page.
- **Backlog** (`backlog.html`, rev 2) — the owner prefers the live ideas
  capture over a labelled form, so the new-item view now uses the same idiom:
  no boxes, no field labels — a large serif title input and a quiet serif
  description written straight onto the page, then one foot row carrying the
  project segments, priority segments, a small "due YYYY-MM-DD — optional"
  input, and the amber commit. Same fields as the live form, re-projected.
  Below, priority groups of hairline rows with project/deadline chips.
- **Ideas** (`ideas.html`, rev 2) — the capture hero restyled to match the live
  screen's treatment exactly: transparent unboxed inputs (focus-line idiom),
  italic placeholders, serif title (21px) and detail, hairline seam under the
  capture; foot row of project segments, ↩ hint, amber Capture. The pile:
  detail rows disclose to serif bodies, bare ideas stay flat; the relative
  capture date is dropped from the rows (owner-directed — the live screen shows
  it, the redesign removes it).

All seven navs are wired together so the whole app can be clicked through.

## Dials the owner can turn

1. **The serif itself.** Newsreader is the pick (reads well small, warm, quiet
   italics). Alternatives with more personality: Fraunces (more display-flavored)
   or staying all-Inter (the safe fallback — but that's pass 1 again).
2. **How far the serif reaches.** Shown: all content voices including chat. It
   can be pulled back to titles + long-form content only (proposals, recaps),
   leaving chat sans, if serif chat reads too literary in daily use.
3. **The amber glow / entrance motion / key hints** — each removable with zero
   layout consequence if they wear badly.
4. **The leash sentence** vs the current labelled pickers.

## History

Pass-2 files (`desk.html`, `brief.html`, `review-room.html`) are deleted — the IA
they explored is settled by ruling 2 and 3 above. What survived from them is the
serif voice (born in the Brief) and the queue-kind sublabels idea ("Plan ·
awaiting approval"), which could later inform the Review queue's entry labels.
