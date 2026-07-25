# PROGRESS

## Current work cycle (2026-07-25): Move + redesign Workspace filter (`t_nup9skrv`)

Isolated on `ticket/t_nup9skrv-workspace-filters` from current `staging` (`a2c0ee0`).
The Workspace project filter moved from above Chief of Staff to below it, and stopped
being a native `<select>`: at rest it is a mini header (active project in the serif
voice, one step below the bucket titles, with a small caret); clicking it opens a
dropdown menu of All projects / each project / No project, active one ticked. Built as
a custom trigger + popover menu reusing the `AcpConversationPane` menu idiom
(click-outside + Escape close). It rides the existing `selectedProjectId` state and its
stale-project reset `$effect`, so filtering and live-invalidation recovery are
unchanged. Only two files carry the change: `web/src/routes/BoardRoute.svelte` and
`assets/app.css`; the dead `.board-workspace-chief-peer + .disclosure--workspace-bucket`
gap rule is retired (the filter's margin-bottom now carries that gap).

The Workspace Playwright test now drives the menu (open, read items, Escape closes,
pick narrows buckets, trigger label + `data-active-project-id` update, stale-project
reset), and asserts the filter renders after Chief of Staff and sits in the tab order
between it and the first bucket. Screenshots of both states are attached to the ticket.
Current stage: implementation landed in the worktree; `./verify` run for the full gate.

## Current work cycle (2026-07-24): Restore day-scoped Workspace (`t_p9chftfg`)

Implementation is isolated on `ticket/t_p9chftfg-day-workspace` from current `staging`
(`ce32d117`). The approved contract restores `/api/board` to the current 5am-aware planning
day, adds one project filter over the left-panel roster, and preserves the current status
buckets, title-only rows, ticket inspector, routing, and conversations. Hide done and status
filters remain absent.

Current stage: implementation is landed in the isolated worktree. `/api/board` resolves the
current planning day at the 5am boundary and the board query filters through `day_tickets`.
The Workspace has one accessible project selector derived from each card's effective project;
it filters only the roster before status buckets are built, preserves the open inspector, and
returns to All projects when live invalidation removes its selected concrete project.

Focused backend unit tests, Ruff, Svelte check/build, and focused Workspace Playwright tests
pass. Independent review asked for the disappearing-project recovery, both sides of the 5am
boundary, All-projects recovery, and explicit other-day exclusion; each finding is now covered.
Targeted re-review reports no unresolved violation or introduced regression.

Canonical implementation `./verify` passed on the settled product/source/test tree: Ruff and MyPy
clean, 1,451 unit tests passed, Svelte check/build and all frontend tests passed, and 128
Playwright e2e tests passed (`VERIFY: PASS`). The implementation package is approved.

Current stage: Closeout. Next: commit the reviewed package, bring current `staging` into the
Ticket branch, run the required verification on that prospective integrated revision, then
advance and push `staging`, confirm its remote SHA and rolling pull request, and clean up the
Ticket worktree and branch. Blockers: none.
