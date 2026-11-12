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
- **Ticket servers.** A Worker's own `panels serve` from its isolated git worktree, with every `PLAN_*` path pointing into that worktree. Headroom never publishes one through the live app. Work Khushal must see is a file in the live Ticket tree or a [preview](previews.md). The Worker stops its server when active work ends. Panels owns the worktree and its later removal, as [Ticket scratch](#ticket-scratch) describes.
- **A local checkout.** One source checkout, one Python environment, one loopback server, and one SQLite file under `data/`. It runs the source, not a sealed release. The install steps are in the repository README.

Live and staging are registered environments. Headroom validates them together: one live environment, distinct ports, and no staging root inside the live tree. Ticket servers and local checkouts are outside that registry. Their isolation is the worktree and its `PLAN_*` paths. [`AGENTS.md`](../AGENTS.md) holds the setup rules agents follow. A second isolated server on one machine is set up with [ops/runbooks/isolated-server.md](../ops/runbooks/isolated-server.md).

## The operating-system boundary

The home uses one platform contract for service identity, app-child launch, process evidence, private sockets, locks, and resource collection. The Linux adapter preserves the existing systemd and kernel operations. Shared installation paths keep explicit configuration and current defaults. Runtime selection separates the operating system, architecture, and home or executor role. Existing archive formats and integrity checks stay the same. A Mac executor archive cannot become a home release.

Only the Linux native home adapter exists. Other POSIX source servers retain their existing foreground lifecycle. Native updates, preview evidence, and resource collection remain explicitly unavailable there. The later Mac adapter must supply login-session launchd services, crash-orphan cleanup, process and preview evidence, metrics, short socket addresses, and an Apple Silicon home runtime. The existing Mac executor remains separate. No Mac home behavior is proved by Linux checks.

Worker supervision is one subsystem for home and executor processes. It uses exact identities and durable custody for bounded shutdown and scoped cleanup proof. Unknown cleanup retains a fence. Linux Workers share the home service resource domain, but the adapter removes its protected OOM score before the backend starts. Separate Worker units and slices no longer apply. A missing observation never proves that all descendants stopped.

The contracts and Linux implementation live in `platform_runtime/`. Domain policy stays in lifecycle, preview, conversation, and environment code.

## How a server is configured

Defaults live in `config.yaml`. Every value there can be overridden by the matching `PLAN_*` environment variable. Test-only switches are environment-only on purpose. Stream heartbeats, database lock waits, and shutdown grace periods use fixed values in code. The background poll interval remains adjustable. Startup does not create an empty logs folder. Status and cleanup can inspect existing logs. A server with dispatch enabled owns the readiness, measure-source schedule, and manager-wake loops behind one machine lock, so a second server on the same lock never becomes a second scheduler.

A server can also be given a default workspace folder for new agent conversations. It links the managed skills into that folder and refuses to start rather than overwrite anything already there. How a conversation picks its folder is in [Conversations](conversations.md).

Agent backends need their CLIs installed and logged in outside Headroom. On the VPS, the sealed release carries the Claude Code and Codex CLIs.

Live and staging launches rebuild the process environment from a fixed allowlist instead of inheriting the caller's. Live passes only named settings and provider keys from its service environment. Staging keeps the user's home, locale, terminal, and path, drops stray `PLAN_*` values, adds validated credentials from its own file, and sets the paths it owns. Credentials are never written into a manifest or printed.

## Host automation

