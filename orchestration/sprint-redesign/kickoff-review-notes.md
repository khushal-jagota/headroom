# Sprint Overview page — design notes (rev 6)

Same design system as the tracking + ticket + today mockups (decoded in `principles.md`).
Grounded in SPEC §3.1 and `assets/screens-sprint.js`. (SPEC §5 freeze is retired — see below.)

The sprint is **two pages** (owner-decided): the **Sprint Overview** page (this one —
Kickoff / Mid-sprint Review / Sprint Review) and the **Sprint Tracking** page (the items).

## What changed in rev 6 (owner decisions)
1. **Freeze is removed entirely.** Nothing in a sprint freezes — the old §5 kickoff/review
   lock is retired. Kickoff and Sprint Review lose their Freeze buttons and become plain
   headed inline-editable content, exactly like the Mid-sprint Review. **No buttons, no
   amber, no ✓/●/○ marks anywhere on the sprint.** Consequence: amber's "irreversible
   commit" meaning now lives ONLY on the ticket's Approve. (Kickoff is now editable, not a
   read-only frozen frame — just collapsed for reference in the rendered review state.)
2. **No sprint number.** The header shows just the sprint name ("Waitlist to first cohort").
3. **Dates = 2 weeks.** The range is a 14-day span (Jul 1 – Jul 14).

## Retained from earlier revisions
- **Three sections, Today idiom:** Kickoff / Mid-sprint Review / Sprint Review — headed,
  inline-editable content. The **Mid-sprint Review** is one review with a few fixed headed
  sub-fields; no list, no "add" button.
- **Sub-headers chosen (flagged for confirmation):** **Where we stand** (status vs the bet) ·
  **What's changed** (new info since kickoff) · **What to adjust** (cut / add / reprioritize).
  A read → learn → decide arc; "What to adjust" over "What to cut" since mid-sprint decisions
  include adding and reprioritizing.
- **Naming:** end section **Sprint Review**; middle **Mid-sprint Review** (no two-"Review" clash).
- **Pages + nav:** two-item tab `Sprint Overview · Sprint Tracking`, current filled, on both
  pages. No arc.

Rendered state: **review-open** (end of sprint) — it populates all three sections. The other
two phases are described in the mockup's header comment.

## The structure: three sections; the phase decides which is OPEN
Each section is the same `<details>` primitive. Nothing freezes and nothing commits — the
only thing the phase drives is which section is **open** by default (progressive disclosure);
all three are editable at any time.

| phase | Kickoff | Mid-sprint Review | Sprint Review |
|---|---|---|---|
| kickoff-open | open (you write the plan) | collapsed (empty) | collapsed (empty) |
| running | collapsed (the frame) | **open** — mid-point reflection | collapsed (empty) |
| review-open (rendered) | collapsed (the frame) | open (reference) | **open** — the write-up |

Open-when-relevant: Kickoff while you're writing the plan, then collapsed as the frame you
refer back to; the Mid-sprint Review once it has content; the Sprint Review at the end. In
review-open the Mid-sprint Review stays open because it is the reference you write the
retrospective *from*.

## Which principle drove which decision
- **P8 (state drives the surface):** the phase table above is the whole design. Three
  sections, three phases, each section active in one — the ticket's "surface the active
  field" generalised to a three-beat sprint arc.
- **P1 (one primitive):** Kickoff, Mid-sprint Review, and Sprint Review are one `<details>`
  header primitive (name + meta + chevron, body not inset — no ✓/●/○ marks). All three share
  the same field primitive (header/label + editable prose); the Mid-sprint Review is a few
  such fields.
- **P7 (edit in place):** every field edits silently in place (contenteditable, no
  affordance). There are **no buttons** anywhere — nothing freezes, so nothing commits. All
  three sections are plain editable content.
- **P5 (one accent):** with Freeze gone, there is **no amber on the sprint at all** — no
  amber button and no ✓/●/○ marks. Amber's "irreversible commit" meaning now lives only on
  the ticket's Approve. (The one amber left is the universal text-edit caret shared with
  Today and the ticket; if you want literally zero amber including the caret, it's a one-line
  change.)
- **P4 / P6:** seams not boxes, no insets; five type roles; the three section names sit one
  step up (15, semibold) so they read as the page's structure.

## Mid-sprint Review — content model (build note)
It is **one review with a few fixed headed sub-fields** (Where we stand / What's changed /
What to adjust), each inline-editable in place. No list, no dated entries, no add button.

**Build implication (flagged, not built):** this is a small **set of markdown fields** (the
sub-sections) on the sprint — the same structured-fields shape as the kickoff/review sections
and the Day fields. NOT a single field, and NOT the `weekly_addenda` `{date,text}` list
(§3.1). And with **Freeze retired (rev 6), the build has no freeze logic** — the §5
kickoff/review lock goes dormant, like the plan-tree. Backend not built here; flagged for the build.

## Ambiguities — now resolved by the owner
1. **Depth of the Mid-sprint Review — RESOLVED (rev 5): a few headed editable fields.**
   Not the `weekly_addenda` list, not a single field — one review with a small set of fixed
   headed sub-fields (see the build note above). Deliberately no backend built.
2. **"Mid-sprint Review" vs end "Review" clash — RESOLVED.**
   - **Naming:** the end section is **Sprint Review**, the middle is **Mid-sprint Review** —
     "Mid-sprint" vs plain "Sprint" carries the distinction; no more two-"Review" clash.
   - **Relationship — RESOLVED to a light cue, not a merge:** they stay mostly independent,
     but the Sprint Review is written with the agent aware of the Mid-sprint Review (its raw
     material). Realised as a quiet reference line atop the Sprint Review body — not a
     structural pull/summarise. If a real summarise-from-mid-sprint interaction is ever
     wanted, that's a separate design.

## Nav — decided (the arc is gone)
The two pages are navigated by a **two-item tab switcher** at the top of the column:
`Sprint Overview · Sprint Tracking`, current one filled. On this page Sprint Overview is
current; on the Tracking page Sprint Tracking is current — the same pair, mirrored. Why a tab
pair over a single "← other page" link: with exactly two pages, a segmented pair **names both
pages and shows which you're on**, making the other page discoverable — one line of restrained
chrome, not a bar. (This replaces the tracking page's old arc, whose nav role the tab pair now
owns; the tracking arc is removed and a quiet progress line kept in its place.)

## Where a Notion designer would still push
- **"Mid-sprint Review" and "Sprint Review" still share the word "review."** The naming is
  settled and the distinction is real, but a first-time user skimming may still blur the two;
  the ordering (mid → end) and the "end of sprint" meta are what carry it.
- **The active section is a `<details open>`** — collapsible for one-primitive consistency,
  but an active writing session you can accidentally collapse is slightly odd; the ticket
  surfaces its active thing non-collapsibly. Deliberate consistency trade.
- **Nothing signals phase transition now.** With Freeze gone, the page has no explicit
  "kickoff set" / "review final" moment — the phase (which section opens) must be inferred
  from the sprint's lifecycle / dates, not from an action on this page. Calmer, but a reviewer
  may ask how the system knows the sprint has moved on.
- **Only review-open is drawn.** Running (where the Mid-sprint Review is the *active* body —
  arguably its most important state) is described, not rendered. A reviewer would want the
  running state mocked to confirm the middle section carries a phase on its own.
- **primary_bet appears twice** (the surfaced frame line + the Kickoff "Primary bet" field) —
  intentional frame-vs-field duplication, same as tracking, but a sharp eye catches it.
