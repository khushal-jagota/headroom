# Backups

A backup is a small verified snapshot of Headroom's record, taken so an operator can return it to a recent state. It holds the database, the worker-settings folder, and the managed skill copies. It runs while Headroom is serving, using SQLite's online backup.

## What a snapshot holds

- **The database.** Everything canonical, including the skill history, notification choices, registered devices, and the Web Push identity. Restoring the database keeps registered devices working.
- **Worker settings and managed skills.** Copied beside the database. Headroom could rebuild the skill files from the database, but the snapshot keeps them anyway.

It leaves out Ticket files and uploads under `files/`, agent homes, and anything off the host. On a fresh machine those artifacts are simply absent, and the app still starts from the restored database. Off-host copies are not part of this system.

The database and the folders are copied back to back, not at one transactional instant. This is recovery to a recent night, not point-in-time history.

## How snapshots are made and kept

A snapshot is written to a temporary folder and published only after the database integrity check and the file checksums pass. A failure leaves earlier snapshots untouched. Only the newest few verified snapshots are kept. Two callers take them, both through the installed app: the nightly timer described in [Environments](environments.md), and [Deployment](deployment.md) before an update whose new release changes the database schema (including a TypeScript release with pending schema steps). Every Mac trial takes one too. The installed launcher forwards backup and maintenance commands to the same CLI as manual operations. The timer units stay unchanged for a TypeScript release. A mismatch against the installed app record logs a warning and does not block a backup. The strict file check at install time stays.

## The update snapshot

The update snapshot is the restore point if the update fails before the new home proves healthy. The update splits its work around the stop, so that the home is down for the shortest time:

1. While the home still serves, the update takes the online copy and runs the full integrity check on it. If that check fails, the update stops there and the home keeps running.
2. After the stop, the installer brings the copy up to the stopped database. It compares the two files block by block, writes only the blocks that differ, and computes the checksum in the same pass. The snapshot is then byte-identical to the stopped database, in the same format as every other snapshot.
3. After the new home passes health, the installer runs the full integrity check on the published snapshot, and then retention. If that check fails, the installer logs the failure, and the snapshot loses its verified mark.

The pages that the home wrote between steps 1 and 2 get no integrity check before the schema steps run. Step 3 checks them, and restore checks every image again before it replaces the database. If the update fails in step 2, the live app is not replaced.

## Restore

Restore runs only while the service is stopped. It validates the whole snapshot before it touches anything, then replaces the database and swaps in each folder the snapshot holds. Each step undoes itself if it fails, but the restore as a whole is not one transaction. It restores only the folders the snapshot lists, so a new snapshot leaves an existing `files/` tree alone.

Deployment's own recovery, including the next start after an installer was killed mid-install, uses a snapshot only before the new app is proven. After that, nothing rewinds data automatically. Going back by hand after a schema step needs that update's snapshot, because the previous app refuses a database at a different revision. Data written after the step is lost. Any other restore is a deliberate operator act. Prove a snapshot on isolated copies, never on live, and never stop production just to check that a snapshot opens. The steps are in [ops/panels-environments/backup-restore.md](../ops/panels-environments/backup-restore.md).

## Decisions that shape it

- Verified or nothing: a snapshot exists only after its checks pass. The update snapshot finishes its last check after the home serves again.
- The record, not the artifacts. Uploads and Ticket files are outside the snapshot and outside app replacement.
- Restore is offline and validates first.

## Code

- `server/environments/backup/`: capture, verification, retention, and restore.
- The `environment backup`, `backup-current`, and `restore` commands in `server/environments/cli.ts`.
- `ops/panels-environments/panels-db-backup.*`: the Linux nightly unit.
- [Mac source installation](../ops/mac-home/installation.md): the login-session backup schedule and isolated restore procedure.

_Last verified: 2026-11-26._
