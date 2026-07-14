# t_ui04 — Workspace floor

Reference: `orchestration/daily-redesign/workspace.html`; notes.md Workspace
notes + fidelity audit (chat header = availability dot + label; pending row =
three dots + activity label — both already live behavior).

## Outcome
The workspace matches the mockup: restyled rail (chief row, one-line Hide done control,
project sections, slim rows with the existing mark vocabulary, needs-you rows
one step stronger), and the chief pane in the serif voice.

## Contract
1. **Rail** (`BoardRoute.svelte` + styles): Chief of Staff as the top row
   (quiet row treatment, active = overlay background;
   `data-chief-of-staff-button` + `aria-pressed` preserved — e2e asserts both).
   The Hide done toggle sits on one quiet line and keeps `data-hide-done-toggle`
   plus checkbox semantics — it may be visually restyled as text but remains the
   same input. It starts on and is the only visibility filter; there is no status
   or Stage selector and no "Filters" heading.
2. **Project sections**: collapsible as today (`data-project-section`,
   `data-project-key`, `.section-heading-label` preserved — e2e reads the label
   text and counts `.board-workspace-stage-mark` per row). Uppercase label +
   right count per mockup.
3. **Rows**: slim rows per mockup — title + trailing StageMark
   (`.board-workspace-stage-mark` class stays; mark vocabulary unchanged).
   Rows whose ticket carries an ask (`data-ticket-status="awaiting_approval"`
   or state `needs_review`) read one text-step stronger at rest — CSS keyed off
   the existing `data-ticket-status`/`data-ticket-state` attributes, no new
   props. `data-card`, `data-ticket-id`, `.list-row-title` preserved.
4. **Right pane**: chief `ChatPanel` — serif message bodies (shared rule from
   t_ui02), centered measure per mockup. No structural chat changes.
5. Replaced workspace styles deleted this wave.

## e2e / selector notes
Grep for `data-chief-of-staff-button`, `data-workspace-filters`,
`data-hide-done-toggle`, `data-project-section`,
`data-card`, `board-workspace-stage-mark`, `list-row-title` — all preserved.

## Acceptance
Implementer slice green (+ `tests/e2e/test_board_stage_indicators.py` and
chief-of-staff e2e); design review against workspace.html; full `./verify`
green at integration.
