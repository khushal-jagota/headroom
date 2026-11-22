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
owner presses Update → installer starts at once, whatever work runs
                                                                                │
installer unit: stop → snapshot if required → swap folders → start → health ── healthy → resume work → done
                                                    └─ not healthy → restore, swap back → resume work → failed
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

The host supplies the glibc loader and a short named list of system libraries, Node 22.18.0 or newer, media tools such as `ffprobe`, and every credential. Credentials live in the host's service environment and agent homes, so replacing the app never replaces them. Each release states its needs in a versioned runtime contract, and Panels checks the host against it before it offers the release. Panels accepts only version 2, the Node release.

An update checks a new release the way a normal installer does: against what the release says about itself, never against what the running version expects. The archive must match the receipt, the unpacked tree must match its own manifest, contract and inventory, its launch files must exist, and the host must meet its contract. The running version does not run release code before the swap. The installer's health check after the swap, with rollback, proves that the release runs. The heavier probe, which loads every server dependency and runs the backend tools, belongs to publication, where it checks a release that the same code built.

Claude, Codex and Antigravity conversations use owner-maintained CLIs outside the release. App installation does not change those versions. SDK dependencies keep their API pins, while CLI qualification checks the protocol that Panels uses instead of an exact version number. These checks cannot guarantee compatibility with every future CLI update. See [Conversations](conversations.md) for the owner update controls.

The platform adapter owns service identity, independent installer launch, and installer source. Linux uses a separate systemd unit. A Mac home uses a separate LaunchAgent in the graphical login session. The copied installer carries its own dependencies and runs on host Node. It keeps the same stop, swap, start, health, and rollback sequence.

## How an install happens

**Getting ready.** On its usual one-minute check, the server looks for the newest successful publication from a push to `main`. It trusts one workflow in its own repository, `publish-typescript-release.yml`. It reads its 20 newest runs, newest first. For a release it is not running, it streams the archive to disk, checks its fingerprint against the receipt once, and unpacks it beside the live app on the same disk. It checks that the unpacked release names itself as the receipt does and that the host meets the release's runtime contract, and keeps the matching Mac runtime. A release accepts a database at its own schema revision or at an earlier revision on its own step chain (the Python baseline's last revision, or an earlier TypeScript schema step). An older revision, or an unknown, ahead, or divergent one, is refused, and a backup does not change that. Only then does the card show **Update available**. A failed download or check is tried again on the next pass and never shows as an error. Only one waiting release is kept.

**The press.** Only the owner can press **Update**, and a press always starts the update. Running work is never asked, stopped, or waited for by the server. The card names the work that the update interrupts, for information. There is no wait for idle, no confirmation, no cancel, and no paused state. The server checks Ticket integrity and the release schema, writes the trial marker, and launches the installer. This takes seconds.

An update treats running work as a restart does. The service stop ends every local agent, because each Worker group sits inside the service group. systemd bounds that stop: it kills what remains after the unit's stop limit. Startup then gives each interrupted conversation one recovery prompt, and each lost background task its notice ([Conversations](conversations.md)). Agents on extra machines keep their processes and deliver results after the home returns. A turn in flight is interrupted and continues from its recovery prompt. A message that waited in memory is lost, as in any restart.

The trial marker keeps the new app closed to writes and new work while the installer can still restore data. The installer removes it at its end, and startup recovers a stale marker only after the installer's ownership ends. The installer runs outside the app, so the home stop cannot kill it.

A frozen home checks installer ownership every two seconds, including before its first restart. A held lock counts as live before its identity text is ready. The restore process has its own limit of 15 minutes, even if the installer dies.

