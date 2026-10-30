# Goals

Goals live under Chief. Each goal has its own agent and conversation. Chief keeps the overall goal surface and sets goals.

A goal stores its Project, title, why it matters, deadline, active state, primary chart, and flexible page document. It also stores its binary checked input and completion display. The page uses the shared page-kit format. Text, checklists, tables, layouts, and supporting charts can appear in that document. Blocks and checklist items keep stable IDs when their order or words change.

Checklists belong to the page. Their checked state does not create a measurement. Numeric charts can reference existing measures or bind a retained dataset. Targets, reference paths, units, and number formats describe the chart. They do not create observations. The goal reader preserves signed values, fractions, missing dates, source health, and observation provenance.

A binary goal is neutral before its due date, yellow on that date, and red afterward. A completed binary goal is green. Completion and active state are independent. A completed goal stays in Active goals until someone explicitly deactivates it. Reactivation preserves completion. The goal agent can change its own page, completion, and active state directly. These edits need no approval step.

The owner, Chief, and the goal's own agent use one writer. Each edit carries the current `expected_revision`. An accepted edit returns the saved goal and page. A stale edit fails instead of replacing a newer document. The caller keeps the draft, reads the current document, and resolves the change.

The detail query does not depend on measurement revisions. The separate readings query depends on both the goal and automatic measures. A collection refresh therefore updates charts without replacing a page draft. Deadline status follows the server's local calendar. The response includes the next local midnight so the browser can refresh status without a database write. Each measure keeps its declared timezone. Without a requested date range, readings include the stored dependency history through the measure's current local date. Missing current readings stay missing instead of displaying an older reading as current.

The goal agent uses the same conversation system as Tickets. The first message starts a conversation. Opening a page alone does not start a model turn. Reset preserves prior conversation history. Goals do not acquire Ticket stages, continuation loops, or a new management hierarchy.

`panels goal list` and `show` read saved goals. `create` and `edit` accept JSON on stdin. `edit` needs `expected_revision`. `read` returns the existing bound measure values, with optional `--first-date` and `--last-date`. The HTTP routes are `/api/goals`, `/api/goals/{id}`, and `/api/goals/{id}/readings`.

Before a write, inspect the accepted request through these read-only commands:

```sh
panels goal schema create --json
panels goal schema edit --json
panels goal schema primary-chart --json
```

Each command returns `schema`, `examples`, and `notes` without stdin or a server request. Replace example identifiers with saved record IDs. CLI help and goal agent startup guidance point to these commands. OpenAPI describes the same create and edit requests from the shared contract reference. [Project measures](measures.md) describes the source and measure discovery route.

_Code paths:_ `src/planner/goals/`, `src/planner/core/request_guidance.py`, and `src/planner/runtime/logic/conversation_start_resolution.py`.

## Native page edits

The owner edits goal title and rationale in place. Project, deadline, active state, completion display, and quantitative thresholds use native inline controls. Each change uses the same revision-checked writer.

Focus loss saves text, dates, and numeric thresholds. Enter saves single-line fields. Ctrl/Cmd+Enter saves multiline text. Escape discards the draft.

Native selects save explicit selection changes. Failed structured edits retain their value and expose Retry save and Discard edit.

On the full goal page, visible authored strings outside charts also support native text edits. This includes nested sections, view labels, callouts, saved metric and property strings, row copy, checklist wording, and table copy. Stable block and item IDs remain intact. Table edits retain original row identity after display sorting. Generated counts, chart titles, and measurement readings keep their existing behavior.

Text blocks retain Markdown, artifact previews, source links, and download fallbacks. Other authored fields remain plain text. Text editors and disclosure, sort, view selection, or completion actions use separate targets. Sidebar previews and the component gallery retain their existing controls without page-text editors.

Each text draft retains its original goal and revision through background refreshes. A stale write fails visibly instead of replacing newer data. The draft remains until an explicit retry or discard. Accepted edits survive reload.

