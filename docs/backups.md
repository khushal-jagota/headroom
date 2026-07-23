# Database and managed-file backups

Panels protects its canonical SQLite record and its managed-file tree with small local
snapshots. A backup can run while Panels is serving requests: SQLite copies the live database
through its online backup API, so committed WAL data is included without stopping the
application, and the managed-file tree is copied alongside it.

```
live SQLite database ──online copy──┐
managed-file tree    ──file copy────┤
                                     ▼
                            temporary snapshot
                            │ integrity + checksums
                            ▼
                    atomic verified snapshot
                            │ keep newest three
                            ▼
                    operator-selected restore
```

Each snapshot is a directory containing `database.sqlite`, `metadata.json`, and — when any
managed root exists — a `files/` directory holding a copy of each captured root plus a
`manifest.json` of every file's checksum. Metadata records the deployed revision, creation time,
the database checksum, the format, the list of captured managed roots with the manifest's
checksum, and the fact that verification passed.

The managed-file tree is the durable state that lives beside the database: **ticket files**
(`files/`), **worker-settings** (`worker-settings/`), and the **skills home**
(`hermes-home/skills/`). All three anchor on the database's directory. The skills home is only
captured once it exists; until then its absence is a normal empty capture, not a failure.

The temporary directory is never published, and a snapshot publishes only when both the database
integrity check and the managed-file manifest verify. A failed copy, integrity check,
verification, metadata write, or atomic publish therefore leaves existing verified snapshots
untouched. Retention runs only after publication and removes the single oldest recovery point
after the new one succeeds. If that removal fails, the older recovery point remains.

The database and managed-file tree are independently verified recovery artifacts captured
back-to-back at nightly granularity; they are not a single transactional point-in-time. This is
recoverability to recent state, not point-in-time versioning.

The repository command is:

```sh
panels environment backup --source-db /path/to/planner.db --backup-dir /path/to/backups --deployed-revision "$REVISION"
```

Restore is deliberately stopped-only. It validates the whole snapshot — the database and every
managed-root manifest — before touching anything. It restores the database first (staging any
existing `-wal` and `-shm` files and atomically replacing it), then restores each captured
managed root to its live location through an atomic directory swap. A database replacement
failure restores the old database sidecars; the managed roots roll back among themselves if any
one swap fails:

```sh
panels environment restore --snapshot /path/to/backups/snapshot-... --destination-db /path/to/planner.db --live-stopped
```

Both commands derive the managed-file roots from the database's directory, so the nightly setup
needs no additional inputs.

The concrete nightly setup, configurable paths, inspection commands, and pre-deployment hook
are in [ops/panels-environments/backup-restore.md](../ops/panels-environments/backup-restore.md).

Code paths: `src/planner/environments/backup.py` and `src/planner/environments/cli.py`.

## Handoffs

The live service and its operator-owned paths are described in [runtime environments](environments.md).

## Deferred

Off-host copies, automatic rollback, and a broader disaster recovery product remain out of scope
until a separate recovery design exists.

_Last verified: 2026-07-23._
