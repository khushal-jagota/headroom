# Deployment

Deployment is how a commit on `main` becomes the app the live host runs. Publication and installation are separate. A push produces a sealed, checked release on disposable hosted machines. Panels downloads that release in the background and checks it, but nothing changes until Khushal presses Update. Then Panels installs exactly those bytes itself. The host never builds code, and a push never installs itself.

```text
staging PR or push → server and web suites
PR into main → candidate packages → native Linux and Apple Silicon boots
                      → previous release → Update → joined machine, file and agent turn
                      → failed trial → previous home restored
merged main tree + passing suites + passing native journeys → publish qualified packages

owner presses Update → drain accepted sends → stop → snapshot if required → select release
                     → start trial → home machine answers → record success → reopen writes
                                   ↳ failed trial → restore data and release → verify previous home
```

The server publishes the installed release and its accepted client versions through one metadata answer. Clients identify their installation and software version. The server retains the last reported version for each device. Older installed manifests without a release number or parts omit those fields.

## What it is made of

The TypeScript Linux builder rebuilds browser assets from source, then removes build tools and unused platform binaries before it seals the archive. The Linux executor uses the same builder. Installation consumes the sealed archive and runs no source build.

- **Publication.** A GitHub workflow on hosted machines. For one commit, one machine builds the Linux app once and seals the archive. It then admits the archive against its runtime contract, and boots and stops it through its real launcher. Another machine builds and proves the Linux executor archive. A native Mac machine builds and proves separate Mac executor and home artifacts. A last machine checks every proof against the build, reads the committed [iOS review](ios-compatibility.md), and writes the receipt. The workflow publishes the checked archives, manifest, and evidence as GitHub Release assets that do not expire. Panels offers only complete publications. Server publications never replace the desktop Release as GitHub’s latest release. The publication workflow requires the suites and the installed upgrade on each platform. It checks that the merged tree passed qualification on the PR into main.
- **The sealed release.** Archives plus a manifest that names the server, the built screens, and the machine code for each system. The manifest records identities from the checked packages. Each machine identity comes from its payload files, executable permissions, links, and fixed dependency inputs. Release provenance stays separate. The signed Darwin launcher and Node distribution are checked external inputs. Their bytes remain part of the machine identity. The server archive holds the TypeScript server with its Node packages and native libraries, the built browser app, and the agent backends' Node packages. The packaged Node runtime starts the home and its installer. Sealed builds use locked Claude Code and Codex packages and record the versions they check. Which packages, state folders, files, and admission commands that means come from the backend drivers' manifests, not from lists in the release tooling. Once sealed, the archive never changes.
- **Machine compatibility.** Before publication succeeds, native checks prove that the oldest supported machine updates through the real channel. They also prove that the previous build leases, runs a turn, and delivers its result to the candidate server. Wire checks protect compatible changes, and the update channel stays at version 1. Independent builds must produce the same machine identity. Retained archives define the supported floor. The [baseline procedure](../ops/machine-release-baselines.md) describes their custody.
- **The live tree.** The service enters the selected release through `app`. Immutable release directories share a filesystem with that link. Data, logs and credentials stay outside the release.
- **The installer.** The independent service runs the installer and Node from the pinned previous release. Its request and journal live outside the release.
- **The attempt record.** One small file beside the live data, outside the database and its backups. It says what one press of Update is doing and how it ended, with the time of each step. The update card and the deployment status that the iOS app shows read it. It is the one file that crosses versions, so every version ignores what it does not know and keeps it when it writes.

Pull requests into main qualify the Mac launcher with ad-hoc signatures on the hosted Mac. The test installs the public predecessor and re-signs its installed launcher copy. It checks the executable code and bundle identifier before it exercises the update. The main release uses the original Developer ID signatures on both packages and checks that their team identifiers match before publication.

## What a release needs from the host

A release brings its own Node packages, so installation never runs a package installer.

Linux supplies the glibc loader and the system libraries listed in the runtime contract. The host supplies credentials and media tools such as `ffprobe`. Credentials live in the host's service environment and agent homes, so replacing the app never replaces them. Each release states its needs in a versioned runtime contract, and Panels checks the host against it before it offers the release. Panels accepts only version 2, the Node release.