## Sidebar previews

Active goals appear inside the full Chief card, before its Worker groups. Each row links to its goal page. Hover, keyboard focus, or a touch hold opens the goal preview. The preview shows the Project, deadline, full title, and primary content.

A selected completion checklist uses the same checklist as the goal page. Other goals show their native chart, with its available date ranges and manual controls. Binary goals without a selected checklist retain Mark done and Mark not done. Automatic readings remain read-only.

Tab reaches the preview controls from its goal row. Escape inside a value field first cancels its draft. An outer Escape dismisses the preview and returns focus to its connected row. Pointer exit, outside input, and user scroll outside the preview also dismiss it. Internal preview scroll keeps it open. Background refresh and keyboard focus can reposition the preview without dismissal. A successful touch hold survives release and suppresses the release click.

Before dismissal or replacement, a focused value field loses focus while it remains mounted. Its valid draft saves through the native control. Submitted writes continue after the preview closes. Reopen the same goal to see a pending save or a failed save with its native retry control. Manual retries reuse the original request and receipt. A stale manual edit requires an accepted-state refresh and another explicit edit. Saves never retry automatically.

Checklist and binary edits share the goal page's revision-checked writer. Queued checklist commands use the accepted document after the preceding write. Accepted saves refresh the page and sidebar queries. A refresh failure remains a read error and does not turn an accepted save into a failed write. Historical chart selection does not change the current sidebar status.

## Completion displays

Effective completion comes from the selected display's saved input. Panels derives it when it reads a goal. It does not save another completion flag. A binary display uses its saved checked input. A quantitative goal keeps its chart and uses that binary input for completion.

A binary goal can select one saved checklist through the inline Completion display selector. The selector also lists checklists inside layouts. The selected checklist appears in the primary area with its existing item controls. Its original page block stays intact and appears only once. Other checklists remain ordinary page content.

A selected checklist completes the goal only when it contains items and every item is done. An empty checklist stays incomplete. Open and blocked items keep it incomplete. Uncheck an item or add an unfinished item to reopen the goal. Active state stays independent of these results.

The ring shows the accepted number of done items and the total item count. Active goal cards show that same count. The goals table shows done items under Current and total items under Target. Numeric filters compare these counts. Checklist counts do not measure effort, create measurements, or become formula inputs.

A pending save does not change accepted completion or count summaries. If a save fails, the checkbox returns to its accepted state. The checklist shows the error and permits an explicit retry.

Agents select a checklist by its stable block ID through the existing revision-checked goal writer. `primary_checklist_block_id` applies only to binary goals. A null value selects the binary display. This edit selects the saved checklist named `approach`:

```json
{
  "expected_revision": 4,
  "primary_checklist_block_id": "approach"
}
```

Selection resolves against the resulting saved page, so one edit can supply the page and its selector. An explicit missing or non-checklist ID fails atomically. Rename or reorder a block without a new ID to preserve its selection.

Clear the selector or remove its selected block to return to the binary display. Panels copies the previous effective completion into the binary input for this transition. A change to quantitative kind also clears the selection. An explicit `completed` value in the same edit takes precedence over that copied value.

The `completed` response field remains the effective completion for existing clients. In writes, `completed` remains the binary checked-input alias when the resulting display is binary. Existing goals retain their saved binary input. While a checklist stays selected, a matching `completed` value is a compatible no-op. A contradictory value fails because completion follows the saved checklist items. Panels compares against the resulting page, so a final check and `completed: true` can share one edit.

## Supporting files

Each goal keeps notes, research, and evidence in its own managed folder beside the database. Completion, deactivation, and conversation reset preserve that folder and its links. The goal page keeps current content in page JSON. Supporting files and measurements remain separate from that document.

