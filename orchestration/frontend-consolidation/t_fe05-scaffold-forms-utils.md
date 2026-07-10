# t_fe05 — Resource scaffold, form fields, lib utilities

Depends on t_fe03 (Button) for the forms.

## Outcome

The eight copies of the error/loading scaffold become one wrapper component. The two
capture forms share field primitives and global form CSS. Three copies of label
formatting and two copies of date formatting become lib functions.

## Contract

`web/src/components/ResourceState.svelte`:

- Props: `error: unknown`, `loading: boolean`, `hasData: boolean`,
  `loadingText: string`, children snippet.
- Renders: ErrorLine when `error`; `<div class="quiet-line">{loadingText}</div>` when
  `loading && !hasData`; otherwise children. Nothing more — data-dependent empty states
  ("No current sprint.", "No unscheduled items.") stay in the routes as today.
- Migrate all eight scaffolds: BoardRoute, SprintRoute, DayRoute, TicketRoute,
  ReviewRoute (outer queues scaffold only — its inner detail-loading branches keep their
  bespoke staleness logic), BacklogRoute (list area), IdeasRoute (list area), and the
  create-error spots stay as plain ErrorLine.

`web/src/components/FormField.svelte`:

- `<div class="form-field">` with `<div class="form-label">{label}<span
  class="form-label-opt"> — optional</span></div>` (opt flag prop) and a children snippet
  for the control. Replaces backlog's `.fl`/`.fl-opt` labels and gives ideas' inputs the
  same wrapper where labels exist.
- CSS: promote the duplicated `[data-screen="backlog"]`/`[data-screen="ideas"]` input
  rules (`.in`, `.title-in`, `.detail-in`) to one global `.form-*`-scoped family (keep
  the class names on the inputs stable if e2e uses them — check first; tests select via
  `data-input`, which must be preserved). The submit state machine (creating/createError/
  clear-on-success) stays inline in each route — it is a few lines and the two routes
  clear different fields.

`web/src/lib/ui.ts` additions (and call-site migrations):

- `labelize(value: string): string` — underscores to spaces, first letter capitalized.
  Replaces `TicketStageSection.labelFor`, `ApprovalBlock.displayLabel`, and
  `SprintRoute.prettyState` (prettyState does not capitalize — check each call's
  rendering: sprint state tags render lowercase via CSS/text; if capitalization would
  change visible text, add a `capitalize` boolean or keep lowercase output and capitalize
  via the existing CSS; do not change rendered output).

`web/src/lib/dates.ts` (new):

- `monthDayLabel(date: Date)` and `weekdayLabel(date: Date)` (DayRoute), and
  `relativeDayLabel(seconds: unknown)` (IdeasRoute's relDate). Both routes import; the
  two hand-rolled month/weekday arrays disappear.

## Docs

- Give `docs/frontend.md` a short plain-language paragraph on the shared component set as
  it now stands after t_fe01–t_fe05 (Disclosure, ListRow, SectionHeading, ScreenHeader,
  Button, Pill, Chip, StageMark, ApprovalBlock, ResourceState, FormField, InlineEdit,
  MarkdownBlock, ChatPanel/ChatComposer, FilePreview, EnumPill, SegmentedControl,
  ScopePairPicker, ErrorLine) — one line each on what it is for.

## Tests / acceptance

- Targeted e2e: `test_backlog_ideas.py`, `test_flows_a.py`, day-screen coverage
  (grep for `data-day-` tests), review screen tests.
- `cd web && npm run build && npm run check && npm test` clean.
- Visible text unchanged: loading strings, labels, date renderings identical for the
  same inputs.
