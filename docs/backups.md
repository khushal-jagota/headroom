# Backups

A backup is a small verified snapshot of Headroom's record, taken so an operator can return it to a recent state. It holds the database, the worker-settings folder, and the managed skill copies. It runs while Headroom is serving, using SQLite's online backup.

## What a snapshot holds

- **The database.** Everything canonical, including the skill history, notification choices, registered devices, and the Web Push identity. Restoring the database keeps registered devices working.
- **Worker settings and managed skills.** Copied beside the database. Headroom could rebuild the skill files from the database, but the snapshot keeps them anyway.

It leaves out Ticket files and uploads under `files/`, agent homes, and anything off the host. On a fresh machine those artifacts are simply absent, and the app still starts from the restored database. Off-host copies are not part of this system.

The database and the folders are copied back to back, not at one transactional instant. This is recovery to a recent night, not point-in-time history.

## How snapshots are made and kept

A snapshot is written to a temporary folder and published only after the database integrity check and the file checksums pass. A failure leaves earlier snapshots untouched. Only the newest few verified snapshots are kept. Two callers take them, both through the installed app: the nightly timer described in [Environments](environments.md), and [Deployment](deployment.md) before an update whose new release changes the database schema, while the server still runs. If that backup fails, the live app is not replaced. A mismatch against the installed app record logs a warning and does not block a backup. The strict file check at install time stays.

## Restore

Restore runs only while the service is stopped. It validates the whole snapshot before it touches anything, then replaces the database and swaps in each folder the snapshot holds. Each step undoes itself if it fails, but the restore as a whole is not one transaction. It restores only the folders the snapshot lists, so a new snapshot leaves an existing `files/` tree alone.

Deployment's own recovery uses a snapshot only before the new app is proven. After that, nothing rewinds data automatically. Any other restore is a deliberate operator act. Prove a snapshot on isolated copies, never on live, and never stop production just to check that a snapshot opens. The steps are in [ops/panels-environments/backup-restore.md](../ops/panels-environments/backup-restore.md).

## Decisions that shape it

- Verified or nothing: a snapshot exists only after its checks pass.
- The record, not the artifacts. Uploads and Ticket files are outside the snapshot and outside app replacement.
- Restore is offline and validates first.

## Code

- `src/planner/environments/backup.py`: capture, verification, retention, and restore.
- The `environment backup`, `backup-current`, and `restore` commands in `src/planner/environments/cli.py`.
- `ops/panels-environments/panels-db-backup.*`: the nightly unit.

_Last verified: 2026-11-04._
