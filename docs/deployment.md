# Deployment

Deployment is how a commit on `main` becomes the app the live host runs. Publication and installation are separate. A push produces a sealed, checked release on disposable hosted machines. The live workspace changes only when Khushal asks for one of those releases, and then it gets exactly those bytes. The host never builds code, and a push never installs itself.

```text
push to main ─┬→ suites (unit, integration, e2e, browser) ─────────────────────────────────┐
              ├→ static checks (ruff, mypy, frontend, vitest) ─────────────────────────────┤
              ├→ build once → seal archive → prove on clean root, beside rehearsal ────────┤
              └→ native Mac build → sealed runtime → native proof ─────────────────────────┤
                                                                                           │
                                                                     all passed → receipt
                                                                                           │
                                                              retained release (archive + proof)
                                                                                           │
owner asks (now or when idle) → pinned request → host runner → admit → stop → snapshot
                                                                                           │
                          replace app → health → commit → reopen work → skills → route check
```

## What it is made of

- **Publication.** A GitHub workflow on hosted machines. For one commit, the suites and the static checks run on their own machines. At the same time another machine builds once and seals the archive. It then proves the archive on a clean Debian root and, at the same time, rehearses the install on a throwaway copy of a known-good release. A native Mac machine also builds and proves the Mac runtime for the same commit. A native Mac job also builds and checks the Mac runtime. A last machine waits for all of them. It checks the app proofs against the build digest, checks the Mac archive and proof, and writes the receipt. Panels offers a release only when the whole run passed and the receipt exists, so nothing can be installed before every check passes. Domain checks include their permission behavior; shared or unknown changes select all default suites.
- **The sealed release.** An archive plus a proof record. It holds the app, a private CPython 3.12 with its native libraries, a virtual environment with every pinned Python dependency, the built browser app, and the agent backends' Node packages, including the Claude Code and Codex CLIs.
- **The live tree.** `~/Deployments/Panels/current/` with `app`, `data`, and `logs`. Replacement changes only `app`. See [Environments](environments.md).
- **The deployment runner.** A self-hosted GitHub runner on the VPS, used only to install.
- **The owner's install request.** A small file beside the live data, outside the database and its snapshots, and the update dialog in the sidebar.
- **The lifecycle record.** Durable evidence of each install attempt and how it ended. The status pill, the [VPS](vps.md) summary, and Chief read it.

The test suites run on several processes at once. Each test process builds the current database once by replaying every migration, then copies it into each new test database; a test of a migration, or of a database that already holds data, still gets the real replay.

## What a release needs from the host

A release brings its own Python, so installation never runs a package installer and the host's Python does not matter. Packaging refuses a native library it cannot resolve; that is a broken release, not something the host repairs. Before publication, a separate proof runs the archive on a minimal Debian root with no Python 3.12 and no network.

The host supplies the glibc loader and a short named list of system libraries, Node 22, Hermes, media tools such as `ffprobe`, and every credential. Credentials live in the host's service environment and agent homes, so replacing the app never replaces them. Each release states its needs in a versioned runtime contract. Installation checks that contract against the host and refuses anything else, including any host other than the supported Debian and Ubuntu x86-64 releases. A release without a contract has to be republished.

## How an install happens

Khushal opens **Update available** and chooses **Update now** or **When work finishes** for a retained release. The request stays pinned to that release. He can cancel a wait or change it to Update now. Only the owner can ask. Chief, Managers, and Workers are refused. Without an update source configured on the host, owner updates are unavailable.

The existing readiness loop asks GitHub to run the install job on the host runner, so there is no second scheduler. An uncertain dispatch is never resent on its own. Khushal can cancel until the runner claims the request, and that cancellation is neutral, not a failure.

The publication also carries a prebuilt Mac runtime from the same source. One approval covers both machines, with separate progress for the Mac. **When work finishes** waits for turns and background tasks on both machines. The VPS keeps the approved Mac bytes before dispatch so a sleeping Mac can catch up after publication expiry. [The Mac executor](executor.md) owns its fixed launcher, runtime swap, and reversion rules. Older publications without a Mac runtime explicitly require a manual Mac update.

A new database starts from one complete schema. An existing staging database upgrades directly to that schema in one startup transaction. No bridge installation is needed.

The current entry revision is staging's `goal_holders`. The direct upgrade applies the sweep removals and Manager rename, preserves retained source records, and finishes at `canonical_manager_identities`. Fresh and upgraded databases have the same schema.

Before the sweep merges, merge the latest staging into the sweep branch. Confirm staging's migration head and the live database revision with a read-only connection. If staging adds migrations, update the one supported entry revision and its direct transformation. Repeat the upgrade on a fresh copy of the live database. Compare every retained source record after the documented transformations, account for removed feature records, check integrity and foreign keys, and compare the complete schema with a fresh database. Keep the proof with the integration Ticket. Do not install the sweep until that proof passes.

