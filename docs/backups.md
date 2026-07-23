# Database backups

Panels protects its canonical SQLite record with small local snapshots. A backup can run while
Panels is serving requests: SQLite copies the live database through its online backup API, so
committed WAL data is included without stopping the application.

```
live SQLite database ──online copy──> temporary snapshot
                                      │ integrity + checksum
                                      ▼
                              atomic verified snapshot
                                      │ keep newest seven
                                      ▼
                              operator-selected restore
```

Each snapshot is a directory containing `database.sqlite` and `metadata.json`. Metadata records
the deployed revision, creation time, checksum, format, and the fact that verification passed.
The temporary directory is never published. A failed copy, integrity check, metadata write, or
atomic publish therefore leaves existing verified snapshots untouched. Retention runs only after
publication and removes the single oldest recovery point after the new one succeeds. If that
removal fails, the older recovery point remains.

The repository command is:

```sh
panels environment backup --source-db /path/to/planning.db --backup-dir /path/to/backups --deployed-revision "$REVISION"
```

Restore is deliberately stopped-only. It checks the selected snapshot, stages any existing
`-wal` and `-shm` files, then atomically replaces the destination database. A replacement failure
restores the old database sidecars; after success, stale canonical sidecars are absent:

```sh
panels environment restore --snapshot /path/to/backups/snapshot-... --destination-db /path/to/planning.db --live-stopped
```

The concrete nightly setup, configurable paths, inspection commands, and pre-deployment hook
are in [ops/panels-environments/backup-restore.md](../ops/panels-environments/backup-restore.md).

Code paths: `src/planner/environments/backup.py` and `src/planner/environments/cli.py`.

## Handoffs

The live service and its operator-owned paths are described in [runtime environments](environments.md).

## Deferred

Off-host copies, managed-file or Hermes backups, automatic rollback, and a broader disaster
recovery product remain out of scope until a separate recovery design exists.

_Last verified: 2026-07-23._
