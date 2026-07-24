# PROGRESS

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
