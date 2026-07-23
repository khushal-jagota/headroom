# Panels SQLite backup and restore runbook

This directory provides install inputs. Choose the real host paths in the operator-owned
`backup.env`; none are fixed by the repository.

## Setup

Copy `backup.env.example` to the host and set `PANELS_BACKUP_SOURCE_DB`, `PANELS_BACKUP_DIRECTORY`,
`PANELS_CURRENT_RELEASE`, and `PANELS_ENVIRONMENT_MANAGER_PYTHON`. The backup reads the
current deployed revision from the validated release manifest each time it runs. Install
`panels-db-backup.service` and `panels-db-backup.timer`, then enable the timer. The timer runs
nightly at 02:30 local host time and the backup does not stop `panels-live`.

Before a deployment changes live state, run the shared hook. It records the revision that owns the
database at that moment:

```sh
PANELS_ENVIRONMENT_MANAGER_PYTHON=/chosen/manager/.venv/bin/python \
PANELS_BACKUP_SOURCE_DB=/chosen/state/planning.db \
PANELS_BACKUP_DIRECTORY=/chosen/state/backups \
PANELS_CURRENT_RELEASE=/chosen/releases/0123456789abcdef0123456789abcdef01234567 \
  ./ops/panels-environments/pre-deploy-backup.sh
```

The deployment system supplies its own paths; the backup never reads Git state from live.

## Inspect

```sh
find "$PANELS_BACKUP_DIRECTORY" -maxdepth 1 -type d -name 'snapshot-*' -print | sort
cat "$SNAPSHOT/metadata.json"
```

Only snapshots with verified metadata and a matching checksum are eligible for restore. Successful
backup runs retain the seven newest verified snapshots.

## Restore

Stop the live service and confirm that no Panels process is using the database. Select a verified
snapshot, then run the stopped-only restore command from the manager environment. Start live again
and check health plus a known record. The restore removes SQLite sidecars before replacing the
database, so stale WAL state cannot be reapplied on startup.

If verification fails, keep live stopped and choose another verified snapshot. The command never
deletes an older snapshot as part of a failed backup attempt.

See [docs/backups.md](../../docs/backups.md) for the system-level model.
