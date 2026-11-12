# Deployment

Deployment is how a commit on `main` becomes the app the live host runs. Publication and installation are separate. A push produces a sealed, checked release on disposable hosted machines. Panels downloads that release in the background and checks it, but nothing changes until Khushal presses Update. Then Panels installs exactly those bytes itself. The host never builds code, and a push never installs itself.

```text
push to main ─┬→ suites (unit, integration, e2e, browser) ──────────────────────┐
              ├→ static checks (ruff, mypy, frontend, vitest) ──────────────────┤
              ├→ build once → seal → prove on clean root, beside rehearsal ─────┤
              └→ native Mac build → sealed runtime → native proof ──────────────┤
                                                          all passed → receipt
                                                                                │
server: newest release → download to disk → check once → unpack beside app → "Update available"
                                                                                │
owner presses Update (now or when work finishes) → pause work → backup if schema changes
                                                                                │
installer unit: stop → swap folders → start → health ── healthy → done
                                                    └─ not healthy → swap back, restore → failed
```

## What it is made of

- **Publication.** A GitHub workflow on hosted machines. For one commit, the suites and static checks run on their own machines while another builds once and seals the archive. It then proves the archive on a clean Debian root and, at the same time, rehearses the install on a throwaway known-good release with the same installer the live host uses. A native Mac machine builds and proves the Mac runtime. A last machine checks every proof against the build and writes the receipt. Panels offers a release only when the whole run passed. If one job fails, "Re-run failed jobs" reruns only that job. Every attempt of a run checks the same commit, so the last machine accepts evidence from the attempt that made it, and still requires the latest result of every job to be a pass. Shared or unknown changes select all default suites.
- **The sealed release.** An archive plus a receipt. It holds the app, a private CPython 3.12 with its native libraries, a virtual environment with every pinned Python dependency, the built browser app, and the agent backends' Node packages. Each build resolves current Claude Code and Codex CLIs and records the versions it checks. Once sealed, the archive never changes.
- **The live tree.** `~/Deployments/Panels/current/` with `app`, `data`, and `logs`. An install changes only `app`. The next release waits beside it, unpacked, and the previous one stays beside it after an install. See [Environments](environments.md).
- **The installer.** One small program that uses only Python's standard library. It runs outside every app, so the app it replaces cannot break it.
- **The attempt record.** One small file beside the live data, outside the database and its backups. It says what one press of Update is doing and how it ended, with the time of each step. The update card and the deployment status that the iOS app shows read it. It is the one file that crosses versions, so every version ignores what it does not know and keeps it when it writes.

## What a release needs from the host

A release brings its own Python, so installation never runs a package installer. Before publication, a separate proof runs the archive on a minimal Debian root with no Python 3.12 and no network.

The host supplies the glibc loader and a short named list of system libraries, Node 22, Hermes, media tools such as `ffprobe`, `python3` 3.11 or newer for the installer, and every credential. Credentials live in the host's service environment and agent homes, so replacing the app never replaces them. Each release states its needs in a versioned runtime contract, and Panels checks it against the host before it offers the release.

Actual Claude and Codex conversations use owner-maintained CLIs outside the release. App installation does not change those versions. SDK dependencies keep their API pins, while CLI qualification checks the protocol that Panels uses instead of an exact version number. These checks cannot guarantee compatibility with every future CLI update. See [Conversations](conversations.md) for the owner update controls.

The platform adapter owns the Linux service identity, independent installer launch, and installer source. The copied installer stays self-contained and uses only the standard library. It keeps the same stop, swap, start, health, and rollback sequence.

The update barrier remains separate work. Current updates close admission and take any schema backup before the installer stops the service. A later change must join confirmed child retirement, acknowledged-result settlement, and the snapshot before it can claim no-loss rollback. A queue drain alone does not prove process retirement.

## How an install happens

**Getting ready.** On its usual one-minute check, the server looks for the newest successful publication from `main`. For a release it is not running, it streams the archive to disk, checks its fingerprint against the receipt once, and unpacks it beside the live app on the same disk. It checks that the unpacked release names itself as the receipt does and that the host meets its runtime contract, and keeps the matching Mac runtime. Only then does the card show **Update available**. A failed download or check is tried again on the next pass and never shows as an error. Only one waiting release is kept.

