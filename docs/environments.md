# Environments

An environment is one running Headroom server with its own state: a database, files, logs, locks, and a loopback port. Headroom has three kinds on the VPS: live, staging, and Ticket servers. A local checkout on any machine is the same shape. This page says which servers exist, what separates them, and who runs what on the host.

All of them run as one Unix user, the existing `vps` account. They are separated by directories and by the environment they are launched with, not by Linux accounts. Provider logins stay in that user's real Hermes, Codex, and Claude homes. Headroom does not copy them into role accounts.

```text
~/Deployments/Panels/current/{app,data,logs}   live: the installed release, no Git
~/projects/Panels/Panels/                      source; staging state under data/environments/staging
<ticket worktree>/data/                        one Ticket server's private state
<any checkout>/data/                           a local server's state
```

## What each one is

- **Live.** The installed release from [Deployment](deployment.md), run by a systemd user service on loopback port 8767 behind the existing Tailscale Serve route. Only the verified service manager can stop it by signal. Workers never stop the server that hosts them. The interactive `panels` command on the host follows the installed app.
- **Staging.** A foreground server from the source checkout with fake data that persists between runs. It runs only while someone needs it and has no service. `panels environment prepare`, `run`, and `reset` manage it.
- **Ticket servers.** A Worker's own `panels serve` from its isolated git worktree, with every `PLAN_*` path pointing into that worktree. Headroom never publishes one through the live app. Work Khushal must see is a file in the live Ticket tree or a [preview](previews.md). The Worker stops its server when active work ends and removes its state at Consequences.
- **A local checkout.** One source checkout, one Python environment, one loopback server, and one SQLite file under `data/`. It runs the source, not a sealed release. The install steps are in the repository README.

Live and staging are registered environments. Headroom validates them together: one live environment, distinct ports, and no staging root inside the live tree. Ticket servers and local checkouts are outside that registry. Their isolation is the worktree and its `PLAN_*` paths. [`AGENTS.md`](../AGENTS.md) holds the setup rules agents follow. A second isolated server on one machine is set up with [ops/runbooks/isolated-server.md](../ops/runbooks/isolated-server.md).

## How a server is configured

Defaults live in `config.yaml`. Every value there can be overridden by the matching `PLAN_*` environment variable. Test-only switches are environment-only on purpose. Stream heartbeats, database lock waits, and shutdown grace periods use fixed values in code. The background poll interval remains adjustable. Startup does not create an empty logs folder. Status and cleanup can inspect existing logs. A server with dispatch enabled owns the readiness, measure-source schedule, and manager-wake loops behind one machine lock, so a second server on the same lock never becomes a second scheduler.

A server can also be given a default workspace folder for new agent conversations. It links the managed skills into that folder and refuses to start rather than overwrite anything already there. How a conversation picks its folder is in [Conversations](conversations.md).

Agent backends need their CLIs installed and logged in outside Headroom. On the VPS, the sealed release carries the Claude Code and Codex CLIs.

Live and staging launches rebuild the process environment from a fixed allowlist instead of inheriting the caller's. Live passes only named settings and provider keys from its service environment. Staging keeps the user's home, locale, terminal, and path, drops stray `PLAN_*` values, adds validated credentials from its own file, and sets the paths it owns. Credentials are never written into a manifest or printed.

## Host automation

The user units in `ops/panels-environments/` cover the live service, the deployment runner, and two daily timers:

- **Backup.** Takes a verified snapshot of live data with the installed app. See [Backups](backups.md).
- **Cleanup.** Runs `panels environment cleanup --apply`: rotates oversized logs, removes expired backup temporaries, and prunes old verified backups. It re-proves every target when it runs and never stops a process or removes a worktree, environment, or the installed app.

Renaming the account, moving host state, or changing Tailscale Serve is operator work outside this code.

## Status

`panels environment status` reads the local filesystem and process list without calling the server. The browser's smaller summary carries the installed commit, the last deployment outcome, CPU, memory, disk, and the age of the latest verified backup. A missing value is reported as unavailable. The graphs are a separate system, [VPS](vps.md).

## Decisions that shape it

- One Unix user and real provider homes. Separation is by directory and launch environment.
- Live is the installed release, never a checkout.
- Staging and Ticket servers run only while needed.
- Launches rebuild their environment from an allowlist rather than inheriting stray settings.

## Code

- `src/planner/environments/`: the registry, launch environments, status, and cleanup. `logic/validation.py` and `logic/launch_env.py` hold the rules.
- `src/planner/core/config.py`: configuration and `PLAN_*` overrides. `src/planner/skill_sources.py`: the workspace skills link.
- `src/planner/server_lifecycle/`: who may stop a server.
- `ops/panels-environments/`: user units, timers, and the host wrapper.

_Last verified: 2026-11-04._
