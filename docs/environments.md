# Environments

Headroom runs as one Unix user on the VPS, the existing `vps` account. Live, staging, and Ticket servers are separated by directories and by the environment they are launched with. They are not separated by Linux accounts. Provider logins stay that user’s real Hermes, Codex, and Claude homes. Headroom does not copy those homes into a role account.

```text
~/Deployments/Panels/current/{app,data,logs}     live, Git-free
~/projects/Panels/Panels/                        source, and staging state under data/environments/staging
<ticket worktree>/data/                          one Ticket server’s private state
```

Live listens on loopback port 8767, behind the existing Tailscale Serve route. Staging and Ticket servers take a free loopback port and run only while someone needs them. The user units under `ops/panels-environments/` cover live, the deployment runner, and backups. There is no staging unit.

## Live

Prepare records the live contract. It does not pre-create `current/data` or `current/logs`. The first exact-app deployment has to be able to create that layout in one transaction. The user service runs `current/app/bin/panels-launcher serve` with explicit live paths. `systemctl --user` starts and stops it. Interactive commands use `current/app/bin/panels`, which the `panels` wrapper on the host follows.

A supervisor started by systemd records that parent and accepts SIGINT or SIGTERM only from the verified manager. If Linux hides the manager’s executable, the supervisor checks the D-Bus owner and the process start time, and it checks again before it honors a signal. An unknown sender is refused. systemd’s own stop timeout then uses SIGKILL. That delay is intentional. Workers stop their own Ticket servers by the PID recorded at launch, and only that PID.

Renaming the account, moving host state, or changing Tailscale Serve is separate operator work. A code change does not do it.

## Staging

Staging is a foreground process from the source checkout. Its fake database, files, and logs persist under `data/environments/staging`. `panels environment prepare --kind staging` records the roots and a credentials file. `panels environment run --kind staging` prints the loopback URL it actually bound. Stop it when the work is done. `environment reset` restores the fake fixture and keeps the prepared paths.

## Ticket servers

A Ticket server runs from an isolated git worktree. Its database, files, logs, locks, and sockets stay in that worktree’s `data/`. Confirm the imported package and every printed `PLAN_*` path point at that worktree before serving. Headroom does not publish a Ticket server through the live app. Something Khushal must review is a file in the live Ticket tree, or a [preview](previews.md) registered against a trusted dev server. Stop the Ticket server when the active work ends. Remove its state, worktree, and branch at Consequences.

## What a launch is allowed to see

Every environment launch rebuilds the process environment from an allowlist. It keeps the user’s normal home, locale, terminal, path, and temp directories, adds validated provider credentials, and sets the `PLAN_*` paths the contract owns. Ambient `PLAN_*` values are not forwarded. Live, staging, and Ticket roots may not nest inside each other. Credential contents are never written into a manifest or printed by inspect.

Tests follow the same idea with synthetic homes. The suite freezes the real skill-link targets before collection and points Codex, Claude, and Hermes at fresh private homes. The tripwire against copying a real login into one of those homes, and the cases it cannot see, are documented on `tests/unit/test_no_codex_credential_copies.py` and in [Conversations](conversations.md).

Building the Python package needs ICU headers, `pkg-config`, and a C++ compiler, because the indexed ordering uses PyICU. On Debian that is `libicu-dev`, `pkg-config`, and `build-essential`. The runtime needs the matching ICU libraries. The sealed production archive carries its own, which is [Archive runtime](archive-runtime.md).

## Status and cleanup

`panels environment status --json` reads the local filesystem and process list without calling the HTTP server. `GET /api/vps-status` keeps that response for compatibility. The browser’s smaller summary, `GET /api/vps-status-summary`, carries the deployed SHA, the deployment outcome, CPU, RAM, disk, and the age of the latest verified backup. Missing measurements come back as an explicit unavailable value. The graphs on the VPS page are the separate metrics store. See [VPS](vps.md).

`panels environment cleanup` is a dry run until `--apply`. Apply re-reads the inventory and re-proves every target. It can rotate oversized logs, remove expired backup temporaries, and prune verified backups past the three that are kept. It does not kill a process, and it does not remove a worktree, a cache, an environment, or the deployed app.

## Pointers

`src/planner/environments/` and `ops/panels-environments/`. The live service and backup timer are described with [Backups](backups.md) and [Deployment](deployment.md).

_Last verified: 2026-10-30._
