# Deployment

Deployment is how a commit on `main` becomes the app the live host runs, without the host rebuilding it and without a push installing itself. Publication and installation are separate. A push creates a retained release on disposable hosted runners. The live workspace changes only after an owner asks for that exact release. The buttons and the idle-or-now choice are [Updates](updates.md). The Python inside the sealed archive is [Archive runtime](archive-runtime.md).

```text
main → checks → build once → seal archive → smoke → disposable rehearsal
                                                          ↓
                                              retained archive and proof
                                                          ↓
owner request → dedicated runner → stop → verified backup → replace app → health
                                                          ↓
                                        admit work → skills → published HTTP/2
```

## The live tree

Production runs one Git-free application as the existing `vps` user:

```text
~/Deployments/Panels/current/
├── app/     the executable release
├── data/    database, configuration, managed files, deployment records
└── logs/
```

Replacement changes `app`. Data and logs stay. Backups taken on this path include the database and the managed configuration roots. They exclude uploads and Ticket artifacts under `data/files`. See [Backups](backups.md).

The live service is the user unit `panels-live.service`, started with `systemctl --user`. The deployment runner is its own user unit. The workflow does not provision the host and does not change Tailscale Serve. Health is `http://127.0.0.1:8767/api/health`. The database file is `current/data/planner.db`. Agent homes stay `~/.hermes`, `~/.codex`, and `~/.claude`. Provider secrets stay in the service environment, including `~/.config/panels/panels-live.env`. Replacing `app` does not replace that file.

Video uploads need `ffprobe` on the host path. Missing, they refuse new videos. The rest of the app still runs.

Workers never stop the service that hosts them. The operator stops the unit. A systemd-launched supervisor accepts ordinary shutdown signals only from its verified manager. Installers run outside the unit they replace. `nohup` does not leave that cgroup. Owner adoption uses a separate short-lived user unit so stopping Headroom cannot kill the installer.

## What a push proves

Publication runs on GitHub-hosted Ubuntu with Python 3.12 and Node 22. It runs Ruff, mypy, the Svelte check, typechecked Vitest, and the browser catalog. Unit, integration, end-to-end, and browser suites run as separate jobs so one failure does not cancel the others. They all build from the same requested commit. The app is packed once into `panels-app.tar.gz` plus a proof artifact. Smoke and a disposable rehearsal consume that archive. The rehearsal uses synthetic records on an ephemeral machine. It does not touch production. Artifacts are requested for about 30 days. The repository can drop them sooner. A release whose artifacts are gone disappears from the owner’s list.

A pull request’s merged-tree mypy check is not publication. Publication is the push to `main`.

## Replacement and what can be undone

The runner downloads the pinned archive and proof. It does not rebuild. It checks the archive, the runtime contract, and the database schema ancestry before downtime. The schema floor is `video_upload_ledger`. An older or unknown ancestry is refused before the service stops.

The candidate is staged while production is still serving. The runner then stops writers and takes a verified snapshot. Only after that snapshot succeeds does it keep the prior app and replace `current/app`. Uploads and Ticket artifacts are outside the snapshot and outside the replacement.

Until the runner records the local commit, ordinary HTTP, CLI work, conversation recovery, background loops, and file cleanup stay closed. Health and status are up so the candidate can be proved. Each requested-app and prior-app health proof gets 300 seconds after the restart command returns. The installed smoke budget is 45 seconds. Neither number is a promise about how long the site is down.

Failure before that commit restores the snapshot and the prior app, then proves the prior SHA. A failed recovery reports the retained paths. It is not reported as success.

After the commit, ordinary work opens. A later skill failure or a failed published-route check leaves the installed app in place and reports the workflow incomplete. Those failures do not restore the database and do not mark the app unhealthy. The finalizer records the exact attempt, the observed health and recovery failures, the installed SHA, and each workflow check on its own. A confirmed cancellation before installation is reported as neutral. Missing evidence stays uncertain. A stale finalizer cannot overwrite a replacement attempt. If the report itself cannot be read, the saved evidence remains authoritative and the report says it is uncertain. A reporting failure does not change what the update does. An intentional later code rollback does not rewind the database by itself. There is no arbitrary-SHA rollback button. The owner selects another retained release.

The published route check runs after skill reconciliation. The operator sets the Actions variable `PANELS_PUBLISHED_ORIGIN` to the private HTTPS origin. The helper requests `/api/meta` with HTTP/2 and rejects anything that did not negotiate HTTP/2. That check is separate from the loopback health check.

Interrupted agent sessions are captured outside the snapshot. After admission, eligible conversations get one recovery prompt through the normal send path. Nothing uncertain is replayed. See [Conversations](conversations.md).

If the runner disappears before commit, work stays closed. Expired lifecycle evidence becomes a visible recovery-required state. Expiry does not admit traffic and does not restore anything. The owner continues on the deployment runner with an explicit snapshot and fallback. No maximum outage is promised.

## An older database that still has cancelled Tickets

An upgrade refuses a database that contains cancelled Tickets, or that would create them while replaying historical migrations. The refusal leaves the schema and the rows alone. Stop the service, then archive those Tickets with the release you are installing. Run as the owner, with no Worker identity, and with an archive directory whose parent already exists:

```sh
systemctl --user stop panels-live.service
PLAN_DB_PATH=/absolute/path/to/planner.db \
  panels maintenance archive-cancelled-tickets \
  --archive-dir /absolute/path/to/cancelled-ticket-archive --yes
```

The command first stores a verified snapshot of the whole database. Keep that directory as private as the database. Some older files need historical migrations before the cancelled rows exist. This command prepares those migrations and stops before the retirement-removal migration. It does not start the server. Deletion and that preparation share one transaction. Failure rolls both back. Ticket files on disk stay where they are. The archive keeps references, not copies. After it succeeds, retry the upgrade.

## Development must not share the deployed tree

Worktrees install their own `.venv` and their own `web/` and `agent_backends/` dependencies. A shared `node_modules` can write caches into the deployed app and break its integrity proof. Use `scripts/dev` for setup, npm, pytest, and mypy. Setup refuses to delete a `.venv` or a Node directory that is already there. No flag turns the guard off.

A refusal names the command, the path it examined, the path it resolved to, and the protected deployment path. If the sharing is real, make a fresh worktree. Do not delete dependencies through an unresolved link or mount. If independent dependencies are refused by mistake, fix the detector and add a regression check. If the detector cannot see the mount evidence, restore that evidence and retry. Direct Ruff or Python invocations outside these entry points can still write into the deployed tree. The archive digest can show damage afterward. It does not prevent the write. If a write reaches the installed tree, leave it in place and report it. Do not clean the installed tree to force an install through.

The guarded commands and the reason they exist are also in [`AGENTS.md`](../AGENTS.md). This section is the diagnostic meaning of a refusal.

## Pointers

`.github/workflows/deploy.yml` is publication and adoption. `src/planner/environments/deployment.py` and `update_runner.py` perform the replacement. `scripts/dev` and `scripts/development_safety/` are the worktree guard. `ops/panels-environments/` holds the user units.

_Last verified: 2026-10-30 against source. This page does not claim a hosted publication or a production adoption was run from here._
