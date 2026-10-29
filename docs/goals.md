# Goals

Goals live under Chief. Each goal has its own agent and conversation. Chief keeps the overall goal surface and sets goals.

A goal stores its Project, title, why it matters, deadline, active state, completion state, primary chart, and flexible page document. The page uses the shared page-kit format. Text, checklists, tables, layouts, and supporting charts can appear in that document. Blocks and checklist items keep stable IDs when their order or words change.

Checklists belong to the page. Their checked state does not create a measurement. Numeric charts reference existing measures. Targets, reference paths, units, and number formats describe the chart. They do not create observations. The goal reader preserves signed values, fractions, missing dates, source health, and observation provenance from the existing measure system.

A binary goal is neutral before its due date, yellow on that date, and red afterward. A completed binary goal is green. Completion and active state are independent. A completed goal stays in Active goals until someone explicitly deactivates it. Reactivation preserves completion. The goal agent can change its own page, completion, and active state directly. These edits need no approval step.

The owner, Chief, and the goal's own agent use one writer. Each edit carries the current `expected_revision`. An accepted edit returns the saved goal and page. A stale edit fails instead of replacing a newer document. The caller keeps the draft, reads the current document, and resolves the change.

The detail query does not depend on measurement revisions. The separate readings query depends on both the goal and automatic measures. A collection refresh therefore updates charts without replacing a page draft. Deadline status follows the server's local calendar. The response includes the next local midnight so the browser can refresh status without a database write. Each measure keeps its declared timezone. Without a requested date range, readings include the stored dependency history through the measure's current local date. Missing current readings stay missing instead of displaying an older reading as current.

The goal agent uses the same conversation system as Tickets. The first message starts a conversation. Opening a page alone does not start a model turn. Reset preserves prior conversation history. Goals do not acquire Ticket stages, continuation loops, or a new management hierarchy.

`panels goal list` and `show` read saved goals. `create` and `edit` accept JSON on stdin. `edit` needs `expected_revision`. `read` returns the existing bound measure values, with optional `--first-date` and `--last-date`. The HTTP routes are `/api/goals`, `/api/goals/{id}`, and `/api/goals/{id}/readings`.

## Supporting files

Each goal keeps notes, research, and evidence in its own managed folder beside the database. Completion, deactivation, and conversation reset preserve that folder and its links. The goal page keeps current content in page JSON. Supporting files and measurements remain separate from that document.

The goal workspace lists files through the shared Artifacts disclosure. Files open in the existing preview beside the goal conversation. Markdown and conversation links use the same preview. Folder index pages represent their supporting assets in the catalog.

The owner, Chief, and the goal's own agent can read, write, list, and delete its managed files. This access does not change authority over sources or manual measurements.

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

Interactive measurement entry and increment controls remain outside this implementation. Existing collection scripts, source schedules, corrections, and measure APIs keep their existing behavior.

The approved chart sheet also includes a single progress bar, a time-budget ring, a time-allocation ring, and stacked composition bars. Progress and budget use a positive target as their denominator. Allocation and stacked bars use the same point identity across categories; a missing category leaves the total unavailable. Static ring and progress values are nonnegative. A negative measured value remains in the readings payload, but progress and budget summaries treat it as unavailable. Existing line and bar measurements still support signed values. Category colors use the shared palette.

Charts can sum the known readings and targets across a selected range. An entirely missing range stays missing. Inspecting a point shows that point instead of the range total. The overview and readings status use all-time data; a chart's temporary range selection does not change that status. Static data uses an authored `all` range when present, otherwise the base series or first authored range. A range may set its own target, such as two conversations per day in Week and ten per week in Month. That target replaces the chart-wide target only while that range is selected; a dated reference path still takes precedence. Time budgets default to an upper limit. Authors can use amber for a below-target comparison, label a threshold as a floor, and express percentage differences in percentage points.

Page tables retain editable string cells and the existing sort and selection components. An optional matrix of cell tones adds the approved outcome colors without replacing the table or changing its stored cell text.

The unlisted application page `#/dev/goal-components` shows all 22 approved chart-sheet examples through the production page, chart, and table components. It uses explicit sample data from October 28, 2026. Local sample controls switch among the approved states without API writes. The chart controls, table sorting, and mobile disclosure use the same components as goal pages. The examples follow the approved responsive grid. The six mobile studies use the production disclosure at their available grid width. An optional 390 px preview shows mobile goal-host behavior for all examples. The page does not add measurement entry or increment controls.

## Manual chart controls

A chart can bind approved manual controls to a shared manual measure. Daily quotas edit the selected date. Cumulative targets edit a dated snapshot of the latest total. Individual readings edit the selected observation. Targets remain chart configuration.

The value is a native input over the shared animated counter. Enter or focus loss commits a changed valid draft. Escape restores the accepted value. A failed or invalid draft blocks date changes and increments. A pending commit finishes before the next explicit chart action. Writes use the accepted measure revision and an explicit date. Failed requests require an explicit retry. The browser does not retry writes automatically.

The application development page includes all three models beside the approved chart examples. These manual samples use local adapters and explicit sample labels. Sample edits do not call measurement APIs or persist outside that page. Real goal charts use the same production controls with the shared authenticated measurement API.
