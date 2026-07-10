# t_fe03 — One Button, one static Pill, Chip covers blocked-by, selects share a class

Depends on t_fe01/t_fe02 only through shared files; no structural dependency.

## Outcome

Six styled button families become one Button component with two visual variants. The five
hand-rolled static pills become a Pill component. The one hand-rolled chip becomes a Chip
variant. The two visible native selects share one styled class. Chat controls are out of
scope (they are one implementation already).

## Contract

`web/src/components/Button.svelte`:

- Renders `<button type="button">`, or `<a>` when `href` is given (several "buttons" today
  are links: FilePreview's open/download actions).
- Props: `variant?: "primary" | "quiet" | "pill"` (default quiet), `disabled`, `onclick`,
  `href` (+ `download`, `target`/`rel` passthrough via rest props), children snippet,
  rest-props for `data-*` (`data-accept`, `data-approve`, `data-skip`, `data-commit`,
  `data-copy`, `data-ticket-takeover-toggle`, `data-chief-of-staff-button`,
  `data-open-ticket`, `data-review-revision-send`).
- CSS: one `.button` base + `.button--primary` + `.button--quiet` + `.button--pill`.

Consolidation mapping (visual normalization to the nearest variant is intended — this is
the footprint reduction; do not invent new variants to preserve tiny differences):

- `.button` / `.button--primary` (ProposalCard until t_fe04, FilePreview links,
  review revision send, review error-state Skip) → Button.
- `.commit` (backlog + ideas submit) → Button `primary` keeping `data-commit`.
- `.approval-approve` (ApprovalBlock) → Button `primary` keeping `data-approve`/`data-accept`.
- `.approval-skip` (review outside actions) → Button `quiet` keeping `data-skip`.
- `.pill-button` (ticket header Take over / Copy) → Button `pill` keeping its data attrs.
- `.board-workspace-chief-button` → Button `quiet` keeping `data-chief-of-staff-button`
  and the `aria-pressed` state (rest-prop).
- `.chat-jump`, `.chat-send`, `.chat-slash`, `.chat-image`, SegmentedControl's `.opt`,
  and `.chat-menu-item` stay as they are (single implementations, chat-specific design).

`web/src/components/Pill.svelte`:

- Static counterpart of EnumPill: `<span class="pill">` with optional `keyLabel`
  (`.pill-key`) and children snippet for the value. Replaces the five hand-rolled pills:
  sprint dates pill, backlog head count pill, ideas head count pill, ticket "due" pill
  (the hidden date input stays inside as children), ticket "sprint" pill.
- EnumPill stays; if trivial, recompose it on the same CSS (no markup change required).

`Chip.svelte` addition:

- Support the sprint `blocked by <title>` chip: `variant="blocked-by"`, `keyLabel`
  ("blocked by") + value. Fold the inline `chip--blocked-by` span in SprintRoute into
  Chip; move its `.k` key styling next to the chip family CSS.

Select unification — DROPPED at integration (decision D77): the boxed board filter and
the inline sentence-embedded scope selects turned out to share nothing beyond
`cursor: pointer`; a shared class carrying only that is a token, and actually
normalizing font/padding/appearance would visibly redesign one of the two controls.
They remain two controls. EnumPill's invisible overlay select was always out of scope.

## Tests / acceptance

- e2e relies on `data-accept`, `data-approve`, `data-skip`, `data-commit`, `data-copy`,
  `data-ticket-takeover-toggle`, `data-scope-ceiling`, `data-scope-atcap`,
  `data-status-filter` — all preserved. `data-status-filter={statusFilter}` lives on the
  `<select>` element itself (`BoardRoute.svelte:166`), alongside `bind:value` — keep it
  there. e2e also selects `a.button` (FilePreview open-preview links in
  `test_ticket_file_previews.py`) — the Button component's link mode must keep the
  `button` class on the `<a>`, or the selectors get updated with equivalent assertions.
- Targeted e2e: `test_flows_a.py`, `test_flows_b.py`, `test_backlog_ideas.py`,
  `test_ticket_file_previews.py`, `test_cli_verbs.py` if it touches the ticket header.
- `cd web && npm run build && npm run check && npm test` clean.
- `grep -rn "approval-approve\|approval-skip\|pill-button\|board-workspace-chief-button\|chip--blocked-by\|\.commit" web/src assets/` shows only the consolidated definitions.
