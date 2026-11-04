# Goals

A goal is an outcome Khushal wants by a date, with a page that shows how close it is. Goals sit under Chief. Chief sets them and keeps the overview. Each goal also has its own agent, which keeps the page current. Goals exist so that committed outcomes, and whether they were hit, are visible in one place instead of spread across Tickets.

## What a goal is made of

- **The goal record.** Project, title, why it matters, deadline, and whether it is active.
- **The page.** A flexible document in the shared page kit: text, checklists, tables, layouts, and charts. The page is a dashboard for Khushal. Steps are checklists and numbers are charts. Text adds context that the checklists and charts do not show.
- **The main display.** One checkbox, one checklist, or one chart that stands for the goal in cards, the overview, and the rail.
- **A completion rule.** What counts as done. See below.
- **A conversation.** The goal's agent, on the ordinary [conversation system](conversations.md).
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

Completion and active state are separate. A finished goal stays in Active goals until someone deactivates it. Reactivation keeps the completion.

## Charts and measurements

Charts read the shared, dated [measures](measures.md). A chart can also bind a retained dataset, or carry an expected-value formula for a target that moves over time. The server does the arithmetic and the browser only draws it. A chart's target describes the chart. It never creates a measurement or completes the goal.

Some charts carry manual controls, so Khushal can enter a day's value or a running total on the page. Those entries write to a shared manual measure, not to the goal.

A missing value stays missing. Headroom does not show an older reading as current, and it does not treat an unknown as zero.

## Who edits a goal

Khushal, Chief, and the goal's agent all edit through one writer. Each edit names the revision it started from. If someone else saved first, the edit fails and the draft stays for a retry. A stale edit never overwrites newer work. The goal agent changes its own page, completion, and active state without an approval step. Goals have no Ticket stages.

Khushal edits most text on the page in place. Checklist wording belongs to the agent. Checklist items can carry their own deadlines, which Khushal sets from the page. Item deadlines are colored, but they do not change the goal's own deadline or result.

## Where it shows up

- The **goal page**, with the goal's conversation beside it.
- **Active goals** inside the Chief card in the rail. Hovering or holding a row opens a preview with the main display, which Khushal can check or edit from there.
- The **goals overview**, with cards and a table.
- `panels goal` for agents: list, show, create, edit, read, and artifact commands. `panels goal schema` prints the accepted request shapes with examples, so agents read those before a write.

## Decisions that shape it

- A chart and a finish line are different things. A chart shows progress. Only the completion rule decides whether the goal is done.
- Checklist state is page content, not a measurement.
- Deadline status follows the server's calendar, not the browser's timezone.
- One revision-checked writer for every editor, so concurrent edits fail visibly instead of overwriting each other.

## Code

- `src/planner/goals/`: records, the writer, completion rules, outcomes, the background check, and goal conversations.
- `src/planner/measures/` and `src/planner/datasets/`: the numbers that charts and rules read.
- `web/src/components/goals/` and `web/src/lib/goals/`: the page, rail, previews, and overview.

_Last verified: 2026-11-04._
