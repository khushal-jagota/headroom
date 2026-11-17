# Deployment

Deployment is how a commit on `main` becomes the app the live host runs. Publication and installation are separate. A push produces a sealed, checked release on disposable hosted machines. Panels downloads that release in the background and checks it, but nothing changes until Khushal presses Update. Then Panels installs exactly those bytes itself. The host never builds code, and a push never installs itself.

```text
push to main ─┬→ Linux build once → seal → admit → boot and stop ───────────────┐
              ├→ Linux executor build → seal → proof ───────────────────────────┤
              └→ native Mac build → sealed runtime and home → native proof ─────┤
                                           all passed + iOS review → receipt
                                                                                │
server: newest release → download to disk → check once → unpack beside app → "Update available"
                                                                                │
owner presses Update → close new starts → save work → freeze receipts → snapshot if required
                                                                                │
installer unit: stop → swap folders → start → health ── healthy → done
                                                    └─ not healthy → swap back, restore → failed
```

## What it is made of

The TypeScript Linux builder rebuilds browser assets from source, then removes build tools and unused platform binaries before it seals the archive. The Linux executor uses the same builder. Installation consumes the sealed archive and runs no source build.

- **Publication.** A GitHub workflow on hosted machines. For one commit, one machine builds the Linux app once and seals the archive. It then admits the archive against its runtime contract, and boots and stops it through its real launcher. Another machine builds and proves the Linux executor archive. A native Mac machine builds and proves separate Mac executor and home artifacts. A last machine checks every proof against the build, reads the committed [iOS review](ios-compatibility.md), and writes the receipt. Panels offers a release only when the whole run passed. The test suites run in their own workflows on `staging`.
- **The sealed release.** An archive plus a receipt. It holds the TypeScript server with its Node packages and native libraries, the built browser app, and the agent backends' Node packages. Host Node runs it. Each build resolves current Claude Code and Codex CLIs and records the versions it checks. Which packages, state folders, files, and admission commands that means come from the backend drivers' manifests, not from lists in the release tooling. Once sealed, the archive never changes.
- **The live tree.** `~/Deployments/Panels/current/` with `app`, `data`, and `logs`. An install changes only `app`. The next release waits beside it, unpacked, and the previous one stays beside it after an install. See [Environments](environments.md).
- **The installer.** One small TypeScript program. Before it runs, Panels copies it and its dependencies out of the release. It runs on host Node outside every app, so the app it replaces cannot break it.
- **The attempt record.** One small file beside the live data, outside the database and its backups. It says what one press of Update is doing and how it ended, with the time of each step. The update card and the deployment status that the iOS app shows read it. It is the one file that crosses versions, so every version ignores what it does not know and keeps it when it writes.

## What a release needs from the host

A release brings its own Node packages, so installation never runs a package installer.

The host supplies the glibc loader and a short named list of system libraries, Node 22.18.0 or newer, Hermes, media tools such as `ffprobe`, and every credential. Credentials live in the host's service environment and agent homes, so replacing the app never replaces them. Each release states its needs in a versioned runtime contract, and Panels checks it against the host before it offers the release. Version 1 is the older Python release with its private Python runtime. Version 2 is the Node release. Panels admits it with its own probe source on host Node, never a script or entrypoint from the release. The probe resolves every server dependency inside the release and exercises SQLite and Koffi. Libraries that host Node loaded before any release code ran are its trusted baseline. Every later library must come from the release or be a declared glibc library.

Actual Claude and Codex conversations use owner-maintained CLIs outside the release. App installation does not change those versions. SDK dependencies keep their API pins, while CLI qualification checks the protocol that Panels uses instead of an exact version number. These checks cannot guarantee compatibility with every future CLI update. See [Conversations](conversations.md) for the owner update controls.

The platform adapter owns service identity, independent installer launch, and installer source. Linux uses a separate systemd unit. A Mac home uses a separate LaunchAgent in the graphical login session. The copied installer carries its own dependencies and runs on host Node. It keeps the same stop, swap, start, health, and rollback sequence.

The update barrier joins confirmed child retirement, saved results, and the snapshot before the installer stops the service. A queue drain alone does not prove process retirement.

## How an install happens