An update checks a new release the way a normal installer does: against what the release says about itself, never against what the running version expects. The archive must match the receipt, the unpacked tree must match its own manifest, contract and inventory, its launch files must exist, and the host must meet its contract. The running version does not run release code before the swap. The installer's health check after the swap, with rollback, proves that the release runs. The heavier probe, which loads every server dependency and runs the backend tools, belongs to publication, where it checks a release that the same code built.

Claude, Codex and Antigravity conversations use owner-maintained CLIs outside the release. App installation does not change those versions. SDK dependencies keep their API pins, while CLI qualification checks the protocol that Panels uses instead of an exact version number. These checks cannot guarantee compatibility with every future CLI update. See [Conversations](conversations.md) for the owner update controls.

The platform adapter owns service identity, independent installer launch, and installer source. Linux uses a separate systemd unit. A Mac home uses a separate LaunchAgent in the graphical login session. Both launch the coordinator from the retained release. The same coordinator selects the new version, checks the trial and restores the previous version on failure.

The workflow also keeps the Actions publication for the transition. The deployed updater uses it to install the first release with the new reader. Attempts that already name Actions artifacts retain their original source and expiration.

## How an install happens

**Getting ready.** On its usual one-minute check, the server looks for the newest successful publication from a push to `main`. It orders stable server publications from every page of GitHub Releases by publication time, newest first, with the source revision and proof from `publish-typescript-release.yml` in its own repository. It does not need the original Actions artifacts or run records. For a release it is not running, it streams the archive to disk, checks its fingerprint against the receipt once, and unpacks it beside the live app on the same disk. It checks that the unpacked release names itself as the receipt does and that the host meets the release's runtime contract, and keeps the matching Mac runtime. A release accepts a database at its own schema revision or at an earlier revision on its own step chain (the Python baseline's last revision, or an earlier TypeScript schema step). An older revision, or an unknown, ahead, or divergent one, is refused, and a backup does not change that. Only then does the card show **Update available**. After a failed release check, the screen shows that it could not check for updates. Discovery retries on its next pass. Only one waiting release is kept.

**The press.** One owner action selects the target release. A repeated press for the same active target returns its attempt. Panels closes admission to new sends and gives accepted messages 30 seconds to reach their agents. The update then continues. The restarted home drops undelivered messages from the queue and shows their outcome in the conversation. A running turn uses the ordinary interruption and recovery path.

An update treats running work as a restart does. The service stop ends every agent that the server runs itself (the Chief, the Managers and other conversations), because each group sits inside the service group. systemd bounds that stop: it kills what remains after the unit's stop limit. Startup then gives each interrupted conversation one recovery prompt, and each lost background task its notice ([Conversations](conversations.md)). Tickets run through machine code, including the Tickets of the server's own machine, so their agents keep their processes and deliver results after the home returns. The first start of the release that moved the server's Tickets to machine code stops their old local processes through the process record and gives them the same recovery prompt.

Before that first start, `panels legacy-agents dry-run --database-path <PATH>` reports proven old Panels processes and unproven records. It reads process state without signals, and copies the database with SQLite's online backup from a read-only connection. That copy never writes the database or its WAL. Like any reader, it can set a read mark in the shared `-shm` index. SQLite inspects the private temporary copy, which the command removes afterward.

If the database or WAL changes during the snapshot, the command refuses it. Retry the command after that refusal. An unproven record gives a nonzero exit status and requires review before the change-over.

The trial marker keeps ordinary writes closed. The new server receives fresh credentialed leases on the local executor socket. Until the enrolled machine completes the exchange, the trial health endpoint stays unavailable. The installer checks the candidate SHA and attempt ID before it records success and reopens writes.

The independent installer owns acceptance and rollback under its lifetime lock. Reading update status cannot settle an attempt from its age or the running SHA.

**The installer.** It checks capacity for a required snapshot before stopping the home, takes the snapshot while the database is quiet, selects the candidate, runs its schema steps and starts its trial. On the first conversion from a real app directory it atomically exchanges directory entries to pin the old release. Later selection replaces one symbolic link with a rename.

A new database starts from the release's complete schema seed. A release refuses a database at a revision its own history does not contain.