**The installer.** It stops the service that the server runs in, which the server reads from its own systemd cgroup, so a copy of Panels elsewhere can never stop the live one. If the release has pending schema steps, the current app then takes a verified snapshot of the stopped database and records it in the attempt before any rename. No writer remains, so nothing needs to be frozen or joined first. The installer renames the current app aside, renames the waiting release into its place, lets the new release run its pending schema steps on the stopped database, starts the service, and waits for the new app to report its exact commit. Each schema step is one transaction, and the last one must leave the schema exactly as the release's seed describes; a failing step undoes only itself. Steps never run without a backup. The chain holds one step, `ts_0001_open_backend_keys`, which lets five tables store any backend name, such as `antigravity`, not only a fixed list. It also adds the navigation ordering table, which older servers created for themselves at startup, and keeps an existing one and its rows. Server startup never migrates: it accepts only its own seed revision. A healthy answer proves that the new process serves Headroom. Work remains paused until the installer commits success. Startup also refreshes the agents' skill links. Interrupted conversations get one recovery prompt ([Conversations](conversations.md)). On success the installer marks the attempt done and keeps only the newest previous app.

A new database starts from the release's complete schema seed. A release refuses a database at a revision its own history does not contain.

A Mac home selects the separate home artifact for its operating system and architecture. Its installer checks the exact installation and stops its home and backup jobs before replacement. It starts the backup job again after home health passes. The interpreter, installer, job definitions, and attempt record remain outside the app. Release jobs use fixed executable search paths. The first-install command selects one sealed release for the server and the machine code on its own machine. It starts separate services, connects them through the local executor link, and checks server health and an accepted machine connection. Provider logins stay with the machine code. A failed installation stops only its new services and removes only its own files. The Mac requires an external Node for updates. See the [Mac procedure](../ops/mac-home/installation.md).

Every Mac trial takes that snapshot, including a trial with no schema change. Rollback restores it before the previous home starts. The update's deadline is written before the installer starts, and it allows for the snapshot, the steps, and a possible restore. A release with schema steps is therefore down for longer, by the snapshot time.

## What can be undone

- **Before the stop.** A refusal leaves the running app untouched and the attempt failed with a plain reason. A snapshot that fails leaves the app unchanged, and the installer starts it again.
- **After the swap.** If the new app does not prove healthy in time, the installer stops it, restores the database from the backup if one was taken (also when a schema step fails, or the new app fails health after one), renames the previous app back, starts the prior app, and waits for it to prove healthy. The attempt is then failed with a plain reason. A failed recovery says so plainly, never success. If the installer itself is stopped or killed after the swap, the next start of the new app settles it before admission, by the database's schema revision rather than the attempt file. At the new app's revision the steps committed, so the new app stays and the attempt is done. At an earlier revision on its chain, startup restores the backup, renames the previous app back, fails the attempt, and exits so the service starts the previous app.
- **After health.** The database is never restored automatically. A later code rollback is a new install and does not rewind data. A previous app refuses to start on a database at a different revision, so going back after a schema step needs that update's backup restored while the service is stopped, and data written after the step is lost.

Three cases still need a person. A kill after the stop and before the first rename, including during the snapshot, leaves the previous app in place on its untouched database, but nothing starts the service again; start it by hand. A kill between the two folder renames leaves no `app` folder in the installation root, only the hidden `.app-previous-<from_sha>` and `.app-next-<to_sha>` beside `data/` (the SHAs are `from_sha` and `to_sha` in `data/update-attempt.json`). The service then fails at every start, and its journal (`journalctl --user -u panels-typescript.service`) shows the start script unable to enter `app`. Rename `.app-previous-<from_sha>` to `app` in every such case, because the data is restored or untouched before any rename; leave `.app-next-<to_sha>` alone. The next start of the service, by its own retry or by hand, runs the previous app. Third, a Mac home's recovery at startup does not restart the backup, maintenance, and stall-watchdog jobs the installer stopped. Start them with the `launchctl bootstrap` loop in the [Mac procedure](../ops/mac-home/installation.md).

Every update ends as done or failed. A failed update writes its reason to the journal and the card, and sends one push to the owner's devices. A refused press writes its reason to the journal too. If no app returns after a failure, the installer unit's journal still holds the reason, and no push is possible. The installing state carries a deadline. A deadline alone never proves trial health while the saved update gate remains closed. If the installer disappears, the gate retains results and requires recovery. A terminal attempt with a released gate permits another update.

