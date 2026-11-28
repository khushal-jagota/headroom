# Environments

An environment is one running Headroom server with its own state: a database, files, logs, locks, and a loopback port. Headroom has three kinds on the VPS: live, staging, and Ticket servers. A local checkout on any machine is the same shape. This page says which servers exist, what separates them, and who runs what on the host.

A Panels home can run on Linux x86_64 or Apple Silicon. Each platform supports the complete home, including joined machines, attachments, conversations, and updates. The VPS layout below describes one installation.

The environments on this VPS run as one Unix user, the existing `vps` account. They are separated by directories and by the environment they are launched with, not by Linux accounts. Provider logins stay in that user's real Codex, Claude and Antigravity homes. Headroom does not copy them into role accounts.

```text
~/Deployments/Panels/current/{app,data,logs}   live: the installed release, no Git
~/projects/Panels/Panels/                      source; staging state under data/environments/staging
<ticket worktree>/data/                        one Ticket server's private state
<any checkout>/data/                           a local server's state
```

## What each one is

- **Live.** The installed release from [Deployment](deployment.md), run by a systemd user service on loopback port 8767 behind the existing Tailscale Serve route. Only the verified service manager can stop it by signal. Workers never stop the server that hosts them. The interactive `panels` command on the host follows the installed app.
- **Staging.** A foreground server from the source checkout with fake data that persists between runs. It runs only while someone needs it and has no service. `panels environment prepare`, `run`, and `reset` manage it.
- **Ticket servers.** A Worker runs its own server from its isolated git worktree with private runtime state. The TypeScript test server uses the private temporary directory described below. Headroom never publishes one through the live app. Work the owner must see is a file in the live Ticket tree or a [preview](previews.md). The Worker stops its server when active work ends. Panels owns the worktree and its later removal, as [Ticket scratch](#ticket-scratch) describes.
- **A local checkout.** One source checkout with its own Node dependencies, one loopback server, and one SQLite file under `data/`. It runs the source, not a sealed release. The install steps are in the repository README.

Live and staging are registered environments. Headroom validates them together: one live environment, distinct ports, and no staging root inside the live tree. Ticket servers and local checkouts are outside that registry. Their isolation is the checkout and their private `PLAN_*` paths. [`AGENTS.md`](../AGENTS.md) holds the setup rules agents follow. A second isolated server on one machine is set up with [ops/runbooks/isolated-server.md](../ops/runbooks/isolated-server.md).

Each development checkout installs its own Node dependencies with `npm ci`, separate from the installed release. Packaged backend CLIs use the checked-in lockfile.

## The server state folder

The server state is one folder, normally `data/`. It holds the database, its credential key, served files, skill files, and private push and update secrets. Database rows also hold managed skill text. Disk copies of that text can accompany their supporting files.

Settings describe the machine and are written again after a move. The state inventory excludes settings, provider logins, locks, sockets, process records, updates, metrics, caches, transfer uploads, and old backups. Private Ticket worktrees stay outside the served-file inventory. The server archives abandoned transfer uploads before its first transfer service starts, while it holds exclusive runtime ownership. Archives stay in `executor-upload-archive/` inside the server folder, outside served files. A person removes them later by hand.

An explicit `server_root` resolves from the settings file. Server file paths resolve from that folder. An absolute `PLAN_DB_PATH` selects its parent as the root unless `PLAN_SERVER_ROOT` also specifies one. This lets an isolated command select its own state. The database stays directly inside the root so every database-adjacent file owner agrees on the folder.

Full-state checks use the same SHA256 file hashes and exact file-list comparison as ordinary backups. The full check requires a stopped, checkpointed database. The [backup page](backups.md) describes the smaller nightly scope. Installation and recovery procedures live in [the operator guide](../ops/panels-environments/README.md).

## The operating-system boundary

The home uses one platform contract for service identity, app-child launch, process evidence, private sockets, locks, and resource collection. The Linux adapter preserves the existing systemd and kernel operations. Shared installation paths keep explicit configuration and current defaults. Runtime selection separates the operating system, architecture, and home or executor role. Existing archive formats and integrity checks stay the same. A Mac executor archive cannot become a home release.

An Apple Silicon Mac can run a home from source in the owner's login session. Its launchd job owns the supervisor and app process group, so a hard supervisor crash does not leave the app behind. Private short sockets support long installation paths. Separate launchd jobs run verified backups, proof-bound maintenance, and stall alarms through the existing commands. Logout stops the home, and sleep makes it unreachable. A sealed release uses the same custody. Its app stays separate from data, logs, and Ticket files. See the [Mac installation procedures](../ops/mac-home/installation.md).

A home does not link skills in the provider account folders. Machine code links them. A disposable home can use private provider folders and omit the shared account command through the installer. At startup, an installed home removes only its own old links from those folders, and it keeps every other entry.

The home links workspace skills and removes its old account links on an awaited worker thread, so file removal does not block the event loop. Startup waits for this work before normal work starts.

Eligible Ticket folders use directory descriptors for safe removal on Linux and Mac. Replacement directories and symlinks cannot redirect recursive removal. Unknown ownership, unpublished work, or uncertain process retirement retains the folder. Retirement retries keep the same checks and recorded scratch snapshot.

A release-installed server carries its own Node 22 runtime on Linux and Apple Silicon. Its first installation and independent update LaunchAgent use native TypeScript. The installer stays outside the app that it replaces. Its jobs run only during the graphical login session.

A failed health trial returns to the previous app and snapshot record. This rollback can discard later record writes and does not prove preservation of active work. Native process evidence supports local previews and metrics. The Mac executor remains separate. Linux checks do not prove Mac behavior.

Mac home publication requires a hosted Apple Silicon build and proof of the sealed archive. The proof admits its native dependencies, starts the real home, checks readiness, and confirms a clean stop. Publication binds the uploaded archive and proof to the same source revision and workflow attempt. The home archive contains the server, provider packages, web assets, and installation recipe. The archive supplies Node. The Mac executor archive has a separate identity and cannot substitute for it.

Process supervision covers every local conversation on the home, Chief, Manager and generic conversations as well as Workers, and the Workers on executors. It uses exact identities and durable custody for bounded shutdown and scoped cleanup proof. Unknown cleanup retains a fence. On Linux, delegated cgroup v2 child groups keep each supervised process and its descendants inside a separate cleanup boundary; hosts without delegation retain a cleanup fence. Everything shares the home service resource domain, and only Workers lower their priority and give up the protected OOM score before the backend starts. Separate Worker units and slices do not apply. [Conversations](conversations.md#worker-processes) owns the details.

The contracts and OS adapters live in `platform_runtime/`. Domain policy stays in lifecycle, preview, conversation, and environment code.

## How a server is configured

Defaults live in `config.yaml`. Every value there can be overridden by the matching `PLAN_*` environment variable. Test-only switches are environment-only on purpose. Stream heartbeats, database lock waits, and shutdown grace periods use fixed values in code. The background poll interval remains adjustable. Startup does not create an empty logs folder. Status and cleanup can inspect existing logs. A server with dispatch enabled owns the readiness, measure-source schedule, and manager-wake loops behind one machine lock, so a second server on the same lock never becomes a second scheduler.

A server can record a default workspace path for new conversations. Machine code checks that path on the execution machine. How a conversation picks its folder is in [Conversations](conversations.md).

Agent backends need their CLIs installed and logged in outside Headroom. On the VPS, the sealed release carries the Claude Code and Codex CLIs; Antigravity runs the host's own `agy`.

Live and staging launches rebuild the process environment from a fixed allowlist instead of inheriting the caller's. Each backend driver's manifest contributes its own variables, home folder, and skills folder to that list. Live passes only named settings and provider keys from its service environment. Staging keeps the user's home, locale, terminal, and path, drops stray `PLAN_*` values, adds validated credentials from its own file, and sets the paths it owns. Credentials are never written into a manifest or printed.

## Host automation

The user units in `ops/panels-environments/` cover the live service and two daily timers:

- **Backup.** Takes a verified snapshot of live data with the installed app. See [Backups](backups.md).
- **Cleanup.** Runs `panels environment cleanup --apply`: rotates oversized Linux logs, truncates live Mac logs in place, removes expired backup temporaries, prunes old verified backups, and runs the [Ticket file clock](#ticket-scratch). It re-proves every target when it runs and never stops a process or removes an environment or the installed app.

## TypeScript test server

Run the test server from the Ticket checkout. Its state must stay in the Worker's private `TMPDIR`, outside the deployment tree. The TypeScript test database check rejects deployment paths. The production smoke also needs its `--state-root` outside that tree.

From the checkout's `server/` directory, create a private state directory and a fresh, bootstrapped database. The database command refuses an existing file. Choose an available loopback port before the last command. If another process takes the port, choose another and retry.

```sh
TEST_SERVER_ROOT=$(mktemp -d "$TMPDIR/panels-server.XXXXXX")
mkdir -p "$TEST_SERVER_ROOT/home" "$TEST_SERVER_ROOT/workspace" "$TEST_SERVER_ROOT/logs" "$TEST_SERVER_ROOT/backups"
printf '{}\n' > "$TEST_SERVER_ROOT/config.yaml"
env -i PATH="$PATH" TMPDIR="$TMPDIR" PLAN_TEST_MODE=1 node installation/database_main.ts "$TEST_SERVER_ROOT/planning.db"
TEST_SERVER_PORT=18739
env -i PATH="$PATH" HOME="$TEST_SERVER_ROOT/home" TMPDIR="$TMPDIR" \
  PLAN_CONFIG_PATH="$TEST_SERVER_ROOT/config.yaml" \
  PLAN_DB_PATH="$TEST_SERVER_ROOT/planning.db" \
  PLAN_LOGS_DIR="$TEST_SERVER_ROOT/logs" PLAN_BACKUP_DIR="$TEST_SERVER_ROOT/backups" \
  PLAN_WORKSPACE_ROOT="$TEST_SERVER_ROOT/workspace" \
  PLAN_DISPATCHER_LOCK_PATH="$TEST_SERVER_ROOT/dispatcher.lock" \
  PLAN_SERVER_CONTROL_SOCKET="$TEST_SERVER_ROOT/control.sock" \
  PLAN_PORT="$TEST_SERVER_PORT" PLAN_DISPATCH_ENABLED=false \
  PLAN_NOTIFICATIONS_ENABLED=false npm run test-server
```

The command runs the normal server in test mode with an empty environment apart from these values. Stop it when the work ends. The Worker runtime owns the temporary directory and removes it when the backend process ends. The production smoke uses a populated fixture, the production server and private home machine code connected through its local socket; this test server uses a fresh database and the test-mode composition.

## Ticket scratch

Panels owns each Ticket's workspace. A Project can store a Git repository URL and optional default branch. A Ticket without a Project, or in a Project with no repository URL, gets a plain work folder. Before repository work starts, it creates the branch and the `worktree/` folder inside that machine's Ticket files folder. The sibling `artifacts/` folder holds documents and evidence. The shared home setup also gives the Ticket folder a `logs/` folder, where the Worker notes problems, and a CLAUDE.md and an AGENTS.md that explain it. Managers and the Chief get the same home setup (`server/agent_homes/`), each with their own short router file and a `rules/` folder. Panels creates each of these only when it is missing, so the agent's own edits last across restarts. TypeScript Workers start in the Ticket folder on the home and Mac. Their checkout stays inside `worktree/`. Workers use the same workspace on later turns. They install dependencies, commit, push, open pull requests, and perform approved merges. They do not create worktrees or remove Ticket files.

New Ticket checkouts on every machine are independent partial clones of the Project's stored repository URL and default branch (or the remote's HEAD branch when none is set). Preparation stops if the remote cannot be cloned. Each machine keeps one bare mirror per Project repository in its cache folder (`<data root>/cache/git-mirrors` on the home, `<executor root>/cache/git-mirrors` on executors). Cold and existing mirrors both receive the ordinary 30-minute fetch allowance. Preparation fetches the mirror under a lock and clones with `--reference <mirror> --dissociate`, so the checkout still owns its objects and only the mirror avoids the network. If the mirror cannot be made, the clone uses the remote alone. Workspace cleanup removes mirrors unused for 30 days and orphaned partial mirrors, and skips any mirror that is locked. Older linked worktrees stay attached to their original repository until their Tickets end and cleanup removes them.

On macOS, managed Ticket Workers receive an Xcode configuration that enables a shared compilation cache beneath the executor root. The cache survives Ticket removal. Each checkout keeps its own DerivedData and build products, so concurrent builds share cached compilation results without sharing writable build directories. The configuration preserves the native account's existing Xcode configuration. Hosted Xcode 26.6 checks prove reuse of SDK compilation results across checkouts. Project-source cache keys still contain private build paths, so this does not prove project-source reuse or Wren's Xcode 27.1 build times.

Codex and Claude use real disk scratch at `~/.pw/<ticket-id>-<six hex characters>` on Linux and macOS. This short path leaves room for Unix sockets. `TMPDIR`, `TMP`, and `TEMP` point there, and the launcher removes it when the backend process ends, including failed startup. A fresh backend folder replaces other scratch folders for the same Ticket. If another process keeps writing into a folder, removal gives up after 1.5 seconds and logs a warning that names what remained. That folder stays until the next start or retirement of its Ticket. It never fails a stop or a start. Retirement removes the scratch recorded before that workspace detached, so a later retry preserves fresh backend scratch.

Scratch belongs to one backend process. Panels can remove scratch of a process that is no longer current. Scratch preparation and removal run on awaited worker threads. A cancelled start waits for preparation and removes its exact new directory before it releases ownership.

Tools that follow the temporary environment use this scratch. Commands that name `/tmp` or `/var/tmp` still use those host paths. Workers must use `TMPDIR` or a tool's explicit temporary-folder setting. Chief and Managers use the temporary folder of their execution machine. Host storage and age cleanup remain operator policy.

The checkout stays private. File access and artifact transfers exclude it, and backups already exclude the files tree. Development checks admit only the managed checkout while they continue to reject storage shared with the installed app. Projects without a repository use an ordinary Ticket workspace.

Panels clears a Ticket checkout when the Ticket ends, once it can prove the work is on origin and the Worker's custody is closed. Git copies inside build caches (`node_modules`, `.venv`, Xcode's `DerivedData` and SwiftPM's `.build`) are not work and are not checked. Panels relies on its Worker custody proof and does not inspect unrelated processes. Artifacts inside the checkout leave with it. The rest of the Ticket folder, including its separate artifacts folder, remains for seven days after the Ticket ends. Then Panels removes everything in it except `logs/`, so the Ticket folder and its logs stay.

Delete just deletes. Once a deleted Ticket's Worker custody is closed, the next cleanup pass removes its whole folder, checkout and artifacts included, except `logs/`. There is no origin proof and no seven-day wait, so unpushed or uncommitted work goes with it. The folder waits only while it holds a checkout that names another Ticket, or its repository owns a worktree outside the folder. The database keeps the end time after Ticket deletion, so a deleted Ticket reads as deleted, not as unknown. A reopened Ticket no longer qualifies. New activity on a Done Ticket starts a fresh seven-day wait for the remaining folder. Grants belong to one physical workspace on one machine. Removal of a server mirror cannot displace the Mac checkout.

On the server's own machine, machine code and the server share the Ticket files folder. Machine code considers only folders that carry a workspace marker. The server answers each Ticket in a cleanup request on its own. Another machine's live Ticket is left out, and only its folder waits. Retirement accepts a folder whose physical data tree holds a workspace that machine code of the same machine registered, so folders the server registered before the switch to machine code are retired too. Retirement waits until the server's old process records for that folder are retired. The same ownership rule covers the Chief and the Managers. The server does not inspect or remove their execution folders.

Attached checkouts keep their local changes on resume. Server and CLI cleanup share exclusive access to each cleanup pass. Cleanup adopts existing artifact folders without recreating absent paths. It detaches the checkout or expired folder before slow deletion. File preparation precedes the short database transaction that authorizes detach. Stop and backlog do not start the clock. Stage text and proposals survive, but links to deleted documents and evidence no longer work. Existing ended Tickets keep their remaining folders for a full week from adoption because their historical end dates are unknown.

Before removal of an ended Ticket's folder, Panels checks that the Ticket is still ended and the work is safe on origin. It never commits or pushes on a Worker's behalf. Uncommitted files, unpublished commits, or failed Git or Worker custody proof preserve the checkout and produce a report. Legacy scratch outside the Ticket files root remains in its original location for owner review. Worktrees with submodules also remain with an explicit reason. Ignored files inside a checkout leave with the checkout.

Tickets deleted before migration have no reliable end record, so their old folders remain for review. The Mac uses authoritative lifecycle state from the VPS, not absence from a local cache. See [Executor](executor.md).

A move of the server to another machine is one owner action ([Moving the server](executor.md#moving-the-server)). Renaming the account or changing Tailscale Serve is operator work outside this code.

## Status

`panels environment status` reads the local filesystem and process list without calling the server. The graphs are a separate system, [VPS](vps.md).

## Decisions that shape it

- One Unix user and real provider homes. Separation is by directory and launch environment.
- Live is the installed release, never a checkout.
- Staging and Ticket servers run only while needed.
- Launches rebuild their environment from an allowlist rather than inheriting stray settings.

## Code

- `server/ticket_workspaces/`: workspace records and shared machine-side filesystem operations.
- `server/environments/registry/`: the registry, its rules, and launch environments. `server/environments/vps_status/`: status and cleanup.
- `server/core/config.ts`: configuration and `PLAN_*` overrides. Machine workspace paths remain values until machine code uses them.
- `server/lifecycle/`: server lifetime. `server/executor/mac/skills/`: machine-owned provider skill publication.
- `server/installation/` and `server/environments/release/mac_home*.ts`: native first-home installation and sealed Mac home publication.
- `ops/panels-environments/`: user units, timers, and the host wrapper.

Named execution destinations and physical workspace identity are separate. A Ticket’s `machine_id` selects home execution or the original executor. `WorkspaceIdentity.machine_id` identifies one local physical data tree. Workspace registration binds its folder to the conversation on that destination. A display edit never rotates either identity.

_Last verified: 2026-11-22._
