# Maintenance

Diagnostic history is the append-only event record conversations and related diagnostics accumulate. Headroom keeps it until the owner explicitly decides to expire or compact it. Install, deploy, startup, restart, background loops, and shutdown do not run expiry. Nothing in the server lifespan is registered to do it.

That wait is the product decision. The history is evidence. Reclaiming space is allowed, and it is a separate act from deleting payloads, and both wait for a reviewed report on a stopped server.

## What a report is allowed to remove

The policy selects diagnostic events older than fourteen days. It keeps current work, control records, unfamiliar records, and ambiguous tool pairs. Every conversation linked to an unfinished Ticket stays, including old conversations and backlogged work. Chief and unsettled Managers stay. A report freezes its UTC cutoff and is valid only for the database contents it measured. Any change invalidates it. A report from a private copy cannot authorize a different database.

Expiry removes selected event payloads. Compaction removes no logical rows. It only gives pages back. The recommendation when disk is not urgent is compaction without expiry. Refresh the measurement before a later decision. An old report’s numbers are not a substitute.

## How the owner runs it

Stop Headroom first. Measure against a private output directory. The source database is opened read-only. Deletions in the experiment hit a copy.

```sh
.venv/bin/python -m planner.maintenance.diagnostic_history_measure \
  --database /absolute/path/to/planner.db \
  --output-dir "$(mktemp -d)/experiment" \
  --as-of-unix "$(date -u +%s)"
```

The output includes `measurement.json` and `reviewed-report.json`. Keep them private. They contain the archive and the removal manifest. The report separates three figures: space recovered by compaction while keeping every row, the logical bytes of payloads expiry would remove, and the extra physical recovery of expiry plus compaction. File length is not a promise about free space on the volume.

Apply and reclaim are later commands, after a separate approval, with the server stopped and with Worker identity cleared from the environment. Apply takes the reviewed report and refuses a stale one, an active backend process, or an unexpected schema. Reclaim does not take an expiry report and does not choose new rows to delete. If reclaim fails, a committed expiry stays committed. Retry reclaim on its own.

```sh
env -u PLAN_TICKET_ID -u PLAN_MANAGER_ID -u PLAN_AGENT_KEY \
  PLAN_ACTOR=owner PLAN_DB_PATH=/absolute/path/to/planner.db \
  panels maintenance diagnostic-history apply \
  --database /absolute/path/to/planner.db \
  --report /absolute/private/path/to/new-report.json --yes
```

```sh
env -u PLAN_TICKET_ID -u PLAN_MANAGER_ID -u PLAN_AGENT_KEY \
  PLAN_ACTOR=owner PLAN_DB_PATH=/absolute/path/to/planner.db \
  panels maintenance diagnostic-history reclaim \
  --database /absolute/path/to/planner.db --yes
```

A fresh report for the apply is produced with `panels maintenance diagnostic-history report` against that same database. Do not run these from a Worker.

Cancelled-Ticket recovery for an old database that cannot migrate is a different command, on the deployment path. See [Deployment](deployment.md).

## Pointers

`src/planner/maintenance/`. The measure module is `planner.maintenance.diagnostic_history_measure`.

_Last verified: 2026-10-30._
