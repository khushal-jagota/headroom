# Goals

A goal is an outcome Khushal wants by a date, with a page that shows how close it is. Each goal is held by Chief or by one Manager, and a holder can have none, one, or several. The holder keeps the page current. Goals have no agent or conversation of their own. Goals exist so that committed outcomes, and whether they were hit, are visible in one place instead of spread across Tickets.

## What a goal is made of

- **The goal record.** Project, title, why it matters, deadline, and whether it is active.
- **The page.** A flexible document in the shared page kit: text, checklists, tables, layouts, and charts. Table columns resize and retain their widths per page in this browser. Wide tables scroll within the page, including on phones. The page is a dashboard for Khushal. Steps are checklists and numbers are charts. Text adds context that the checklists and charts do not show.
- **The main display.** One checkbox, one checklist, or one chart that stands for the goal in cards, the overview, and the rail.
- **A completion rule.** What counts as done. See below.
- **A file folder.** Notes, research, and evidence that the page can link to.

```
 measures ─────────────────────► charts on the page
     │
     └──► numeric completion rule ──┐
 checkbox or checklist ─────────────┴──► completion date ──┐
 deadline ─────────────────────────────────────────────────┴──► hit or miss
```

## How completion works

There are two kinds of goal.

- A **binary** goal is done when its checkbox is checked, or when every item on its chosen checklist is done.
- A **numeric** goal has a finish line that is separate from its chart. For example, a chart can show monthly revenue while the goal finishes when MRR reaches $10,000. The rule compares a [measure](measures.md) against a target, from a first date onward.

Headroom records the date a goal completes. For a numeric goal, the background clock checks the rule against retained measurements, without a page visit or an agent turn. It saves the earliest date the finish line was met, so a later dip does not undo the hit. A corrected measurement can move or clear that date.

The completion date against the deadline gives the result. On or before the deadline is a hit. Later is a miss. An unfinished goal becomes a miss when its deadline passes. `panels goal` and the API report each result and the hit rate across all goals that have one. The browser does not show them.

Completion and active state are separate. A finished goal stays on its holder's page until someone deactivates it. Reactivation keeps the completion.

## Charts and measurements

Charts read the shared, dated [measures](measures.md). A chart can also bind a retained dataset, or carry an expected-value formula for a target that moves over time. The server does the arithmetic and the browser only draws it. A chart's target describes the chart. It never creates a measurement or completes the goal.

Some charts carry manual controls, so Khushal can enter a day's value or a running total on the page. Those entries write to a shared manual measure, not to the goal.

A missing value stays missing. Headroom does not show an older reading as current, and it does not treat an unknown as zero.

## Who edits a goal

Khushal, Chief, and the holding Manager all edit through one writer. Each edit applies to the current saved record. A failed edit retains its draft for an explicit retry. The holder changes the page, completion, and active state without an approval step. Goals have no Ticket stages.

Khushal edits most text on a separate Goal page in place. On Manager and Chief pages, the same text is read-only. Checklist wording belongs to the holder. Checklist items can carry their own deadlines, which Khushal sets from the page. Item deadlines are colored, but they do not change the goal's own deadline or result.

## Permanent deletion

Khushal, Chief, and the holding Manager can permanently delete a goal. The goal page asks for confirmation. Agents confirm with `panels goal delete GOAL_ID --yes`.

Deletion removes the goal, its page and files, and its own completion and outcome records. The goal leaves the hit-rate statistics, rail, and navigation. Open views stop showing the deleted goal. Other goals and their history stay intact. Shared measures, datasets, and their retained observations stay intact.

## Where it shows up

- The **goal page**, with its holder's conversation beside it.
- **Holder pages.** Chief and each Manager have a page in the same page kit, where Tickets is one of the blocks. A Manager with one goal is shown as that goal under the Manager's name, and both its Goal and Manager links open that page. A Manager with several goals shows active goals as cards. Chief also uses cards for active goals, even when it holds only one. `panels goal make-manager` turns a Chief goal into a new Manager that holds it.
- **The rail header and preview.** A status tag summarizes the worst active goal status for that holder. On Manager header hover or focus, the tag slides left as Settle fades in. Reduced motion changes the positions immediately. Chief has no Settle. Chief and each Manager keep their existing goals. The preview keeps the recap and adds a chart for one numeric goal or a compact checklist for one checklist goal. A lone yes-or-no goal uses its compact completion display. Several goals use rows. Settings can restore the goal rows in the sidebar instead.
- **All goals**, a table of every goal, opened from Goals in the main navigation.
- `panels goal` for agents: list, show, create, edit, delete, read, and artifact commands. `panels goal schema` prints the accepted request shapes with examples, so agents read those before a write. `panels page` reads and edits the separate Manager and Chief documents. `panels manager workspace` identifies which document supplies a Manager's visible page.

## Decisions that shape it

- A chart and a finish line are different things. A chart shows progress. Only the completion rule decides whether the goal is done.
- Checklist state is page content, not a measurement.
- Deadline status follows the server's calendar, not the browser's timezone.
- One writer for every editor, with each edit applied to the current saved record.

## Code

- `src/planner/goals/`: records, the writer, completion rules, outcomes, holders, and the background check. `src/planner/managers/pages.py`: Chief and Manager page documents.
- `src/planner/measures/` and `src/planner/datasets/`: the numbers that charts and rules read.
- `web/src/components/goals/` and `web/src/lib/goals/`: the page, rail, previews, and overview.

_Last verified: 2026-11-11._
