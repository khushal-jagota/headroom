# Sprint screen — decoded principles + how they drive the design

Source of the decode: the finalized ticket screen
(`.gstack/.../ticket-sprint-redesign-20260706/finalized.html`), its in-repo structural
twin (`orchestration/ticket-redesign/mockup.html`), and the reasoning in
`orchestration/ticket-redesign/plan.md`. Grounding for the sprint side: SPEC §3.1, §3.2,
§4.4, §5, §10.5, §11, §15, and `PRINCIPLES.md`.

The ticket screen's own title is *"Ticket — one primitive."* That is the thesis. What
follows is the system underneath it, then the map from each rule to a concrete sprint
decision. The goal is to reuse the **thinking**, not the pixels — the sprint's layout is
derived from what a sprint is *for*, not copied from the ticket.

---

## Part 1 — the decoded system

### P1. One primitive, flexed by state — not many widgets
The ticket is built from a single repeating shape (a small header label, then content
directly below) and *one* collapsible field primitive reused four times (Success,
Approach, Plan, Result). There is no bespoke chrome per field. **Why:** a single
well-made primitive that bends across every state reads as considered; a zoo of one-off
components reads as accreted. It is also the PRINCIPLES rule — "build few, reuse hard, no
near-duplicates." The primitive earns its keep by being *the same* whether a field is
settled, active, or empty.

### P2. Surface the one thing that needs the human; disclose the rest
The screen does **not** present all four fields as equals in source order. It computes
*what currently needs you* — the pending gating proposal, or the review — and renders only
**that**, in full, up top. Everything settled or referential collapses behind
`<details>`. **Why:** hierarchy is earned by task-relevance, not by importance-in-the-
abstract. The page answers "what do I do here, now?" before it answers "what is
everything about this?" This is the single most important idea to carry over.

### P3. Surface elevation is semantic, not decorative
Two surface treatments, each with a fixed meaning:
- **Raised** (lighter than the page) = *this needs you / act here* — the approval block.
- **Recessed** (the darker "sunk" token) = *notes / secondary / supporting* — the Note
  inside the approval, the review notes.
**Why:** if elevation always means the same thing, the user learns to read the page's
shape before reading a word of it. A raised region is always an ask; a sunk region is
always ancillary. Elevation is a language, not a finish.

### P4. Background OR border — never both. No vertical rules, no insets.
Every separated region is defined by exactly one mechanism. Pills, the approval, the
notes: **background only**. The chat rail and the row dividers: **a border seam only**.
Nesting is shown by disclosure, never by indentation — content sits at the same left edge
as its own header. **Why:** double-encoding (a fill *and* an outline for the same box) is
the tell of an unconsidered UI; picking one per element is what makes the whole sheet read
as disciplined rather than busy. Insets and vertical rules add lines that carry no
information — cut them.

### P5. Grayscale carries structure; one accent carries meaning
The palette is a warm grayscale — five foreground tints, three surface tints. The *single*
bright accent, amber, is spent **only** on "this needs the human": the Approve button, the
edit caret, the ● mark on the field in play. A second, quieter semantic color (green)
marks *settled/passed* (the ✓). **Why:** when color is rationed to one meaning, its
presence is information. Amber never decorates; wherever it appears, that is where your
attention or action goes. Grayscale + spacing does all the structural work.

### P6. A closed type + tint ladder; hierarchy from the ladder, not from variety
A strict five-role scale, and a five-step foreground ladder (title → body → secondary →
label → faint). Labels are one treatment everywhere: tiny, uppercase, letter-spaced,
faint. **Why:** calm comes from rhythm and a small vocabulary repeated exactly, not from
many sizes and weights competing. Fewer roles, held strictly, read as more designed.

### P7. One inline-edit surface, no affordance — but commitments get a button
Editing happens *in place*: contenteditable with no box, no pencil, just an amber caret on
focus. Reversible edits save silently on blur / ⌘Enter and revert on Esc. The **one
exception** is the approval body: it is a *draft* that persists only when you press
Approve. **Why:** the affordance-lessness says "this text is the truth, edit it where it
lives." And the split — silent-save for reversible edits, an explicit button for
consequential commits — is itself hierarchy: the weight of an action is encoded in whether
it needs a deliberate press.

### P8. State drives what is shown — nothing on screen is inert
The approval has two modes and appears only when there is something to approve; the recap
editor hides in states where recap is illegal; the grant pickers appear only for the
gating field; each field's mark (✓ / ● / ○) reflects its own state. **Why:** a screen
whose contents are a pure function of the entity's lifecycle can never show a stale or
meaningless control. What you see *is* where the thing is.

### Meta-rule (the brief's, holding above all eight)
Usability outranks aesthetics; every visual decision must serve a task the user actually
performs here. Impress by **rigor**, not by resembling any particular product. A beautiful
page that doesn't serve the task is a failure.

---

## Part 2 — what a sprint page is *for*, and how the hierarchy expresses it

A ticket detail page is a **single-entity decision surface**: one unit of work, a linear
lifecycle, usually exactly one thing needing you — so its "one thing" is the pending
approval, and the fields disclose behind it.

A sprint page is a different animal: a **many-item overview across a time box**, with a
lifecycle of its *own* — **kickoff → work → review**, gated by two freeze commitments
(§3.1, §5).

**The mistake in rev 1 (corrected here):** I read the ticket's P2 as "surface the approval"
and built a sprint version of it — an amber "Needs you" band of pending status proposals.
That was decoding the ticket's *appearance*, not its principle. **P2's real content is
progressive disclosure: be calm at rest, reveal detail on demand.** And a sprint item in
the sprint view is **not an approval surface** — approvals live on the Review screen and
the Ticket page, never here. So the band is gone, along with every "needs you" / "unblock"
/ accept control on items. What P2 actually asks of a tracking page:

