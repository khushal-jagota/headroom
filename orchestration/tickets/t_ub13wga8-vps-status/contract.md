# Contract — t_ub13wga8 lightweight VPS status and safe cleanup

## Outcome

Panels provides one lightweight, sanitized operational snapshot through an SSH-usable CLI command and a manually refreshed header popover. The snapshot covers environment/release identity, verified backup recency, disk pressure, known Panels workloads, worktrees, logs, cleanup candidates, and nullable CPU/load/RAM/swap fields. On macOS those resource fields remain explicitly unavailable; real Linux collection and thresholds belong to VPS deployment Ticket `t_qrdamx8z`.

A dry-run-first cleanup command rotates configured logs, keeps the newest seven verified backups, and removes only expired temporary state created by known Panels operations after proving it is unreferenced. It never kills processes or automatically removes worktrees, caches, prepared environments, selected releases, or ambiguous state.

## Existing contracts

Implement against the current environment, backup, release, deployment, server/API, resource-cache, and shell contracts. Do not redesign them:

- `src/planner/environments/contracts.py`
- `src/planner/environments/backup.py`
- `src/planner/environments/deployment.py`
- `src/planner/environments/materialize.py`
- `src/planner/environments/cli.py`
- `src/planner/core/server.py`
- `web/src/App.svelte`
- `web/src/lib/resourceCatalogue.ts`
- `web/src/lib/types.ts`

New environment-domain modules are allowed when they create a clear boundary for status collection or cleanup. Do not add a database table, event kind, background browser polling, monitoring history, alerting platform, automatic process control, or a persistent preview-environment model.

## Accepted UI direction

Use the selected **header popover**, not a top-level Status route. Keep the popover concise; fuller evidence belongs in the CLI. Planning reference: `/files/tickets/t_ub13wga8/artifacts/vps-status-layout-comparison.html`.

## Acceptance

Focused tests must prove:

- the CLI and `/api/vps-status` expose the same sanitized snapshot shape;
- nullable CPU/load/RAM/swap is honest on the current Mac path;
- environment, release, backup, disk, process, worktree, log, and cleanup facts correlate only from evidence and preserve unknowns;
- status collection and rendering never expose credential values or unbounded command arguments;
- the header popover is manually refreshed and renders healthy, warning, unavailable, and review-needed states without polling;
- cleanup dry-run and apply share one inventory;
- only explicitly recognised, expired, root-contained, unreferenced Panels temporary state can be deleted;
- backup retention keeps seven verified snapshots and never treats unverified/ambiguous directories as disposable;
- no cleanup path kills a process or deletes a worktree, cache, prepared environment, or selected release;
- live docs explain the CLI, popover, schedules, permissions, and the Linux follow-up boundary.

Focused gates: relevant environment/CLI/server unit tests, frontend tests, and the focused Playwright case. Repository-wide completion is one final canonical `./verify` on the settled ticket branch.
