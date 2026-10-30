# Backups

A backup is a small verified snapshot of the canonical database, the worker-settings root, and the managed skills, taken so an operator can return the record to a recent state. It can run while Headroom is serving. SQLite’s online backup copies committed data, including the WAL, without stopping the app. Settings and skills are copied beside it.

The snapshot is not the artifact tree. Ticket files and uploads under `files/` are excluded. Skill files are copies Headroom can rebuild from the database when it opens. The snapshot still keeps them, and the database keeps the immutable skill history, so a restore has both the current files and the older text. Agent homes are not part of the snapshot. They only link at the managed copies.

Notification choices, device subscriptions, and the Web Push identity live in the database. Restoring the database keeps registered devices. Replacing only the application does not rotate that identity.

## How a snapshot is kept

The temporary directory is published only after the database integrity check and the managed-file checksums succeed. A failure leaves the previous verified snapshots untouched. Retention then keeps the three newest verified snapshots. The database and the managed roots are captured back to back. They are not one transactional instant. This is recoverability to a recent night, not point-in-time history.

The format is `panels-backup-v2`. Metadata lists the roots actually captured. Older snapshots that included `files/` can still restore that root. A new snapshot’s restore leaves an existing `files/` tree alone. On a fresh machine, excluded artifacts are simply absent. The app can still start from the restored database.

Nightly and pre-deployment automation both call the deployed launcher:

```sh
"$HOME/Deployments/Panels/current/app/bin/panels-launcher" environment backup-current \
  --source-db ~/Deployments/Panels/current/data/planner.db \
  --backup-dir ~/Deployments/Panels/current/data/backups \
  --current-app ~/Deployments/Panels/current/app
```

The user timer runs that as `vps`. Deployment runs the same command before it replaces `current/app`. If the backup fails, the live app is unchanged.

A general checkout can take the same kind of snapshot with `panels environment backup`, passing the database path, the backup directory, and the deployed revision.

## Restore

Restore runs only while the service is stopped. It validates the whole snapshot before it touches the destination. It replaces the database first, staging any `-wal` and `-shm` files, then swaps each captured managed root into place. A database failure puts the old sidecars back. Managed roots roll back among themselves if a later swap fails.

```sh
panels environment restore \
  --snapshot /path/to/backups/snapshot-... \
  --destination-db /path/to/planner.db \
  --live-stopped
```

Roots come from the database path, so the nightly layout needs no extra map. Restore changes only the roots the snapshot lists.

Prove a restore on isolated copies. Do not restore onto the live database, and do not stop production merely to prove that a snapshot opens. The concrete timer, inspection commands, and the isolated proof are in [ops/panels-environments/backup-restore.md](../ops/panels-environments/backup-restore.md).

Deployment’s automatic app recovery, described in [Deployment](deployment.md), restores a snapshot only before the new release is committed. It is not a general rewind of persistent state after that point.

Off-host copies are not part of this system.

## Pointers

`src/planner/environments/backup.py` and the `environment` commands in `src/planner/environments/cli.py`.

_Last verified: 2026-10-30._
