# Production deployment

Production runs one Git-free application as the existing UID-1000 `vps` user:

```text
~/Deployments/Panels/current/
├── app/     deployed executable application
├── data/    database, managed files, configuration, deployment lifecycle, and runtime state
└── logs/    application logs
```

Only `app` changes during deployment. `data` and `logs` remain in place through code
changes, failed deployments, and code rollback. Serving, deployment, rollback, backup,
and user-service control need no sudo.

## Services and runner

Linux uses systemd user services. `panels-live.service` launches the deployed app,
`panels-deployment-runner.service` owns the dedicated GitHub Actions runner used by the
deployment workflow, and the backup timer invokes the deployed app's backup command.
The `vps` user controls them with `systemctl --user`.

macOS retains its launchd path. Both service controllers operate in the signed-in
user's service-manager domain, and both preserve the user's normal Hermes, Codex, and
Claude homes.

## Exact commit path

A push to `main` deploys `${{ github.sha }}`. The owner-triggered workflow input accepts
one lowercase, full 40-character SHA, including an earlier commit selected for rollback.
Both routes use the same transaction:

```text
workflow checkout
        ▼
publish Preparing for the requested SHA
        │
        ▼
exact temporary checkout → prove requested HEAD
        │
        ▼
build and validate temporary candidate app
        │ stage candidate while production remains live
        ▼
stop service → create verified database + managed-files snapshot
        │ hard cutover
        ▼
replace current/app → restart + prove requested SHA
        │
        ▼
app healthy → reconcile managed skills into Hermes, Codex, and Claude homes
        │
        ▼
publish workflow success

or stop candidate → restore snapshot + prior app → prove prior SHA
```

The self-hosted runner proves Python is at least 3.12 and Node is version 22. It builds
under runner-temporary storage and removes that state when the job ends. The resulting
app contains no Git metadata and carries a validated identity for the requested commit.
Runtime validation requires both executable entrypoints: `bin/panels` is the
root-relative interactive CLI that preserves caller context, while
`bin/panels-launcher` is the isolated launcher for services and other managed runtime
operations.

The workflow uses the fixed VPS contract:

- current root: `~/Deployments/Panels/current`
- live database: `~/Deployments/Panels/current/data/planner.db`
- backups: `~/Deployments/Panels/current/data/backups`
- deployment lifecycle: `~/Deployments/Panels/current/data/deployment-lifecycle.json`
- health: loopback port 8767
- service manager: `systemctl`
- service: `panels-live.service`
- managed skills: `~/Deployments/Panels/current/data/skills`
- agent homes: `~/.hermes`, `~/.codex`, and `~/.claude`

After the app deployment succeeds, the current workflow deployment tool invokes
`environment provision-skills` with all four paths explicitly. Using the workflow tool
means an intentional rollback to an older app still applies the current skill-retirement
policy. The command reconciles Panels skills into each agent home, removes explicitly
retired Panels skills, and leaves unrelated custom skills in place. This post-deploy step
is idempotent. If it fails after app cutover, the app remains deployed and the workflow
reports failure; rerunning the deployment safely retries skill reconciliation.

## Deployment status evidence

The runner owns one small versioned lifecycle file in persistent `current/data`.
Checkout is the unavoidable bootstrap because it supplies the canonical stdlib-only
writer. Immediately after checkout, the workflow reads the current deployment ID and
starts the new record before proving or building the requested source. A checkout
failure and a requested value that is not a lowercase full SHA therefore cannot be
recorded; later Git proof, runtime, build, deployment, and provisioning failures can.
The app-deploy transaction advances the same deployment ID
through restart, exact-SHA verification, app health, and rollback. The workflow records
success only after app deployment and production skill provisioning have both
completed. Its finalizer turns interrupted work into a public-safe failure and does not
overwrite a proved rollback.

Writes are serialized beside the record. Starting a run compares the deployment ID the
runner read—including an explicit expectation that no readable ID existed—with the ID
observed while holding the write lock. Transitions compare their own deployment ID.
A stale runner therefore cannot overwrite a newer deployment. Each bounded JSON record is written
to a same-directory temporary file, synced, atomically replaced, and followed by a
directory sync. Panels rejects links, non-regular files, oversized files, unsupported
versions, malformed fields, and invalid SHAs rather than guessing.

`GET /api/deployment-status` projects that evidence into `idle`, `preparing`,
`restarting`, `back_up`, `problem`, or `unknown`. Preparation and restart evidence
expires if the runner disappears. A proved success appears as `back_up` only briefly
and only when the running app SHA exactly matches the requested SHA. Failure and
rollback remain problems until a later deployment supersedes them. The server watches
the lifecycle path and sends the existing contentless change signal after create,
replace, change, or deletion, so connected browsers re-read the canonical file.

An app deployed before this lifecycle feature cannot expose the new status endpoints or
observe runner updates while that old app is serving. The current workflow tool and
finalizer still record the rollback durably. The status becomes visible again when a
compatible app is restored or deployed; this is the bootstrap limitation when crossing
the feature boundary.

## Replacement and recovery

Deployment is serialized. It fully stages and validates the candidate while production
remains live, then stops the service and runs the deployed application's
`backup-current` command. That command publishes a verified snapshot containing both
the database and managed files. Deployment then retains the prior app as a temporary
fallback, installs the candidate at `current/app`, restarts Panels, and requires health
to report the requested commit.

Every candidate, including its staged copy, must contain the complete current runtime
and both executable entrypoints. The existing app still receives manifest, artifact,
and safe-tree validation, but it may predate the interactive `bin/panels` entrypoint.
This allows an intact older production app to upgrade while still rejecting a tampered
one before downtime, backup, restart, or filesystem replacement begins.

If replacement, restart, or health proof fails, deployment stops the candidate, restores
the verified database-and-files snapshot, restores the fallback app, restarts it, and
proves the prior SHA healthy. If recovery cannot be completed and proved, the error
reports the exact retained filesystem paths for operator continuation. A successful
transaction removes the fallback.

Code rollback uses the same workflow with an earlier full SHA. Because database
migrations may be destructive, automatic failure recovery restores the pre-cutover
snapshot; a later intentional rollback deploy does not independently rewind data.

## Existing-host precondition

The operator must establish the single-user filesystem layout, user units, runner, and
persistent paths before enabling automatic deployment. This ticket does not migrate the
live host, rename `vps-agent`, or change the existing Tailscale Serve route. UID 1000 is
preserved later by renaming that account, not by creating a second identity.

Code paths: `.github/workflows/deploy.yml`, `src/planner/environments/app.py`,
`src/planner/environments/deployment.py`,
`src/planner/environments/deployment_lifecycle.py`,
`src/planner/environments/cli.py`, and `src/planner/skill_sources.py`.

---

_Last verified: 2026-07-29._