**Getting ready.** On its usual one-minute check, the server looks for the newest successful publication from a push to `main`. It trusts two workflows in its own repository, and each can publish only one contract version: `publish-typescript-release.yml` publishes version 2, and the removed Python workflow `deploy.yml` published version 1. It reads the 20 newest runs of each, newest first, and a workflow that GitHub does not have yet counts as having no runs. For a release it is not running, it streams the archive to disk, checks its fingerprint against the receipt once, and unpacks it beside the live app on the same disk. It checks that the unpacked release names itself as the receipt does and that the host meets its runtime contract, and keeps the matching Mac runtime. A version 2 release accepts a database at its own schema revision or at an earlier revision on its own step chain (the Python baseline's last revision, or an earlier TypeScript schema step). An older Python revision, or an unknown, ahead, or divergent one, is refused, and a backup does not change that. Only then does the card show **Update available**. A failed download or check is tried again on the next pass and never shows as an error. Only one waiting release is kept.

**The press.** Only the owner can choose **Update now** or **When work finishes**. Both choices pause new starts. Running Workers can still save results during the wait. Update now names work and confirms child retirement before the snapshot. After five minutes, existing detail explains the delay. Existing controls let the owner wait, cancel, or choose Update now.

Cancel reopens admission, including after an uncertain stop. An uncertain stop still blocks installation until another check proves retirement. Time expiry never forces a stop. Reads and health checks remain available.

The home checks Ticket integrity and the release schema. The final barrier closes writes and remote result acceptance, then joins accepted saves before the snapshot. Extra machines retain later output in their outboxes. The gate stays closed while an installer or its database-restore child can restore data. Terminal exits release it, and startup recovers stale barriers only after that ownership ends. The installer runs outside the app, so the home stop cannot kill it.

A frozen home checks installer ownership every two seconds, including before its first restart. Active local preparation and a bounded launch handoff remain protected. A held lock counts as live before its identity text is ready. The restore process has its own limit of 15 minutes, even if the installer dies.

Proposal retries release admission during their wait. Once the final barrier closes writes, the next retry refuses the proposal without a write. Activity reads use worker threads so that health checks remain responsive.

**The installer.** It stops the service that the server runs in, which the server reads from its own systemd cgroup, so a copy of Panels elsewhere can never stop the live one. It renames the current app aside, renames the waiting release into its place, lets the new release run its pending schema steps on the stopped database, starts the service, and waits for the new app to report its exact commit. Each schema step is one transaction, and the last one must leave the schema exactly as the release's seed describes; a failing step undoes only itself. Steps never run without a backup. The chain holds one step, `ts_0001_open_backend_keys`, which lets five tables store any backend name, not only Hermes, Codex, or Claude. A Python version 1 release has no steps and still upgrades through Alembic. TypeScript server startup never migrates: it accepts only its own seed revision. A healthy answer proves that the new process serves Headroom. Work remains paused until the installer commits success. Startup also refreshes the agents' skill links. Interrupted conversations get one recovery prompt ([Conversations](conversations.md)). On success the installer marks the attempt done and keeps only the newest previous app.

A new database starts from the release's complete schema seed. A release refuses a database at a revision its own history does not contain.

A Mac home selects the separate home artifact for its operating system and architecture. Its installer checks the exact installation and stops its home and backup jobs before replacement. It starts the backup job again after home health passes. The interpreter, installer, job definitions, and attempt record remain outside the app. Release jobs use fixed executable search paths. The first-install command selects and checks the sealed release, refuses missing host prerequisites, starts the new service, and checks its exact release health. The Mac requires an external Node for updates. See the [Mac procedure](../ops/mac-home/installation.md).

Every Mac trial takes a verified snapshot before the installer starts, including a trial with no schema change. Rollback restores that snapshot before the previous home starts. Linux takes a verified backup before an update that changes the schema through Alembic, or when a TypeScript release has pending schema steps. For a TypeScript release the update's deadline is written before that backup starts, and it allows for the backup, the steps, and a possible restore. Meanwhile the card says "Backing up the database first (about N min)". Both platforms require confirmed retirement and saved results before this snapshot.

## What can be undone

- **Before the stop.** A refusal leaves the running app untouched and the attempt failed with a plain reason.
- **After the swap.** If the new app does not prove healthy in time, the installer stops it, restores the database from the backup if one was taken (also when a schema step fails, or the new app fails health after one), renames the previous app back, starts the prior app, and waits for it to prove healthy. The attempt is then failed with a plain reason. A failed recovery says so plainly, never success. If the installer itself is stopped or killed after the swap, the next start of the new app settles it before admission, by the database's schema revision rather than the attempt file. At the new app's revision the steps committed, so the new app stays and the attempt is done. At an earlier revision on its chain, startup restores the backup, renames the previous app back, fails the attempt, and exits so the service starts the previous app.
- **After health.** The database is never restored automatically. A later code rollback is a new install and does not rewind data. A previous TypeScript app refuses to start on a database at a different revision, so going back after a schema step needs that update's backup restored while the service is stopped, and data written after the step is lost. A Python server needs the same backup, because Alembic does not know the new revision.

Two cases still need a person. A kill between the two folder renames leaves no `app` folder in the installation root, only the hidden `.app-previous-<from_sha>` and `.app-next-<to_sha>` beside `data/` (the SHAs are `from_sha` and `to_sha` in `data/update-attempt.json`). The service then fails at every start, and its journal (`journalctl --user -u panels-typescript.service`) shows the start script unable to enter `app`. Rename `.app-previous-<from_sha>` to `app` in every such case, because the data is restored or untouched before any rename; leave `.app-next-<to_sha>` alone. The next start of the service, by its own retry or by hand, runs the previous app. Second, a Mac home's recovery at startup does not restart the backup, maintenance, and stall-watchdog jobs the installer stopped. Start them with the `launchctl bootstrap` loop in the [Mac procedure](../ops/mac-home/installation.md).

The installing state carries a deadline. A deadline alone never proves trial health while the saved update gate remains closed. If the installer disappears, the gate retains results and requires recovery. A terminal attempt with a released gate permits another update.

## The card and the Mac

One server answer supplies the card's state and allowed actions: up to date, available, waiting, updating, failed, Mac failed, or Mac unreachable. While the connection is lost during the restart, the card keeps saying updating until the deadline. The browser offers a refresh when its loaded app differs from the server, and the server rejects a write from an outdated page.

The publication carries a TypeScript Mac executor from the same source, with its own Node runtime and native dependencies. The first switch from the Python executor requires a manual installation by the owner. Later TypeScript runtime updates use the existing update channel. The separate Mac home package does not change. The card covers the home and its original Mac. Other registered Macs receive their own offers and keep separate reports. The VPS never waits for the Mac. The Mac sees its target from the moment work pauses and activates it once the VPS install is done. The card stays updating until the Mac reports the new runtime. A Mac that reports a failure shows **Mac failed**; one that says nothing for about a minute and a half after the VPS part shows **Mac unreachable**; it may be asleep or offline. Either way **Try again for the Mac** sends a fresh target for the same release, and a Mac that returns later and reports the new runtime moves the card on by itself. A release without a Mac part, or a host without a Mac, skips it. See [Extra-machine updates](executor.md#updates-and-health).

## The switch to mailboxes

The first release in which the [Mac executor](executor.md) works through mailboxes (protocol 22) is one switch-over. The old and new Mac links never run side by side.

1. **The order.** The VPS updates first, and the Mac follows by itself. One press covers both. **Update now** stops running Mac agents once; **When work finishes** waits for them. The schema changes, so Panels takes the backup before the install. The new VPS refuses the old Mac's link with "Mac needs update", but its update channel still serves it. The Mac installs its new runtime, which leases at once, and its report moves the card on. The Mac never updates before the VPS.
2. **The watchdog.** The release ships the stall watchdog's unit and timer, but nothing starts them. Khushal installs and enables the timer once the release is current, with the [install lines](../ops/panels-environments/README.md#stall-watchdog). Until then, stalls are recorded but no alarm goes out. Native push also needs the `PLAN_APNS_*` inputs in that unit's environment.
3. **The LaunchAgent.** An update replaces the Mac's runtime but not its LaunchAgent. Khushal renders and loads it again once, by the [runbook](../ops/mac-executor/installation.md#render-and-start-one-launchagent), so macOS schedules the executor as foreground work and does not defer its lease. Loading it restarts the executor, so do it while no Mac agent runs.
4. **The check.** The Mac shows as present, its health shows a recent lease, and a Mac Ticket starts.

**Rollback** means the previous release plus the pre-deploy database backup. A new migration drops the old Mac connection tables, so the previous release refuses the upgraded database. Restore the backup while the service is stopped ([Backups](backups.md)). The update gate prevents result acceptance during the trial, so rollback retains every result accepted before its snapshot. The Mac keeps its previous runtime beside the new one. The new runtime cannot lease from the old server, so the Mac goes back by the [runbook's](../ops/mac-executor/installation.md#deferred-update-and-failed-installation) recovery steps. The previous runtime opens the same outbox and reads the new runtime's handoff record. It ignores the command journal.

## The private route

Publication never changes Tailscale Serve. A separate manual workflow restores the private route if it is lost.

## Decisions that shape it

- Build once, prove those bytes, install the same bytes.
- A push publishes. Only the owner's press installs.
- Do the slow work before the press: download, check, and unpack while Headroom keeps working.
- The installer depends on nothing it replaces: host Node, its own copy of its code, and its own service job.
- Data is restorable only until the new app is proven.
- Every attempt ends, and nothing waits for the Mac.

## Development checkouts

Development dependencies and runtime state stay separate from the installed app. `scripts/dev` launches setup and development tools without a filesystem safety scan.

The remaining Python tests, for the Mac rollback runtime, replace inherited runtime paths with private scratch storage before application imports. They refuse remaining deployment paths and live server URLs. Each process gets separate storage, and server fixtures select their own local server. An explicit test URL prevents the CLI from falling back to the live server.

The archive digest detects damage after a write. If a write reaches the installed tree, leave it in place and report it. Never clean the installed tree to force an install through.

## Code

- `.github/workflows/publish-typescript-release.yml`: publication. `recover-private-route.yml`: route repair.
- `server/environments/release/`: building, sealing, runtime admission, the boot proof, and the receipt.
- `server/environments/updates/provider.ts`: reading publications and streaming downloads. `service.ts`, `routes.ts`: getting ready, the press, and the card's state.
- `server/environments/updates/attempts.ts`: the attempt record. `installation.ts`: the installer's start. `server/environments/updates/installer/`: the standalone installer.
- `server/environments/updates/mac/`: the Mac target and its progress. `updates/darwin/`: Mac home updates.
- `web/src/components/UpdateActions.svelte`, `web/src/lib/updates/`: the card.
- `scripts/dev`, `scripts/dev_launcher.py`: setup and development commands.
- `scripts/test_runtime/`: private runtime paths and early deployment refusal for Python tests.
- `ops/panels-environments/`: user units.

_Last verified: 2026-11-16._
