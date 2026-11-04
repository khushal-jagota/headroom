# Measure, expire, and compact diagnostic history

Owner-run, offline maintenance of the conversation diagnostic history. The policy and
the reasons are in [docs/diagnostic-history.md](../../docs/diagnostic-history.md). Do
not run any of this from a Worker.

## 1. Measure

Stop Headroom first. Measure against a private output directory. The source database
is opened read-only, and the experiment's deletions hit a copy.

```sh
panels maintenance diagnostic-history measure \
  --database /absolute/path/to/planner.db \
  --output-dir "$(mktemp -d)/experiment" \
  --as-of-unix "$(date -u +%s)"
```

`.venv/bin/python -m planner.maintenance.diagnostic_history_measure` takes the same
arguments.

The output includes `measurement.json` and `reviewed-report.json`. Keep them private:
they contain the archive and the removal manifest. The report separates three figures:
space recovered by compaction while keeping every row, the logical bytes of payloads
expiry would remove, and the extra physical recovery of expiry plus compaction. File
length is not a promise about free space on the volume.

When disk space is not urgent, the recommendation is compaction without expiry (step 4
only). Refresh the measurement before any later decision. An old report's numbers are
not a substitute.

## 2. Get approval

Apply and reclaim are later commands, run only after a separate approval of the
measured result.

## 3. Expire (apply a fresh report)

With the server stopped, produce a fresh report against the same database the apply
will change:

```sh
panels maintenance diagnostic-history report \
  --database /absolute/path/to/planner.db \
  --as-of-unix "$(date -u +%s)" \
  --output /absolute/private/path/to/new-report.json
```

Then apply it, with Worker identity cleared from the environment:

```sh
env -u PLAN_TICKET_ID -u PLAN_MANAGER_ID -u PLAN_AGENT_KEY \
  PLAN_ACTOR=owner PLAN_DB_PATH=/absolute/path/to/planner.db \
  panels maintenance diagnostic-history apply \
  --database /absolute/path/to/planner.db \
  --report /absolute/private/path/to/new-report.json --yes
```

Apply refuses a stale report or one for another database, an active backend process
using the database, or an unexpected schema. Nothing is expired when it refuses.

## 4. Reclaim (compact)

```sh
env -u PLAN_TICKET_ID -u PLAN_MANAGER_ID -u PLAN_AGENT_KEY \
  PLAN_ACTOR=owner PLAN_DB_PATH=/absolute/path/to/planner.db \
  panels maintenance diagnostic-history reclaim \
  --database /absolute/path/to/planner.db --yes
```

Reclaim does not take a report and does not choose rows to delete. If it fails, a
committed expiry stays committed. Retry reclaim on its own.

## 5. Restart

Start Headroom again when maintenance is finished.