## The card and the Mac

One server answer supplies the card's state and allowed actions: up to date, available, updating, failed, Mac failed, or Mac unreachable. While the connection is lost during the restart, the card keeps saying updating until the deadline. The browser offers a refresh when its loaded app differs from the server, and the server rejects a write from an outdated page.

The publication carries a TypeScript Mac executor from the same source, with its own Node runtime and native dependencies. Its updates use the existing update channel. The separate Mac home package does not change. The card covers the home and its original Mac. Other registered Macs receive their own offers and keep separate reports. The VPS never waits for the Mac. The Mac sees its target from the press and activates it once the VPS install is done. The card stays updating until the Mac reports the new runtime. A Mac that reports a failure shows **Mac failed**; one that says nothing for about a minute and a half after the VPS part shows **Mac unreachable**; it may be asleep or offline. Either way **Try again for the Mac** sends a fresh target for the same release, and a Mac that returns later and reports the new runtime moves the card on by itself. A release without a Mac part, or a host without a Mac, skips it. See [Extra-machine updates](executor.md#updates-and-health).

## The desktop app

The macOS desktop app has its own workflow, separate from the home's publication. On a push to staging or `main`, or a manual run, a GitHub-hosted Mac builds it, signs it with the Developer ID of the Blue Door Labs team and the hardened runtime, and sends it to Apple for notarization. Then it staples Apple's ticket to the app and checks the result with Apple's own tools, so that a Mac opens the download with no warning. The signing certificate and the notarization key are repository secrets, set once ([steps](../ops/desktop/signing.md)). The run keeps the signed zip as the `panels-desktop-mac` artifact for 30 days. The CI run number sets its version, `0.0.<run>`.

A push to `main` that touches the app's paths also publishes the zip as a public GitHub Release, tagged `desktop-v<version>`. The newest one always downloads from <https://github.com/khushal-jagota/headroom/releases/latest/download/Panels-mac-arm64.zip>. The app runs on Apple silicon Macs. A manual run makes a draft Release instead, which is not public, so that the job can be checked before a release. Delete such a draft after the check. Its updates come later.

## The private route

Publication never changes Tailscale Serve. A separate manual workflow restores the private route if it is lost.

## Decisions that shape it

- Build once, prove those bytes, install the same bytes.
- A push publishes. Only the owner's press installs.
- Running work never blocks, pauses, or delays an update. An update is a restart, and restart recovery resumes the work.
- Do the slow work before the press: download, check, and unpack while Headroom keeps working.
- The installer depends on nothing it replaces: host Node, its own copy of its code, and its own service job.
- Data is restorable only until the new app is proven.
- Every attempt ends, and nothing waits for the Mac.

## Development checkouts

Development dependencies and runtime state stay separate from the installed app. `scripts/dev` launches npm without a filesystem safety scan.

The archive digest detects damage after a write. If a write reaches the installed tree, leave it in place and report it. Never clean the installed tree to force an install through.

## Code

- `.github/workflows/publish-typescript-release.yml`: publication. `recover-private-route.yml`: route repair.
- `.github/workflows/desktop.yml`, `desktop/`: the desktop app, its checks and its signed build. `ops/desktop/signing.md`: its Apple secrets.
- `server/environments/release/`: building, sealing, runtime admission, the boot proof, and the receipt.
- `server/environments/updates/provider.ts`: reading publications and streaming downloads. `service.ts`, `routes.ts`: getting ready, the press, and the card's state. `failure_notice.ts`: the owner push for a failed update.
- `server/environments/updates/attempts.ts`: the attempt record. `installation.ts`: the installer's start. `server/environments/updates/installer/`: the standalone installer.
- `server/environments/updates/mac/`: the Mac target and its progress. `updates/darwin/`: Mac home updates.
- `web/src/components/UpdateActions.svelte`, `web/src/lib/updates/`: the card.
- `scripts/dev`: the npm launcher for development.
- `ops/panels-environments/`: user units.

_Last verified: 2026-11-22._
