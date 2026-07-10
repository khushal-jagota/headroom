# t_fe01 — One Disclosure component

## Outcome

Every expand/collapse surface in the app is one `Disclosure` component: a native
`<details>` with a summary slot, a chevron, and a body wrapper. The two existing
disclosure components (`ContentDisclosure`, `CollapsibleField`) and the five inline
`<details>` projections disappear, as does the board's hand-rolled button-and-state
project collapse. The stage dot becomes a `StageMark` component used everywhere the
`fsec-mark` classes are used today.

## Contract

`web/src/components/Disclosure.svelte`:

- Renders `<details class="disclosure disclosure--{variant}" open={defaultOpen}>` with
  `<summary class="disclosure-summary">` and `<div class="disclosure-body">`.
- Props:
  - `variant?: string` — styling hook only (`content`, `support`, `stage`, `phase`,
    `item`, `settled`, `idea`, `make`, `project`); appended as `disclosure--{variant}`.
  - `defaultOpen?: boolean` (default false).
  - `title?: string` — shorthand summary: title span + trailing chevron (covers today's
    ContentDisclosure).
  - `summary?: Snippet` — full custom summary content for the rich cases (stage sections,
    sprint items/phases, ideas rows, backlog "make", board project headings). When given,
    `title` is ignored. The component still renders the trailing chevron unless
    `chevron="leading"` places it first (sprint items/phases and ideas use a leading
    chevron today) or `chevron="none"` suppresses it.
  - Arbitrary `data-*` passthrough (rest props onto the `<details>` element) so existing
    hooks (`data-field`, `data-stage-state`, `data-phase`, `data-item-id`, `data-idea-id`,
    `data-create`, `data-content-section`, `data-project-section`, `data-project-key`,
    `data-status-group`) survive verbatim.
- One chevron element, one open-rotation rule, one `::-webkit-details-marker` reset —
  defined once in CSS under `.disclosure-*`.

`web/src/components/StageMark.svelte`:

- Renders the stage dot: `<span class="stage-mark stage-mark--{state}" role="img"
  aria-label=...>` where `state: FieldStageVisualState`. Optional rest props for the data
  attributes BoardRoute puts on it (`data-stage-field`, `data-stage-state`, `data-marker`).
- CSS: rename the `.fsec-mark*` family to `.stage-mark*` (same rules, including the
  reduced-motion block), since `fsec` stops existing.

## Migrations (all call sites, none skipped)

1. `ContentDisclosure.svelte` — delete. Call sites (TicketRoute user-note/recap,
   TicketStageSection recap/notes ×3, ApprovalBlock proposal/notes ×4) move to
   `Disclosure` with `title`, `variant="content"` or `variant="support"` (today's `tone`),
   and `data-content-section`. Migrate `.content-disclosure*` CSS into `.disclosure*`
   base + `.disclosure--support` override.
2. `CollapsibleField.svelte` — delete. TicketStageSection's non-review wrapper becomes
   `Disclosure variant="stage"` with a `summary` snippet of `StageMark` + field name span,
   keeping `data-field` and `data-stage-state` on the details element and the
   `defaultOpen` logic unchanged. Migrate the `.fsec` summary/name/body CSS to
   `.disclosure--stage` scoped rules.
3. `BoardRoute` stage rail — replace the two bare `fsec-mark` spans usage with `StageMark`
   (attributes preserved).
4. `SprintRoute` `details.phase` — `Disclosure variant="phase"` with summary snippet
   (name + meta spans), `data-phase`, per-phase `defaultOpen` as computed today.
5. `SprintRoute` `details.item` (both live and settled groups) — `Disclosure
   variant="item"` with summary snippet (current chevron+title+chips+count content,
   minus the hand-rolled chevron span — the component owns the chevron), `data-item-id`.
6. `SprintRoute` `details.sett` (settled group and loose tickets) — `Disclosure
   variant="settled"` with summary snippet (group label + count).
7. `IdeasRoute` `details.idea` — `Disclosure variant="idea"` with summary snippet
   (title + project chip + date). The bodyless `.flat` row stays as-is for now
   (t_fe02 makes it a ListRow).
8. `BacklogRoute` `details.make` — `Disclosure variant="make"` with summary snippet
   (plus sign + label), `data-create="item"`.
9. `BoardRoute` project sections — replace the `collapsedProjects` state array, toggle
   button, and conditional render with `Disclosure variant="project"` per section,
   `defaultOpen` (all projects start expanded today), summary = current heading content
   (label + count). Remove the now-unused collapse state and its chevron CSS. Native
   details keeps the same collapse behavior; drop `aria-expanded`/`aria-pressed`
   bookkeeping in favor of details semantics.

## CSS scope

- New `.disclosure*` base family in `assets/app.css` (summary reset, marker reset, chevron
  glyph + open rotation, body spacing), written once.
- Delete: `.content-disclosure*` family, `.fsec` summary/chevron rules, sprint
  `.phase`/`.item`/`.sett` summary+chevron duplicates, backlog `.make` summary rules,
  ideas `.idea` summary rules, board index-toggle chevron rules. What remains per screen
  is only real per-screen difference (colors, spacing, typography), re-keyed to
  `.disclosure--{variant}` or the existing `[data-screen]` scoping.

## Tests / acceptance

- `tests/e2e/*` currently passes 56/56; the only class selectors in tests are `.it`,
  `.body`, `.plan-tree`, `.day-ticket-row`. If sprint markup changes rename `.it` or
  `.body` containers, update those selectors in the test in the same change without
  weakening any assertion (they assert visibility/text of sprint item rows and bodies).
- All `data-*` attributes listed above render identically (verify by targeted e2e:
  `tests/e2e/test_flows_a.py`, `test_flows_b.py`, `test_board_stage_indicators.py`,
  `test_backlog_ideas.py`).
- `cd web && npm run build && npm run check && npm test` clean.
- `grep -rn "ContentDisclosure\|CollapsibleField\|fsec" web/src assets/` returns nothing.
