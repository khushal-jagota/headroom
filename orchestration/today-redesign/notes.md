# Today (Day) — design notes

Same design system as the ticket and sprint mockups (decoded in
`../sprint-redesign/principles.md`). This screen's job, per the owner's locked definition:
**Today = the Overview, and nothing else** — a calm strategic daily *read*, not a
dashboard. Board absorbed Workspace + Tracker; Review owns approvals; so Today drops the
plan-tree, the today-ticket-list, and the review-count, and becomes purely the brief.

Content model (from the old viewer's Overview lens): a **date**, a one-line **focus**, then
three sections — **Brief Take**, **Watchout**, **If Today Lands**. Supporting Context and
Sources Checked stay hidden, as they were there.

## Flatter revision (owner-directed)

The owner reviewed the page and asked for a purer read: **the recessed Watchout surface and
the horizontal section dividers were both removed.** Nothing on the page is boxed and no
hairline seams remain. The whole hierarchy now leans on **spacing, type scale, and rhythm** —
a large top margin, a dominant focus line, generous 56px gaps between the framing sections,
and the tiny uppercase labels (two of them colored). The Watchout keeps its **amber label** —
that marks the day's attention-point and is meaning, not decoration — but loses its box. The
principle notes below (P3, P4) are updated to match; the earlier boxed/seamed treatment is
described only as the draft it replaced.

## Which principle drove which decision

- **P2 — surface the one thing that needs the human → surface the day's *frame*.** On a
  read surface there is no approval to surface, so P2 translates to: make the **focus** the
  hero (the largest type on the page, first thing after the date) and the **Brief Take**
  its immediate elaboration. Focus + take sit together with no divider — the way recap and
  the approval sit together on a ticket — because together they *are* "the take." That is
  the day's one thing.

- **P1 — one primitive, flexed.** All three sections are the same primitive: a tiny
  uppercase label, then prose below, no inset. With the Watchout box gone, the *only* thing
  that changes between them is the accent — the label color.

- **P3 — semantic elevation, spent to zero here.** The earlier draft boxed the **Watchout**
  on a **recessed** surface — the exact treatment the ticket gives its Note. Per the owner
  that box is gone: the read now carries **no elevation at all**. All three sections are flat
  prose on the page. The caution no longer needs a surface to land — its **amber label**
  alone marks the day's attention-point. On a calm strategic read, one boxed region proved to
  be one box too many; flatness is the purer read.

- **P5 — grayscale carries structure; the accent carries meaning.** The three section
  labels are color-coded and that is the *only* color on the page: **Brief Take** faint
  neutral (the read), **Watchout** amber (the day's single attention-point — the read-page
  analogue of "needs you"), **If Today Lands** green (the positive/settled semantic, same
  green as the ✓ elsewhere). No boxes needed to carry the meaning — the label color does it.

- **P4 — background OR border; here, neither.** The framing sections were separated by a
  hairline seam + space, and the one surface (Watchout) was background-only. Per the owner the
  seams are gone too. Sections are now separated by **space and rhythm alone** — no rules, no
  surfaces, nothing indented. P4 says "one mechanism, never both"; this read takes it to the
  limit and uses neither.

- **P6 — closed type/tint ladder.** Five roles: date label (12) · focus hero (23) · take
  body (16) · watchout/lands body (15) · section labels (11). The focus is the one big
  thing; hierarchy is spacing + the tint ladder, not variety.

- **P7 — one inline-edit surface, no affordance.** The brief is boundary-authored but
  human-amendable in place: focus and all three bodies are `contenteditable` with no chrome
  (an amber caret on focus, nothing else). The page still reads as a document — editing is
  silent and in place, so it never turns the read into a form.

- **Layout as a read.** The column is a tight ~680px measure (Ticket was 768, Sprint 840)
  with generous vertical rhythm and a large top margin. The narrow measure and the
  whitespace are the point: they say "this is a document you read," not "an app you
  operate." A Notion designer reads column width as intent — a read gets a reading measure.

## The two deliberate divergences (both honest, both domain-derived)

1. **Read, not act — no "needs you" band.** Ticket and Sprint each surface an amber action
   block (the approval / the pending status proposals). Today has none, on purpose:
   approvals live on Ticket and Review, and the owner locked Today as a strategic read.
   Making the page action-free *is* the design statement. The single amber marker here
   (the Watchout label) marks attention, not an action — there is no button anywhere on the page.

2. **No chat rail — despite the day having a chat.** §11 grants a day (unlike a sprint) a
   chat session, so a rail would be *defensible*. I deliberately withheld it. Reasoning: a
   persistent chat rail is a second pane and an action surface; it re-imports exactly the
   "dashboard" feeling the owner rejected, and it softens the "read, not act" thesis I was
   asked to make explicit. The morning strategic read deserves to be the whole page. The
   day's chat and quick-capture-by-talking (§10.1, §17 planner-main) are *working*
   interactions; they belong to the working surfaces, not to the calm morning read. This
   is a different call from the sprint's no-chat (a sprint has no chat at all; Today has one
   and chooses not to show it here).
   - **The tradeoff, stated honestly:** if the product decides quick-capture must live on
     the home screen, the least-invasive home is a single bottom capture bar ("tell the
     planner…") rather than a full rail — it preserves the read column and adds one line of
     action. That is the fallback if "read-only" proves too pure in dogfooding. My
     recommendation is the pure read; the capture bar is the one concession I'd make before
     a full rail.

## Data-shape implication to flag for the lead

Realizing this structured Overview implies work at the **boundary agent**, not just the UI:

- **Focus has no independent home today.** The one-line focus currently lives only at
  `day.plan.root.focus` (§6.3) — part of the plan tree, which this screen drops, and which
  can be null (reject-all clears the plan; §6.2 skips the judgment pass entirely if the
  human pre-planned the day). So Today cannot reliably source the focus from the plan. The
  focus needs a home independent of the plan tree.

- **The brief is one markdown blob, not three sections.** v2 stores `day.brief` as a single
  markdown field (see `assets/screens-day.js`, which renders it via one `markdownBlock`).
  Brief Take / Watchout / If Today Lands are not modeled.

Two ways to close the gap, lightest first:

1. **Convention, no schema change (recommended).** The boundary agent's judgment pass
   (§6.2, contract in the `planning-boundary.md` skill, §17) emits `brief_markdown` with a
   leading focus line and three fixed H2s (`## Brief Take`, `## Watchout`,
   `## If Today Lands`); the Today screen parses those headings to slot the sections and
   lifts the focus line. No contract/DB change — it's a boundary-skill prompt convention
   plus a small frontend parse. Empty/failed brief → each slot degrades to a quiet line
   (§15: no instructional empty state).

2. **Structure at the contract layer (heavier, firmer).** Split the day brief into
   `focus`, `brief_take`, `watchout`, `if_today_lands` fields in the day contract (§14
   contracts-first) and have the boundary adapter return them. Enforces the shape but is a
   schema + adapter + migration change. Worth it only if the parse-by-heading convention
   proves too loose in practice.

I designed to option 1's shape (focus + three headed sections). Flagging so the lead can
decide whether the boundary skill emits the headed structure or the contract is changed.
