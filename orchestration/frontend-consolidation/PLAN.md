# Frontend component consolidation — program plan

Owner intent: reduce the component footprint. The same visual jobs are currently done by
several hand-rolled projections per screen, each with its own per-screen CSS family. Fewer,
shared components make the later design pass (spacing, structure, polish) tractable. This
program consolidates; it does not redesign. Visual output may normalize slightly where two
projections of the same thing merge, but layout, behavior, and information content stay.

## Ground rules (apply to every ticket)

- **The e2e contract is `data-*` attributes.** Every `data-*` attribute in current markup is
  preserved exactly — name and value — unless the ticket explicitly says otherwise. The e2e
  suite selects mostly on them, but NOT exclusively: tests also select on element+class
  combinations (`details.make`, `details.idea`, `.flat`, `.entity-row-title`,
  `.board-workspace-index-toggle`, `.board-workspace-index-heading-main`,
  `.board-workspace-item-label`, `.board-workspace-stage-mark`, `.proposal-card`,
  `.approval-draft`, `.fval`, `a.button`, `.it`, `.body`, `.day-ticket-row`, and possibly
  more). Before renaming or removing ANY class or changing an element tag, grep
  `tests/e2e/` for it; update every affected selector in the same change, keeping each
  assertion equivalent (same condition being tested, never loosened or deleted). Each
  ticket lists its known test-selector impacts; the grep is still mandatory because those
  lists may be incomplete.
- **No behavior changes.** Same links, same clicks, same open/closed defaults, same
  keyboard handling, same aria semantics (an equivalent element swap — button+state to
  native `<details>` — is allowed where a ticket says so).
- **CSS consolidates with the markup.** When a hand-rolled projection moves onto a shared
  component, its per-screen CSS block is deleted or reduced to genuine per-screen
  differences. Do not leave dead CSS behind.
- **Svelte 5 idiom** (`$props`, `$derived`, `$state`, snippets), matching the existing code.
- Implementers run `cd web && npm run build && npm run check && npm test` and may run
  *targeted* e2e files (`.venv/bin/pytest tests/e2e/<file>`), but never full `./verify` —
  the orchestrator serializes full verify runs after integration.
- Update `docs/frontend.md` only where the component story materially changes; keep it
  plain-language.

## Tickets, in execution order (serial — they share `assets/app.css` and the routes)

1. **t_fe01-disclosure** — one Disclosure component; retire `ContentDisclosure`,
   `CollapsibleField`, and five inline `<details>` projections plus the board's manual
   project collapse. Also introduces `StageMark` (the stage dot currently drawn with
   `fsec-mark` classes in two places).
2. **t_fe02-rows-headers** — one ListRow component for the six hand-rolled row
   projections; SectionHeading (label · count) and ScreenHeader (title + meta pill);
   delete dead `EntityRow.svelte` and `.day-ticket-row` CSS.
3. **t_fe03-buttons-pills-selects** — one Button component (button or link) with
   primary/quiet variants replacing six styled button families; a static Pill component
   for the five hand-rolled pills; Chip gains the `blocked-by` case; the two visible
   select projections share one CSS class.
4. **t_fe04-approval-surface** — merge `ProposalCard` into `ApprovalBlock`, dedupe
   ApprovalBlock's four internal action-group copies, and fold TicketStageSection's
   hand-rolled dropped-state proposal display into the same component.
5. **t_fe05-scaffold-forms-utils** — ResourceState wrapper for the eight copies of the
   error/loading scaffold; shared FormField for the backlog/ideas capture forms; lib-level
   `labelize` and date helpers replacing three and two copies respectively.

## Per-ticket pipeline

Ticket specs in this directory are the contracts (written by the orchestrator, reviewed by
Codex before execution). Per ticket: one implementer sub-agent works this worktree to the
spec → Codex reviews the diff read-only → orchestrator addresses findings, runs full
`./verify`, and commits the green wave on this branch. Tickets run strictly serially.

## Success criteria

- `./verify` fully green after each ticket (baseline green run recorded first).
- Component count in `web/src/components/` goes down or stays flat while all inline
  projections disappear; no route hand-rolls a row, disclosure, pill, or approval surface
  it could take from a component.
- The per-screen CSS families for merged projections (`.tk`, `.brow`, `.idea`, `.flat`,
  `.item`, `.phase`, `.sett`, `.make`, `.glabel`, `.glabel2`) are gone or reduced to
  genuine per-screen differences.