The user units in `ops/panels-environments/` cover the live service, the deployment runner (kept for one release, only for the [update bridge](deployment.md#the-one-release-bridge)), and two daily timers:

- **Backup.** Takes a verified snapshot of live data with the installed app. See [Backups](backups.md).
- **Cleanup.** Runs `panels environment cleanup --apply`: rotates oversized logs, removes expired backup temporaries, prunes old verified backups, and runs the [Ticket file clock](#ticket-scratch). It re-proves every target when it runs and never stops a process or removes an environment or the installed app.

## Ticket scratch

Panels owns each Ticket's workspace. Before repository work starts, it creates the branch and the `worktree/` folder inside that machine's Ticket files folder. The sibling `artifacts/` folder holds documents and evidence. Workers use the same workspace on later turns. They install dependencies, commit, push, open pull requests, and perform approved merges. They do not create worktrees or remove Ticket files.

Codex, Claude, and Hermes use real disk scratch at `~/.pw/<ticket-id>-<six hex characters>` on Linux and macOS. This short path leaves room for Unix sockets. `TMPDIR`, `TMP`, and `TEMP` point there, and the launcher removes it when the backend process ends, including failed startup. A fresh backend folder replaces other scratch folders for the same Ticket. Retirement removes the scratch recorded before that workspace detached, so a later retry preserves fresh backend scratch.

Scratch belongs to one backend process. Panels can remove scratch of a process that is no longer current.

Tools that follow the temporary environment use this scratch. Commands that name `/tmp` or `/var/tmp` still use those host paths. Workers must use `TMPDIR` or a tool's explicit temporary-folder setting. Chief and Managers retain the server's private `/tmp` on the VPS, which shares the host's memory-backed filesystem. Host storage and age cleanup remain operator policy.

The checkout stays private. File access and artifact transfers exclude it, and backups already exclude the files tree. Development checks admit only the managed checkout while they continue to reject storage shared with the installed app. Projects without a repository use an ordinary Ticket workspace.

Panels clears the complete Ticket folder seven days after the Ticket ends. The database keeps the end time and ownership after Ticket deletion. A reopened Ticket no longer qualifies. New activity on a Done Ticket starts a fresh seven-day wait. Grants belong to one physical workspace on one machine. Removal of a server mirror cannot displace the Mac checkout.

Attached checkouts keep their local changes on resume. Cleanup detaches only the exact expired folder before slow deletion. Stop and backlog do not start the clock. Stage text and proposals survive, but links to deleted documents and evidence no longer work. Existing ended Tickets receive a full week from adoption because their historical end dates are unknown.

Before removal, Panels checks that the Ticket is still ended and the work is safe on a remote. It never commits or pushes on a Worker's behalf. Uncommitted files, unpublished commits, or failed Git inspection preserve the folder and produce a report. Legacy scratch outside the Ticket files root remains in its original location for owner review. Worktrees with submodules also remain with an explicit reason. Ignored files, including environment files and local databases, expire with the folder.

Tickets deleted before migration have no reliable end record, so their old folders remain for review. The Mac uses authoritative lifecycle state from the VPS, not absence from a local cache. See [Executor](executor.md).

Renaming the account, moving host state, or changing Tailscale Serve is operator work outside this code.

## Status

`panels environment status` reads the local filesystem and process list without calling the server. The graphs are a separate system, [VPS](vps.md).

## Decisions that shape it

- One Unix user and real provider homes. Separation is by directory and launch environment.
- Live is the installed release, never a checkout.
- Staging and Ticket servers run only while needed.
- Launches rebuild their environment from an allowlist rather than inheriting stray settings.

## Code

- `src/planner/ticket_workspaces/`: local workspace ownership, activity clocks, and safe removal.
- `src/planner/environments/`: the registry, launch environments, status, and cleanup. `logic/validation.py` and `logic/launch_env.py` hold the rules.
- `src/planner/core/config.py`: configuration and `PLAN_*` overrides. `src/planner/skill_sources.py`: the workspace skills link.
- `src/planner/server_lifecycle/`: who may stop a server.
- `ops/panels-environments/`: user units, timers, and the host wrapper.

_Last verified: 2026-11-12._

Named execution destinations and physical workspace identity are separate. A Ticket’s `machine_id` selects home execution or the original executor. `WorkspaceIdentity.machine_id` identifies one local physical data tree. Workspace registration binds its folder to the conversation on that destination. A display edit never rotates either identity.