Authored page text blocks render Markdown, preserving prose line breaks. Links to supported goal artifacts show the shared file preview. Open a file from its preview or a conversation link to read it beside the goal conversation. External links remain ordinary links, and unsupported managed files remain downloads. The goal page presents files through these authored links, without a separate file inventory. Folder index pages still represent their supporting assets in the catalog.

The owner, Chief, and the goal's own agent can read, write, list, and delete its managed files. File access is separate from source and manual measurement permissions.

The agent uses the ordinary artifact commands with paths relative to `artifacts/`:

```sh
panels goal artifact write GOAL_ID notes/context.md --body-file context.md --json
panels goal artifact list GOAL_ID --json
panels goal artifact read GOAL_ID notes/context.md
panels goal artifact delete GOAL_ID notes/context.md --json
```

Use `--body-file -` for UTF-8 text on stdin. A write returns its served URL. Reuse the same path to replace a file. Read preserves the exact text, including its final newline. All four commands support `--json`. The list includes openable artifacts. An index page represents its folder, while its supporting assets remain readable by known paths.

Files live under `files/goals/<goal-id>/` beside the database and use `/files/goals/<goal-id>/<relative-path>` links. The CLI convention places artifacts beneath that goal root. The existing managed path rules apply to reads, writes, deletes, and HTML navigation.

_Code paths:_ `src/planner/files/`, `src/planner/goals/cli.py`, and `web/src/components/goals/GoalWorkspace.svelte`.

## Charts and measurement controls

Manual charts use the controls described below. Existing collection scripts, source schedules, corrections, and measure APIs keep their existing behavior.

The approved chart sheet also includes a single progress bar, a time-budget ring, a time-allocation ring, and stacked composition bars. Progress and budget use a positive target as their denominator. Authored allocation and stacked bars use the same point identity across categories; a missing category leaves their total unavailable. Static ring and progress values are nonnegative. A negative measured value remains in the readings payload, but progress and budget summaries treat it as unavailable. Existing line and bar measurements still support signed values. Category colors use the shared palette.

Authored charts can sum the known readings and targets across a selected range. An entirely missing range stays missing. Inspecting a point shows that point instead of the range total. The overview and readings status use all-time data; a chart's temporary range selection does not change that status. Static data uses an authored `all` range when present, otherwise the base series or first authored range. A range may set its own target, such as two conversations per day in Week and ten per week in Month. That target replaces the chart-wide target only while that range is selected; a dated reference path still takes precedence. Time budgets default to an upper limit. Authors can use amber for a below-target comparison, label a threshold as a floor, and express percentage differences in percentage points.

A live chart can bind a retained dataset or named scalar inputs. Dataset bindings choose a category dimension, optional code filters, a field or ratio, and sum or latest aggregation. Named scalar inputs use stable keys. A time budget identifies its spent and planned inputs. The server returns Week, Month, and All projections with range and point summaries. Weighted ratios, totals, differences, and comparison verdicts come from those summaries. The browser formats and plots them without recalculation.

A dataset projection keeps the retained query interval, missing dates, provider limits, and reasons for unavailable values. Available subtotals compare against their configured targets. A latest snapshot excludes categories absent from that snapshot. Inspection follows stable keys through corrections and label changes. If a bound chart has no projection, it shows no current value. The overview and goals table use the supplied All summary, including the planned value for a named scalar budget.

Manual and imported charts compare each available recorded value against its configured target now. Partial or unknown period coverage does not suppress comparison or color. An unfinished day can show a comparison, and a later value can change it. Missing numeric values remain gaps.

This rule applies to targets, reference paths, expectations, named scalar inputs, and range summaries. Inspection and overview readouts use the same comparisons. Authored target direction and inclusive equality keep their existing meaning. Existing aggregate arithmetic determines whether a numeric result exists. A missing date does not neutralize an available subtotal.