**The press.** Khushal chooses **Update now** or **When work finishes**. Only the owner can; Chief, Managers, and Workers are refused. The server answers at once and does the rest in the background. **When work finishes** waits for an idle workspace through the existing readiness loop; Khushal can cancel the wait or change it to Update now. When it is time, Panels pauses work, checks Ticket integrity, and, if the new release would change the database schema, takes a verified [backup](backups.md) while the server is still running. Then it marks the attempt as installing and starts the installer in its own short-lived user service, so stopping Headroom cannot kill it. The server copies the installer out of its own app first, so the folder swap cannot change the code that runs.

**The installer.** It stops the service that the server runs in, which the server reads from its own systemd cgroup, so a copy of Panels elsewhere can never stop the live one. It renames the current app aside, renames the waiting release into its place, starts the service, and waits for the new app to report its exact commit. A healthy answer comes from the process that serves Headroom, so work is already open. Startup also refreshes the agents' skill links. Interrupted conversations get one recovery prompt ([Conversations](conversations.md)). On success the installer marks the attempt done and keeps only the newest previous app.

A new database starts from one complete schema. An existing staging database upgrades directly to it in one startup transaction. The oldest revision it upgrades is staging's `goal_holders`; the upgrade passes `retire_explicit_reply_requirement` and `canonical_manager_identities`, where the installed release already stands. Later revisions add the Mac executor's mailbox tables and the stall-alarm switch, and drop the old Mac connection tables. A release refuses a database at a revision its own history does not contain. An upgrade refuses a database that still contains cancelled Tickets; resolve them through the prior supported version first.

## What can be undone

- **Before the stop.** A refusal leaves the running app untouched and the attempt failed with a plain reason.
- **After the swap.** If the new app does not prove healthy in time, the installer stops it, renames the previous app back, restores the database from the backup if one was taken, starts the prior app, and waits for it to prove healthy. The attempt is then failed with a plain reason. A failed recovery says so plainly, never success.
- **After health.** The database is never restored. A later code rollback is a new install and does not rewind data.

Every attempt ends. The installing state carries a deadline. If the installer vanishes, the server settles the attempt past that deadline from the commit it is running: the new one counts as done, anything else as failed. A settled attempt never blocks a new one.

## The card and the Mac

One server answer supplies the card's state and allowed actions: up to date, available, waiting, updating, failed, Mac failed, or Mac unreachable. While the connection is lost during the restart, the card keeps saying updating until the deadline. The browser offers a refresh when its loaded app differs from the server, and the server rejects a write from an outdated page.

