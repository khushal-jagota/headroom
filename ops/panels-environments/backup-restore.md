# Panels backup and restore runbook

The user timer runs a verified live backup every night at 02:30. It invokes the
deployed application:

```sh
"$HOME/Deployments/Panels/current/app/bin/panels-launcher" \
  environment backup-current \
  --source-db "$HOME/Deployments/Panels/current/data/planner.db" \
  --backup-dir "$HOME/Deployments/Panels/current/data/backups" \
  --current-app "$HOME/Deployments/Panels/current/app"
```

Install `panels-db-backup.service` and `panels-db-backup.timer` in
`~/.config/systemd/user/`, then run:

```sh
systemctl --user daemon-reload
systemctl --user enable --now panels-db-backup.timer
systemctl --user start panels-db-backup.service
systemctl --user status panels-db-backup.service
```

The deployment transaction enters through the same deployed-app command before
replacing `current/app`. A failed backup leaves the live app unchanged.

## Inspect

```sh
find "$HOME/Deployments/Panels/current/data/backups" \
  -maxdepth 1 -type d -name 'snapshot-*' -print | sort
cat "$SNAPSHOT/metadata.json"
```

Only snapshots with verified metadata and matching checksums are eligible for restore.
New snapshots cover the database and its adjacent `worker-settings/` and `skills/`
roots when present. They omit the adjacent artifact `files/` tree. Metadata still uses
`panels-backup-v2` and lists the captured roots. Legacy snapshots retain their original
contents and restore any captured artifacts unchanged. Backup does not own unrelated
entries in the user's provider homes. Successful backups retain the three newest
verified snapshots.

## Verify recovery on isolated copies

This is a proof of recovery, not a production restore. Use a copied snapshot and a
scratch destination with its own database, files, settings, skills, logs, locks,
sockets, provider homes, and available loopback port. Use test mode to prevent
worker dispatch from restored records. Never target the live database, files tree,
or service, and never stop production for this check.

Restore the copied snapshot into that stopped scratch destination:

```sh
panels environment restore \
  --snapshot "$COPIED_SNAPSHOT" \
  --destination-db "$SCRATCH_ROOT/planner.db" \
  --live-stopped
```

Start the application against the restored scratch database with all runtime paths
pointing into the scratch environment. Confirm health and read a known database
record, then stop the scratch application. Repeat for a new snapshot and an
independently prepared legacy snapshot that includes artifacts. Confirm the legacy
artifacts are recovered. For a new snapshot, confirm any preexisting scratch
`files/` tree remains untouched; on an empty destination, missing artifact links or
uploads are expected, but application startup and record reads must still succeed.

## Operator production restore

Use this separate procedure only for an intended production recovery.
Stop live and confirm no Panels process is using the database. Select a verified
snapshot, then run:

```sh
systemctl --user stop panels-live.service
"$HOME/Deployments/Panels/current/app/bin/panels-launcher" environment restore \
  --snapshot "$SNAPSHOT" \
  --destination-db "$HOME/Deployments/Panels/current/data/planner.db" \
  --live-stopped
systemctl --user start panels-live.service
```

Check the local health endpoint and a known record. A new snapshot leaves the live
`files/` tree untouched; a legacy snapshot that captured it restores that content.
Restore validates the complete snapshot before changing live state and removes stale SQLite sidecars before replacing
the database. If verification fails, keep live stopped and select another snapshot.

## Daily local maintenance

`panels-maintenance.service` and `.timer` run
`environment cleanup --apply` as the `vps` user at 03:15. The command collects and
re-proves a fresh inventory at execution time; an earlier dry run is not authority to
remove anything.

See [docs/backups.md](../../docs/backups.md) for the backup correctness model.