The installer runs in its own short-lived user service, so stopping Headroom cannot kill it. While the old app still serves, it checks the download against the request, checks the runtime contract, and refuses a database whose schema history it does not recognise or that is too old. Then it asks the server for a final claim. The server rechecks workspace activity under the same barrier that guards new conversations and background work, and runs the startup Ticket integrity check. An idle request that finds work running goes back to waiting. A failed integrity check fails the request before any downtime.

Once claimed, the installer stops the service, captures interrupted agent sessions, and takes a verified [backup](backups.md). It keeps the prior app, moves the new one into place, restarts, and waits for the new app to report its exact commit. Meanwhile ordinary requests, CLI work, and background loops stay closed. Health, status, and owner update recovery remain available. When the new app proves healthy, the runner records the commit and work reopens. Interrupted conversations then get one recovery prompt ([Conversations](conversations.md)). Last, the job refreshes the agents' skill links and checks the private HTTPS route over HTTP/2.

## What can be undone

The commit is the boundary.

- **Before the swap.** If stopping the service or the backup fails, the runner restarts the untouched prior app and proves it is back.
- **After the swap, before the commit.** If the new app fails its health proof, the runner restores the snapshot and the prior app, then proves the prior commit. A failed recovery is reported with what was kept, never as success.
- **After the commit.** The database is never restored. A failed skill refresh or route check leaves the new app installed and the job incomplete, not the app unhealthy.

Every dispatched attempt has a durable deadline. The server settles the request from the running version and the matching attempt records, even if the runner disappears. A settled attempt cannot regain installation authority or block a later request. An expired wait also ends. Expiry alone never reopens ordinary work or restores data. A paused candidate needs health proof before work can resume.

An older server may accept an install before its runner can recognise the receipt. The runner reads old and new receipts through the same defaults. If an already accepted legacy attempt is stuck before shutdown, an operator can explicitly recover it through the same install job. Recovery first proves the prior app is still healthy, work remains paused, and the saved request and failure match. It grants a new bounded attempt for that pinned release; it never silently selects a newer release. See the [legacy recovery runbook](../ops/panels-environments/recover-legacy-update.md).

One server answer supplies the update state and allowed actions. The sidebar card moves through Updating and Restarting to **Update complete · Refresh**. The browser reconnects after a restart and preserves a message draft until Refresh is safe. A failed install offers Try again while the release remains valid. Update complete means that the VPS finished. The Mac has separate status and work.

A finalizer records the installed commit, application health, and each later check separately. It runs from checked-out source with the host's own Python, never from the app it judges. Missing evidence stays uncertain.

An upgrade refuses a database that still contains cancelled Tickets. Resolve those records through the prior supported version before retry.

The deploy workflow never changes Tailscale Serve. A separate manual workflow restores the private route if it is lost.

## Decisions that shape it

- Build once, prove those bytes, install the same bytes.
- A push publishes. Only an owner request installs.
- A release carries its own interpreter and dependencies. The host provides a declared, checked minimum.
- Data is restorable only until the new app is proven. A later code rollback is a new install and does not rewind data.
- Uncertain evidence stays uncertain. Nothing guesses success, re-dispatches, or replays a prompt.

## Older cancelled Tickets

An upgrade refuses a database that contains cancelled Tickets. Resolve these records through the prior supported version before retry.

## When `scripts/dev` refuses

Development worktrees must never share dependency storage with the installed app, because a shared `node_modules` or `.venv` can write into the deployed tree and break its integrity proof. `scripts/dev` guards setup, npm, pytest, and mypy before the tool starts. No flag turns it off.

A refusal prints the command and one of two findings: shared deployment storage detected, or dependency safety could not be established. It names the path examined, what it resolved to, the protected deployment path, and the signal used.

- **The sharing is real.** Make a fresh worktree and run `scripts/dev setup`. Do not delete dependencies through an unresolved link or mount.
- **Independent dependencies were refused.** Fix the detector and add a regression check.
- **Evidence was missing.** Restore it, then retry.

Commands outside the guard, such as Ruff or a direct `.venv/bin` call, can still write into the deployed tree. The archive digest shows the damage afterwards; it does not prevent it. If a write reaches the installed tree, leave it in place and report it. Never clean the installed tree to force an install through.

## Code

- `.github/workflows/deploy.yml`: publication, rehearsal, and owner adoption. `recover-private-route.yml`: route repair.
- `src/planner/environments/deployment.py`: replacement and the restore boundary. `update_runner.py`: the host-side install.
- `src/planner/environments/update_service.py`, `update_requests.py`, `update_admission.py`: the owner request, final claim, and admission hold.
- `src/planner/environments/runtime_packaging.py`, `runtime_contracts.py`, `runtime_admission.py`, and `scripts/runtime_target_proof.py`: the private runtime and host contract.
- `src/planner/environments/deployment_lifecycle.py`, `deployment_attention.py`, `scripts/deployment_finalize.py`: evidence and reporting.
- `web/src/components/UpdateActions.svelte`, `web/src/lib/updates/`: the owner's actions.
- `scripts/dev`, `scripts/development_safety/`: the worktree guard.
- `ops/panels-environments/`: user units and runner setup.

_Last verified: 2026-11-04._