Source-authored coverage facts stay separate from target comparisons. Partial period counts remain subtotals, and unknown coverage stays distinct from proven partial coverage. Observation details retain supplied bounds, limitations, reasons, and provenance. A historical date does not establish completeness. Source coverage authoring is described in [Project measures](measures.md#scalar-coverage-facts).

Page tables retain editable string cells and the existing sort and selection components. An optional matrix of cell tones adds the approved outcome colors without replacing the table or changing its stored cell text.

The unlisted application page `#/dev/goal-components` shows all 22 approved chart-sheet examples through the production page, chart, and table components. It uses explicit sample data from October 28, 2026. Local sample controls switch among the approved states without API writes. The chart controls, table sorting, and mobile disclosure use the same components as goal pages. The examples follow the approved responsive grid. The six mobile studies use the production disclosure at their available grid width. An optional 390 px preview shows mobile goal-host behavior for all examples. The page does not add measurement entry or increment controls.

## Manual chart controls

A chart can bind approved manual controls to a shared manual measure. Daily quotas edit the selected date. Cumulative targets edit a dated snapshot of the latest total. Individual readings edit the selected observation. Targets remain chart configuration.

Manual charts show comparisons through the shared hover or tap inspection. They omit the permanent Difference line and repeated date label. Date controls and inspection retain necessary dates. The selected date remains the edit context.

The value is a native input over the shared animated counter. Enter or focus loss commits a changed valid draft. Escape restores the accepted value. A failed or invalid draft blocks date changes and increments. A pending commit finishes before the next explicit chart action. Writes use the accepted measure revision and an explicit date. Failed requests require an explicit retry. The browser does not retry writes automatically.

The application development page includes all three models beside the approved chart examples. These manual samples use local adapters and explicit sample labels. Sample edits do not call measurement APIs or persist outside that page. Real goal charts use the same production controls with the shared authenticated measurement API.

## Formula expectations

A read-only scalar line chart or number readout can store an expected-value formula for each date. It can bind a retained manual measure. Editable manual widgets retain their fixed-target controls. Stacked charts, aggregate sums, and other geometries reject formula expectations explicitly.

The formula takes precedence over saved references and targets. An unavailable formula never falls back to another target. Removing it restores the chart's existing reference and target behavior, including range-specific targets.

Agents save the formula through the existing revision-checked goal edit. For example, this chart field expresses a baseline of 100 plus two per elapsed day:

```json
{
  "expectation": {
    "op": "add",
    "left": {"op": "constant", "value": 100},
    "right": {
      "op": "multiply",
      "left": {"op": "constant", "value": 2},
      "right": {"op": "elapsed_days", "start": "2026-10-01"}
    }
  }
}
```

Expressions compose ordinary arithmetic, powers, minimums, maximums, fixed-date references, and date branches through the shared [measurement evaluator](measures.md). A branch selects its new phase on the named date. Only the selected phase evaluates. Unsupported meanings require explicit explanation; calendar-month growth and repeating completed-period rules remain outside this language.

Goal reads retain the existing values and source health. They also return chart-keyed scalar projections for formula charts. Each point contains actual and expected results, provenance, missing inputs, an exact difference, and a same-date comparison. Only display formatting rounds numbers. A missing or invalid value produces no comparison. Formula results never change goal completion or active state.

Week and Month show the current seven-day and thirty-day windows. All time extends through the deadline even when actual observations stop earlier. Future expected values remain separate from observations and cannot replace the current headline. Missing today remains missing. A request for future dates alone has no current headline. Date inspection uses the formula on that exact date. Phase boundaries and missing expected values break the plotted line.

Explicit date requests override the display interval and can extend beyond the deadline. Defaults start at the earlier of retained history and the current thirty-day window. Formula projections allow at most 3,660 dates per chart, 50,000 chart-date points per response, and 50,000 distinct demanded measurement/date pairs. Larger requests fail with an explicit limit instead of returning partial coverage. Ordinary measure reads keep their existing limits.

Code paths: `src/planner/goals/`, `src/planner/measures/logic/`, and `web/src/lib/charts/`.

_Last verified: October 30, 2026._
