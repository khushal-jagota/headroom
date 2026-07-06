# Backlog + Ideas — two screens, one design language

Source system: `../sprint-redesign/principles.md` (P1–P8), and the token/idiom set shared by
the Ticket, Sprint, and Today mockups. These two screens reuse that vocabulary verbatim —
same `:root`, same Inter, same chip / whisper-label / disclosure / inline-edit idioms — and
derive their layout from what each screen is *for*, not by copying another screen's shape.

---

## Backlog — the one thing: **scan unscheduled work by priority, and add to it**

A backlog item is an *unassigned* sprint item (`sprint_id = null`), which the server hands
back sorted by priority then created_at. So the screen is a **prioritised index of
not-yet-scheduled work** — the staging pool you triage from and pull into a sprint.

**Hierarchy → priority is the organising axis.** Items group under P0 / P1 / P2 / P3 whisper
labels, exactly the way the Sprint page groups by status. That grouping *is* the hierarchy;
it needs no explaining prose (there is no data field to state as a frame the way Today has a
focus or the Sprint has a bet, so inventing an explainer line would be chrome — §15).

**Surfaced vs disclosed — nothing disclosed. The rows are flat.**
- A backlog item has **no sub-tickets** (a sprint item does; a backlog item doesn't), so
  there is no tree to reveal.
- Its depth — description, current-state note, and the act of assigning it to a sprint — all
  live on the **item page**, one click away. The row's whole job here is to be scanned and
  pulled, so it *navigates* rather than expands.
- **Progressive disclosure must earn its place.** On Backlog it doesn't, so I didn't force
  it: the page takes the Today screen's flatness, not the Sprint's disclosure. Putting a
  chevron on every row to reveal a usually-empty description would be inert affordance (P8).
- One redundancy removed: since the group already states the priority, the row does **not**
  repeat it as a chip. Rows carry only project + an optional deadline. (Contrast the Sprint,
  which groups by status and therefore *does* chip the priority.)

**Create — dormant.** Scanning is the job; adding is the errand. So the compose is a quiet
`+ New backlog item` affordance at the top that opens on demand and otherwise leaves the list
uninterrupted (P2, calm at rest). In the mockup it is shown *open* so the fields are
reviewable; collapsed is the resting state. Fields: title, project (required), priority
(default P3), optional deadline, optional description — the inputs are the inline-edit
surface (transparent, amber caret, no box, P7).

---

## Ideas — the one thing: **capture a thought with the least friction, then browse the pile**

§3.5 ideas are raw capture: title, a markdown body, a nullable project — no priority, no
status, no lifecycle. Newest first.

**Hierarchy → the compose is the hero.** This is the one real structural difference from
Backlog, and it's honest: Backlog's job is scanning (so create is dormant); Ideas' job is
*capturing* (so the compose is always open, at the top, the largest type on the page). It
borrows the **Today focus-line idiom** — prominent, no box, separated by rhythm alone (P4).
Type a title, press Enter, it's saved; detail and project are quiet and optional. Lowest
possible friction is the whole point.

**Surfaced vs disclosed — title surfaced, body disclosed.** An idea's substance *is* its
body, but a wall of open bodies is not calm. So each idea shows title + project at rest and
**expands to read the body** (P2) — this is where progressive disclosure genuinely earns its
place (it does not on Backlog; the two screens make opposite, deliberate calls on the same
tool). A light relative date sits at the right to orient the browse.

**Considered refinement of the Sprint's disclosure (P8).** The Sprint renders every item as
a disclosure, and an item with no tickets still expands to a "No tickets yet" line. On a
capture pile, *many* ideas are title-only quick dumps — so an expand-to-"no detail" on most
rows would be a lot of inert chevrons. Instead, **title-only ideas are flat rows with no
chevron**; only ideas with a body get the disclosure. This is a small, intentional divergence
from the Sprint precedent in the direction the system already points (an affordance that
reveals nothing shouldn't be drawn). Flagging it so it's a decision, not a drift.

---

## Amber — the one judgment call, resolved toward *less*

The design system rations amber to a **system-surfaced ask** ("this needs you") plus the
inline-edit caret. A create/commit button superficially resembles the Ticket's amber
**Approve** (a draft that persists only when you press it) — which is why the brief flagged
it as an open question.

**Call: neither screen uses amber beyond the edit caret.** Approve is amber because it
*appears only when the system has surfaced an approval* — it's an ask. A create surface is
always-on, user-initiated capture; it is never an ask. Painting its commit amber would make
"amber" mean "any button," and amber would stop being information (P5). So both commit
buttons stay grayscale (a quiet raised surface), and the only amber on either page is the
caret while you type — identical to Today / Sprint / Ticket. This matches the brief's lean
and, more importantly, keeps amber's single meaning intact. One-line reversal if the owner
ever wants the commit to read as the page's one deliberate action.

---

## Nav — two separate top-level screens, **not** a tab pair (confirmed)

A tab pair (the Sprint's `Sprint Overview · Sprint Tracking`) is for **two lenses on one
entity** — same sprint, same header, two views. Backlog and Ideas fail every part of that:
different entity types (sprint items vs. ideas), different data shapes, different lifecycles,
no shared subject or header. Tabbing them would falsely assert they're two views of one
thing. They are two **destinations in the app's primary nav** (alongside Today, Sprint,
Board, Review) — which is exactly the owner's framing, "backlog and ideas are 2 separate
things." So: no shared header, no tab pair, no cross-link between them. Each mockup is just
its screen's content (the way the Today and Sprint mockups are), with the global nav owned
by the app shell, not drawn here.

---

## Field / data implications for the build

- **Backlog list** is the existing `GET /api/items?sprint_id=null` — already server-sorted
  priority → created_at → id. The screen just groups the returned rows by `priority` on the
  client; no new endpoint. `project` is required, `deadline` nullable, `body` optional — all
  already on `CreateItemBody`.
- The current `screens-backlog.js` create form does **not** send `body`; this design's
  optional Description field does. `create_item` / `_marshal_create_item` already accept
  `body`, so wiring it is a one-line addition on the client, no backend change.
- **Ideas list** is `GET /api/ideas` (created_at DESC) unchanged. Capture posts
  `{title, body?, project?}` to `POST /api/ideas` — `project` omitted → NULL, which the
  "None" chip expresses. No priority/status fields exist on an idea and none are shown.
- **Body rendering.** Idea bodies are markdown (§3.5). The mockup shows them as plain
  paragraphs; the build needs a minimal markdown render for the disclosed body (the app is
  no-build vanilla JS — a tiny renderer or escaped-paragraphs, not a library). Same choice
  will recur on the item page.
- **Relative date** (`2d`, `Jul 1`) on ideas is derived from `created_at` on the client.
- **Project / priority selectors** are chip toggles here rather than native `<select>`s (the
  current form uses selects). Chips are more on-language, but that's a real component to
  build; a styled select is the cheaper path if the toggle isn't worth it. Either satisfies
  the contract — flagging it as a build cost, not a data question.

---

## Where a Notion designer would still push

- **Backlog with zero disclosure risks feeling like a dumb list.** The bet is that the
  prioritised grouping + one-click item page is enough. A designer might argue for a
  hover-peek of the description (not a click) so triage never leaves the page — worth
  prototyping if backlog items routinely carry bodies.
- **Ideas has no triage path.** Capture is frictionless, but ideas only ever accrue — there's
  no "promote to backlog item" or "archive." A designer would push for a lightweight promote
  action so the pile doesn't become a graveyard. Deliberately out of scope here (the brief is
  the two screens as they stand), but it's the obvious next move and the data would support
  it (an idea → item is title + body + project, all present).
- **The chip-toggle selectors** are clean at 4–5 options but don't scale; if projects ever
  grow, they'd need to become a combobox.
- **No empty states drawn.** Per §15 the real screens fall back to a single quiet line
  ("No unscheduled items." / "No ideas yet."); a designer would still want to see those two
  states mocked before sign-off, since a capture screen's empty state is its first-run
  moment.
