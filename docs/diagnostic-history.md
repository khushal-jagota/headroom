# Diagnostic history waits for an explicit owner decision

Panels keeps diagnostic history until an owner explicitly runs the offline maintenance command. Installation, deployment, startup, restart, background loops, and shutdown do not activate expiry. The code registers no expiry task.

The first policy selects diagnostic events older than fourteen days. It keeps current work, control records, unfamiliar records, and ambiguous tool pairs. All conversations linked to unfinished Tickets remain intact, including old conversations and backlogged work. Chief conversations and unsettled orchestrators remain intact. A report freezes its UTC cutoff and binds its candidates to the database contents and identity. Any change invalidates that report.

Expiry permanently removes selected event payloads. Compaction removes no logical records. These are separate owner decisions. The Ticket authorizes experiments on private copies, never expiry from the live archive. Chief receives the report before the owner decides.

## Produce a current measurement

Use the worktree's Python environment. Set the source path and a new private output directory. Use the current UTC time for a decision report. The command freezes that time for the experiment.

```sh
.venv/bin/python -m planner.maintenance.diagnostic_history_measure \
  --database /absolute/path/to/planner.db \
  --output-dir "$(mktemp -d)/experiment" \
  --as-of-unix "$(date -u +%s)"
```

For historical reproduction, replace the final timestamp with the frozen `as_of_unix` from the historical report. Historical reproduction uses a fixed cutoff. A current decision uses the refresh command above.

The source opens read-only. SQLite creates a consistent online backup. Every schema preparation, deletion, and compaction targets a private copy. The command refuses an existing output directory. Keep the files private because they contain the full archive and detailed removal manifests.

The experiment uses the production writer with every DELETE trigger enabled. It compares that writer's entire removal set against an independent oracle over raw archive rows. It verifies every retained event and every unrelated table. It verifies omission ranges, publication effects, and projections against the retained source events. It refuses unknown projection schemas instead of rebuilding them with an older writer.

The output contains `measurement.json` and `reviewed-report.json`. The measurement records the exact rerun command beside each numerical metric. The command uses a fresh output directory and the same fixed cutoff. The source fingerprint and code revision identify the experiment. Snapshot completion time remains separate from cutoff selection time. A rerun against a newer archive produces different results by design.

The report keeps three figures separate:

- Compaction-only physical recovery preserves every logical row.
- Removed logical payload counts UTF-8 JSON bytes in selected events.
- Extra physical recovery compares equally compacted control and expired copies.

The physical comparison includes actual omission metadata and normal publication overhead. Physical recovery can exceed logical payload because rows, indexes, and freed pages also occupy space. Database file lengths and allocated blocks do not guarantee an equal change in filesystem free space. The report records WAL bytes separately.

Compaction without expiry is the first recommendation. Expiry is not urgent at the previously reported disk headroom. Refresh the measurements before a later decision. Historical research numbers do not substitute for the current guarded experiment.

## Apply and reclaim remain separate

Stop Panels before an owner runs an offline maintenance action. Use the maintenance CLI's explicit owner confirmation and reviewed report. The action acquires database ownership and an exclusive SQLite lock. It refuses active backend processes, stale reports, and unknown schema shapes. It records actual missing sequence ranges in the same transaction as deletion. It preserves sequence watermarks.

Space reclamation runs after expiry as a separate operation. If reclamation fails, committed expiry remains committed. A reclaim retry compacts the database and chooses no new candidates.

Conversation read behavior remains a separate phase under Chief's release. The maintenance code does not change the held API, pagination, or frontend files.

Use a fresh report for the selected database. Reports for private experiment copies cannot authorize a different database.

```sh
PLAN_DB_PATH=/absolute/path/to/planner.db \
  panels maintenance diagnostic-history report \
  --database /absolute/path/to/planner.db \
  --as-of-unix "$(date -u +%s)" \
  --output /absolute/private/path/to/new-report.json
```

The following examples belong to the owner after separate approval and a stopped server. Do not run them from a Worker context. The explicit environment removes Worker identity and selects the same database as the command.

```sh
env -u PLAN_TICKET_ID -u PLAN_SPRINT_ITEM_ID -u PLAN_AGENT_KEY \
  PLAN_ACTOR=owner PLAN_DB_PATH=/absolute/path/to/planner.db \
  panels maintenance diagnostic-history apply \
  --database /absolute/path/to/planner.db \
  --report /absolute/private/path/to/new-report.json --yes
```

Compaction needs no expiry report and removes no logical history.

```sh
env -u PLAN_TICKET_ID -u PLAN_SPRINT_ITEM_ID -u PLAN_AGENT_KEY \
  PLAN_ACTOR=owner PLAN_DB_PATH=/absolute/path/to/planner.db \
  panels maintenance diagnostic-history reclaim \
  --database /absolute/path/to/planner.db --yes
```

## Verification choices

Each test group covers a distinct destructive-data risk. The tests reuse existing database setup. No browser host or provider connection is added.

| Test group | Risk and existing coverage gap | Smallest useful boundary |
| --- | --- | --- |
| Pure classifier | Age boundaries, unfinished ownership, malformed input, tool-pair ambiguity, and unresolved lifecycle facts can release protected data. | Pure rules with explicit permitted sets. |
| Independent oracle | Survivor assertions alone miss an unexpected deletion elsewhere in a real archive. | Raw SQLite rows, an independently selected complete removal set, and unchanged survivors. |
| Guarded maintenance | A transaction can partially commit, accept stale evidence, miss a competing connection, or leave projections inconsistent. | Production admission, DELETE triggers, owner refusal, future-cutoff refusal, schema refusal, database leases, exclusive locks, and atomic rollback. |
| Startup integration | A deployed rule can accidentally run through lifespan or background loops, or fail to reconcile offline dirty facts. Pure rules cannot prove those connections. | Existing `make_app` setup with actual lifespan and loops, no browser or provider. |

Implementation evidence records the focused commands, observed runtimes, and real-copy disk cost. A skipped or blocked experiment does not establish correctness.

The focused implementation checks cover 31 policy cases, 6 independent-oracle cases, and 13 guarded-maintenance cases. The connected startup integration call took 1.44 seconds. It reuses the application setup and adds no browser host. These figures describe the recorded implementation run.
