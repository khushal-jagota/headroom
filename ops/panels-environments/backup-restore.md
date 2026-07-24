# Panels SQLite backup and restore runbook

This directory provides install inputs. Choose the real host paths in the operator-owned
`backup.env` and `maintenance.env`; none are fixed by the repository.

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

Only snapshots with verified metadata and matching checksums are eligible for restore. Each
snapshot covers the database and the managed-file tree beside it. Successful backup runs retain
the seven newest verified snapshots. The nightly and pre-deployment backup schedules remain
unchanged by status collection.

The managed-file tree is the ticket files, worker-settings, and skills home that live beside the
database. The skills home is resolved the same way the live server resolves it: `PLAN_HERMES_HOME`
when set, otherwise `hermes-home` beside the database. If the live service sets `PLAN_HERMES_HOME`,
set the same value in `backup.env` so the backup and restore commands capture the skills home the
server actually uses.

## Restore

Stop the live service and confirm that no Panels process is using the database. Select a verified
snapshot, then run the stopped-only restore command from the manager environment. Start live again
and check health plus a known record. The restore removes SQLite sidecars before replacing the
database, so stale WAL state cannot be reapplied on startup.

If verification fails, keep live stopped and choose another verified snapshot. The command never
deletes an older snapshot as part of a failed backup attempt.

See [docs/backups.md](../../docs/backups.md) for the system-level model.

## Daily local maintenance

`panels-maintenance.service` and `panels-maintenance.timer` are optional systemd inputs for an
operator-owned daily `panels environment cleanup --apply` run. Copy
`maintenance.env.example` to `/etc/panels/environments/maintenance.env` and set its absolute
`PLAN_DB_PATH`, `PLAN_LOGS_DIR`, and `PLAN_BACKUP_DIR` values to the live environment. Set the
same `PLAN_BACKUP_DIR` in `live.env` so the server status and maintenance use one backup root.
The matching launchd plist is an input for the same command on macOS. Both commands build a fresh
inventory at execution time; a previous dry run is not authority to delete anything. Install them
only with an identity permitted to read the configured logs and backup directory.