> **The sprint's tracking page is for reading the state of the work at a glance and drilling
> in only where you want detail. It is calm at rest and progressively discloses.**

So the hierarchy is made literal — **sprint → sprint items → tickets** — and expressed by
disclosure:
1. **The bet, stated once** (from `primary_bet`) — the frame the work is tracked against.
   Echoed here read-only; the editable copy lives on the Kickoff page.
2. **The arc** — `Kickoff ✓ · Running ● · Review ○` — the sprint's lifecycle in the ✓/●/○
   vocabulary, doubling as the **navigation** between the three pages (see the split below).
3. **The items**, grouped by status, each a **collapsed row** (the primary rows, kept
   simple). Expanding an item reveals **its tickets and their states** — nothing about
   tickets is flattened onto the page. Live groups (active/todo/blocked) are visible;
   settled groups (done/deferred) and loose tickets are themselves collapsed disclosures.

### The page split (recommendation — the owner is undecided, so here is the case)
The old Panels viewer had **Kickoff / Tracking / Review** as three distinct Day-like
lenses. I recommend the sprint keep that shape rather than stacking everything on one page:

- **Kickoff page** — the four kickoff fields (editable until frozen; **Freeze kickoff**),
  plus **weekly addenda**. Addenda belong here because they are, by definition, the one
  thing you may still write *after* the kickoff freezes (§3.1) — amendments to the plan. The
  plan and its living amendments living together is cleaner than orphaning addenda on the
  tracking page.
- **Tracking page** (this mockup) — the items, progressively disclosed. Nothing else.
- **Review page** — the five review fields (editable until frozen; **Freeze review**).

Why split rather than one page: the three phases have **different jobs, different rhythms,
and different write-vs-read postures** — kickoff and review are focused *writing* sessions
that end in a one-way freeze; tracking is a *daily read* you return to. Jamming a writing
surface and a freeze commitment onto the daily tracking read re-creates exactly the "flat,
everything-at-once" problem this revision removes. Splitting also lets each freeze
commitment own its page (the deliberate-commit-gets-its-own-surface logic of P7). The arc
strip keeps the three navigable and always shows where the sprint is. *If the owner prefers
one page,* kickoff and review return as two collapsed disclosures at the top (below the
bet), not the bottom — but the split is the stronger call.

---

## Part 3 — each principle → a concrete sprint decision (rev 2)

- **P1 (one primitive):** one `<details>` disclosure primitive carries the whole page — the
  item row (expands to its tickets) and the settled/loose group (expands to its items) are
  the same shape at two levels of the sprint → items → tickets hierarchy. No status gets
  bespoke chrome; the ticket sub-row is the only other primitive.

- **P2 (calm at rest, disclose on demand):** the page shows the bet, the arc, and the item
  rows collapsed. Tickets appear only when you open an item; done, deferred, and loose
  tickets appear only when you open their group. Nothing is flattened onto the page.

- **P3 (semantic elevation):** with the approval band gone, there is **no raised surface** on
  this page — a tracking read has nothing that "needs you," so nothing is raised. Elevation
  stays honest by staying flat. (Raised surfaces return on Kickoff/Review only if those
  pages ever carry an ask; they don't today.)

- **P4 (background or border; no insets):** item rows are divided by border seams; a faint
  background appears only on hover (background only, no border) to signal the row expands.
  Revealed tickets are **not inset** — disclosure, not indentation, carries the nesting.

- **P5 (one accent):** amber is now spent on almost nothing — only the arc's ● on the
  current phase and the edit caret. Green marks the settled/positive: the arc's ✓ (kickoff
  frozen), `done` ticket states, and a **blockers-cleared** chip (a neutral status display,
  **not** an "unblock" action). Item statuses are otherwise grayscale; settled items dim.

- **P6 (closed ladder):** five type roles (20 name / 16 bet / 15 item / 14 ticket / 11
  labels). The whisper group labels, the arc, and the metadata share the faint treatments;
  hierarchy comes from the tint ladder + rhythm, not size variety.

- **P7 (inline edit; commit gets a button):** on the tracking page the only editable thing
  is the sprint **name** (in place, no affordance); the bet is a read-only echo. The
  consequential commits — **Freeze kickoff**, **Freeze review**, **Add addendum** — live on
  the Kickoff/Review pages they belong to, each a real button (deliberate commit, own page).

- **P8 (state drives the surface):** the mockup renders **running** (kickoff frozen, review
  not started) — the arc shows ✓ · ● · ○ accordingly, and the live groups are visible while
  settled ones collapse. In earlier/later phases the arc simply re-marks and the current
  page shifts (the owner opens Kickoff or Review from the arc); the tracking page itself is
  unchanged — it is always the items.

### The deliberate divergences from the ticket (domain-driven)
- **No chat rail.** §11: only tickets and days have a chat session — a sprint does not.
  Copying the ticket's right rail would be copying appearance over substance. Single
  centered column.
- **A sprint item is not an approval surface.** The ticket's "surface the one thing that
  needs you" does **not** transfer: pending status proposals are resolved on the Review
  screen and the Ticket page, so the tracking page carries **no** approval band and **no**
  accept/unblock controls — it reads and navigates, it does not decide. This is the
  correction from rev 1, and it is the clearest proof the design now follows the ticket's
  *principle* (calm progressive disclosure) rather than its *look* (an amber action block).
- **Calm at rest, not open at rest.** Where rev 1 laid the whole board flat, the items are
  now collapsed by default and tickets live inside them — the ticket screen's real spine
  (collapsed `<details>`, expand for detail), applied to the sprint → items → tickets tree.