The publication carries a Mac runtime from the same source, and one press covers both machines. The VPS never waits for the Mac. The Mac sees its target from the moment work pauses and activates it once the VPS install is done. The card stays updating until the Mac reports the new runtime. A Mac that reports a failure shows **Mac failed**; one that says nothing for about a minute and a half after the VPS part shows **Mac unreachable**; it may be asleep or offline. Either way **Try again for the Mac** sends a fresh target for the same release, and a Mac that returns later and reports the new runtime moves the card on by itself. A release without a Mac part, or a host without a Mac, skips it. See [The Mac executor](executor.md#what-runs-on-the-mac).

## The switch to mailboxes

The first release in which the [Mac executor](executor.md) works through mailboxes (protocol 22) is one switch-over. The old and new Mac links never run side by side.

1. **The order.** The VPS updates first, and the Mac follows by itself. One press covers both. **Update now** stops running Mac agents once; **When work finishes** waits for them. The schema changes, so Panels takes the backup before the install. The new VPS refuses the old Mac's link with "Mac needs update", but its update channel still serves it. The Mac installs its new runtime, which leases at once, and its report moves the card on. The Mac never updates before the VPS.
2. **The watchdog.** The release ships the stall watchdog's unit and timer, but nothing starts them. Khushal installs and enables the timer once the release is current, with the [install lines](../ops/panels-environments/README.md#stall-watchdog). Until then, stalls are recorded but no alarm goes out. Native push also needs the `PLAN_APNS_*` inputs in that unit's environment.
3. **The LaunchAgent.** An update replaces the Mac's runtime but not its LaunchAgent. Khushal renders and loads it again once, by the [runbook](../ops/mac-executor/installation.md#render-and-start-one-launchagent), so macOS schedules the executor as foreground work and does not defer its lease. Loading it restarts the executor, so do it while no Mac agent runs.
4. **The check.** The Mac shows as present, its health shows a recent lease, and a Mac Ticket starts.

**Rollback** means the previous release plus the pre-deploy database backup. A new migration drops the old Mac connection tables, so the previous release refuses the upgraded database. Restore the backup while the service is stopped ([Backups](backups.md)). Work recorded after the deploy is lost. The Mac keeps its previous runtime beside the new one. The new runtime cannot lease from the old server, so the Mac goes back by the [runbook's](../ops/mac-executor/installation.md#deferred-update-and-failed-installation) recovery steps. The previous runtime opens the same outbox and reads the new runtime's handoff record. It ignores the command journal.

## The one-release bridge

The installed release before this design installs through the old route: it asks GitHub to run an `adopt` job on the self-hosted runner. That job stays for one release. It checks out the release the host is running and runs that release's own installer, unchanged. This works from 5622d2a81, whose installer already accepts the new release's runtime, schema, receipt, and health answer; an older installed release refuses it before anything stops. On its first start the new server turns the old install request into a done attempt, so a Mac that the old server stopped receives its target and updates. The next release deletes the runner, the adopt job, its dispatch inputs, and that import.

The deploy workflow never changes Tailscale Serve. A separate manual workflow restores the private route if it is lost.

## Decisions that shape it

- Build once, prove those bytes, install the same bytes.
- A push publishes. Only the owner's press installs.
- Do the slow work before the press: download, check, and unpack while Headroom keeps working.
- The installer depends on nothing it replaces: standard library, host Python, its own unit.
- Data is restorable only until the new app is proven.
- Every attempt ends, and nothing waits for the Mac.

## When `scripts/dev` refuses

Development worktrees must never share dependency storage with the installed app, because a shared `node_modules` or `.venv` can write into the deployed tree and break its integrity proof. `scripts/dev` guards setup, npm, pytest, and mypy before the tool starts. No flag turns it off.

A refusal prints the command and one of two findings: shared deployment storage detected, or dependency safety could not be established. It names the path examined, what it resolved to, the protected deployment path, and the signal used.

- **The sharing is real.** Make a fresh worktree and run `scripts/dev setup`. Do not delete dependencies through an unresolved link or mount.
- **Independent dependencies were refused.** Fix the detector and add a regression check.
- **Evidence was missing.** Restore it, then retry.

Commands outside the guard, such as Ruff or a direct `.venv/bin` call, can still write into the deployed tree. The archive digest shows the damage afterwards; it does not prevent it. If a write reaches the installed tree, leave it in place and report it. Never clean the installed tree to force an install through.

## Code

- `.github/workflows/deploy.yml`: publication, the install rehearsal, and the bridge `adopt` job. `recover-private-route.yml`: route repair.
- `src/planner/environments/update_provider.py`: reading publications and streaming downloads. `update_service.py`, `update_api.py`: getting ready, the press, and the card's state.
- `src/planner/environments/update_attempts.py`: the attempt record. `update_install.py`: the schema and backup decision and the installer's start. `src/planner/platform_runtime/linux/update_installer.py`: the standalone installer.
- `src/planner/platform_runtime/`: shared contracts and selection, with Linux native packaging, contract validation, and service operations. `src/planner/environments/runtime_admission.py` and `scripts/runtime_target_proof.py` coordinate runtime proof.
- `src/planner/environments/mac_updates.py`: the Mac target and its progress.
- `web/src/components/UpdateActions.svelte`, `web/src/lib/updates/`: the card.
- `scripts/dev`, `scripts/development_safety/`: the worktree guard.
- `ops/panels-environments/`: user units and runner setup.

_Last verified: 2026-11-11._
