# Production deployment

Production runs one Git-free application as the existing UID-1000 `vps` user:

```text
~/Deployments/Panels/current/
├── app/     deployed executable application
├── data/    database, managed files, configuration, and runtime state
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
exact temporary checkout
        │ prove HEAD
        ▼
build and validate temporary candidate app
        │ compatibility proof + verified live backup
        ▼
replace current/app
        │ restart + prove requested SHA
        ▼
success, or restore and prove the prior app
```

The self-hosted runner proves Python is at least 3.12 and Node is version 22. It builds
under runner-temporary storage and removes that state when the job ends. The resulting
app contains no Git metadata and carries a validated identity for the requested commit.

The workflow uses the fixed VPS contract:

- current root: `~/Deployments/Panels/current`
- live database: `~/Deployments/Panels/current/data/planner.db`
- backups: `~/Deployments/Panels/current/data/backups`
- health: loopback port 8767
- service manager: `systemctl`
- service: `panels-live.service`

## Replacement and recovery

Deployment is serialized. It validates the candidate and proves one-version database
compatibility before running the deployed application's `backup-current` command.
Only then does it retain the working app as transaction-temporary fallback, install the
candidate at `current/app`, restart Panels, and require health to report the requested
commit.

If replacement, restart, or health proof fails, deployment restores the fallback app,
restarts it, and proves it healthy. If recovery cannot be proved, the retained fallback
path is the exact operator continuation point. A successful transaction removes it.

Code rollback uses the same workflow with an earlier full SHA. It replaces only the
app; it does not restore the database.

## Existing-host precondition

The operator must establish the single-user filesystem layout, user units, runner, and
persistent paths before enabling automatic deployment. This ticket does not migrate the
live host, rename `vps-agent`, or change the existing Tailscale Serve route. UID 1000 is
preserved later by renaming that account, not by creating a second identity.

Code paths: `.github/workflows/deploy.yml`, `src/planner/environments/app.py`,
`src/planner/environments/deployment.py`, and `src/planner/environments/cli.py`.

---

_Last verified: 2026-07-25._