A Mac home selects the separate home artifact for its operating system and architecture. Its installer checks the exact installation and stops its home and backup jobs before replacement. It starts the backup job again after home health passes. The service definitions and attempt record stay outside the selected app. Release jobs use fixed executable search paths. The first-install command selects one sealed release for the server and the machine code on its own machine. It starts separate services, connects them through the local executor link, and checks server health and an accepted machine connection. Provider logins stay with the machine code. A failed installation stops only its new services and removes only its own files. See the [Mac procedure](../ops/mac-home/installation.md).

Every Mac trial takes that snapshot, including a trial with no schema change. Rollback restores it before the previous home starts. The update's deadline is written before the installer starts, and it allows for the snapshot, the steps, and a possible restore. A release with schema steps is therefore down for longer, by the snapshot time.

## What can be undone

- **Before the stop.** A refusal leaves the running app untouched and the attempt failed with a plain reason. A snapshot that fails leaves the app unchanged, and the installer starts it again.
- **During the trial.** If the candidate fails, the installer stops it, restores the required snapshot, selects the previous release and checks its health. A failed recovery leaves the write barrier in place and records the reason.
- **After commit.** The database is never restored automatically. A later code rollback is a new install and does not rewind data. A previous app refuses to start on a database at a different revision, so going back after a schema step needs that update's backup restored while the service is stopped, and data written after the step is lost.

If the installer is interrupted before acceptance, its service manager can restart it to reconcile the saved attempt under the lock. It restores the previous selection and any required snapshot. After acceptance it preserves the newly committed data. Diagnose failed rollback from the installer journal and retain both releases and the snapshot.

The installer records success before it opens the write barrier. On failure it records the cause and the outcome of restoring the previous version. While the result is uncertain, the screen must not report completion. An expired deadline or a stale browser alone proves neither success nor rollback. The sidebar clears the success notice eight seconds after the home finishes once all machines are current. The result stays in the attempt record. The interruption warning appears only before and during the update.

## The server and each machine

The server keeps its own update attempt. Each registered machine keeps a separate state and retry. A new offer does not erase a failed machine's retry. The server compares machine content identities and skips unchanged builds and their downloads. A known target build requires a matching machine identity. Older publications without machine build identities use the machine’s reported application revision to recognize completion.

The machine channel retains targets for absent machines. Changed machines activate only after the server succeeds. A machine failure leaves its previous runtime available. Linux archive readiness survives a server restart through validation of the retained archive.

The card names the agents that the pending release stops, or says that agents keep running. The server computes this effect again at the press. Until the server and its own machine run separately, the report also includes home work that the server restart stops.

Each machine has a short row with its own progress and retry. The offer describes future changes separately from current targets, so a new offer does not hide a failed machine. Before Update, the card warns when an offered release requires a newer device version. The publication supplies its own compatibility limits. Older publications without those limits supply no device warning. The card uses the [update API](../server/environments/updates/README.md). See [Extra-machine updates](executor.md#updates-and-health).

## The desktop app

The macOS desktop app has its own workflow, separate from the home's publication. On a push to staging or `main`, or a manual run, a GitHub-hosted Mac builds it, signs it with the Developer ID of the Blue Door Labs team and the hardened runtime, and sends it to Apple for notarization. Then it staples Apple's ticket to the app and checks the result with Apple's own tools, so that a Mac opens the download with no warning. The signing certificate and the notarization key are repository secrets, set once ([steps](../ops/desktop/signing.md)). The run keeps the signed zip as the `panels-desktop-mac` artifact for 30 days. The CI run number sets its version, `0.0.<run>`.

A push to `main` that touches the app's paths also publishes the signed shell and updater metadata as a public desktop release. The app downloads shell updates quietly and installs a ready update at quit or after a period without a window. A manual run produces private proof builds and publishes no release.

The screens follow a separate route: the app takes them directly from its selected server. The server names its installed digest and serves the matching files, independently of publication metadata or GitHub Releases. The app verifies each copy and reloads only after the server and installed bridge fit it. See [The desktop app](desktop.md) for screen trust, recovery, and the bundled baseline.

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

_Last verified: 2026-11-24._
