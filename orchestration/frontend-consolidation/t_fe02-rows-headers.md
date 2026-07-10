# t_fe02 — One list row, one section heading, one screen header

Depends on t_fe01 (sprint items and ideas are Disclosure summaries by now).

## Outcome

The six hand-rolled "title on the left, metadata on the right, hover state" rows become
one `ListRow` component. Group labels ("Label · count") and doc-screen headers
("Title + meta pill") each become one small component. The dead `EntityRow.svelte` and
`.day-ticket-row` CSS are removed.

## Contract

`web/src/components/ListRow.svelte`:

- Renders `<a>` when `href` is given, `<button type="button">` when `onclick` is given,
  and a plain `<div>` when neither is given (the static mode — ideas' bodyless `.flat`
  row is non-interactive today and must stay a non-interactive element).
- Structure: optional `leading` snippet (e.g. a state tag), `<span class="list-row-title">`
  from a `title` prop, optional `trailing` snippet (chips, counts, dates, stage marks).
- Base class `list-row` plus `variant?: string` → `list-row--{variant}` styling hook
  (`ticket`, `backlog`, `idea`, `board`). `active?: boolean` → `active` class (board
  selection). Rest-props passthrough for `data-*` attributes and a `class` merge for
  contexts that keep an extra hook.
- One hover rule, one title-color-on-hover rule, one grid/flex row rule in CSS under
  `.list-row*`. `entity-row-title` is replaced by `list-row-title` everywhere (it is a
  bare CSS hook today, used in four screens: sprint, backlog, ideas, board — ReviewRoute
  does not use it). e2e references `.entity-row-title`, `.board-workspace-item-label`,
  `.flat`, and `.it` — grep `tests/e2e/` for every class touched and update selectors
  with equivalent assertions.

`web/src/components/SectionHeading.svelte`:

- `<div class="section-heading">` with `label`, optional `count` (renders `· n`),
  optional `variant` (`settled`/`done` for sprint's muted variants). Replaces sprint
  `.glabel` and `.glabel2`, backlog `.glabel`, ideas `.glabel`, and the label+count
  content inside board project-section summaries (the summary snippet composes
  SectionHeading; the Disclosure stays from t_fe01).

`web/src/components/ScreenHeader.svelte`:

- `<header class="screen-header">` with a row of `title` + optional `meta` snippet
  (pills). Replaces backlog `.head`, ideas `.head`, and the sprint header row (sprint's
  InlineEdit title renders via a `titleContent` snippet instead of the plain string —
  support both).

## Migrations (all call sites)

1. Sprint ticket rows `.tk` (two places: item bodies and loose tickets) — ListRow with
   `leading` = state tag span, `trailing` = priority span; keep `data-ticket-id`, href.
2. Sprint item summary row content (inside t_fe01's Disclosure summary) — compose the
   summary from ListRow's title/trailing conventions or plain spans sharing
   `list-row-title`; the chips/count spans keep their `data`-free classes but move to
   shared classes where identical.
3. Backlog `.brow` — ListRow (`href="#/backlog"`, `data-item-id`, trailing chips).
4. Ideas `.flat` (bodyless idea) — ListRow in static `<div>` mode, trailing chip + date.
   e2e selects `.flat` in `test_backlog_ideas.py` — update the selector, same assertions.
   Ideas with bodies keep their Disclosure from t_fe01, with the summary sharing the
   same row classes.
5. Board `.board-workspace-item-row` — ListRow (`onclick` select, `active` for the
   selected card, trailing StageMark inside the stage rail span; keep `data-card`,
   `data-ticket-id`, `data-ticket-state`, `data-ticket-status`).
6. Delete `web/src/components/EntityRow.svelte` (imported nowhere) and the
   `.day-ticket-row` / `.day-ticket-row .entity-row` CSS block. One e2e test references
   `.day-ticket-row` — read it first: if the selector can no longer match anything (dead
   markup), fix the test to assert the current real behavior rather than deleting the
   assertion; report what it was checking in the final summary.
7. Replace `entity-row-title` class usage in all four screens with `list-row-title`
   (sprint `.it` spans ×3, backlog `.bt`, ideas `.it` ×2, board item label). Update the
   `.entity-row-title`, `.it`, `.body`, and `.board-workspace-item-label` selectors in
   e2e tests where the classes change; assertions stay equivalent.

## CSS scope

- New `.list-row*`, `.section-heading*`, `.screen-header*` families written once.
- Delete or reduce to real differences: `[data-screen="sprint"] .tk*`,
  `[data-screen="backlog"] .brow*`, `[data-screen="ideas"] .flat`, sprint/backlog/ideas
  `.glabel*`/`.glabel2*`, backlog/ideas `.head*` blocks, board item-row rules,
  `.entity-row*` family (fold what ListRow needs into `.list-row*`).

## Tests / acceptance

- Targeted e2e: `test_flows_a.py`, `test_flows_b.py`, `test_backlog_ideas.py`,
  `test_board_stage_indicators.py`, `test_ticket_hard_delete_e2e.py`.
- `cd web && npm run build && npm run check && npm test` clean.
- `grep -rn "EntityRow\|entity-row\|day-ticket-row" web/src assets/ tests/` returns
  nothing (or only intentional test updates).
