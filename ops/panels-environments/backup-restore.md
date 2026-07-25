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
Each snapshot covers the database and its adjacent managed-file roots, including the
canonical Panels `skills/` authority. It does not own unrelated entries in the user's
provider homes. Successful backups retain the seven newest verified snapshots.

## Restore

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

Check the local health endpoint and a known record. Restore validates the complete
snapshot before changing live state and removes stale SQLite sidecars before replacing
the database. If verification fails, keep live stopped and select another snapshot.

## Daily local maintenance

`panels-maintenance.service` and `.timer` run
`environment cleanup --apply` as the `vps` user at 03:15. The command collects and
re-proves a fresh inventory at execution time; an earlier dry run is not authority to
remove anything.

See [docs/backups.md](../../docs/backups.md) for the backup correctness model.
