# Production deployment

Production runs one Git-free application. On the single-user Mac its durable layout is:

```text
~/Deployments/Panels/current/
├── app/     deployed executable application
├── data/    database, managed files, configuration, and runtime state
└── logs/    application logs
```

Only `app` changes during deployment. `data` and `logs` are siblings of the app, so code changes,
failed deployments, and code rollback do not replace persistent state. The self-hosted runner and
the Panels LaunchAgent run as the signed-in operator. Production continues to use that operator's
Hermes home and provider credentials. Ordinary deployment does not require root access or a
separate service user.

## Exact commit path

A push to `main` deploys `${{ github.sha }}`. The workflow also has an owner-triggered input for
one lowercase, full 40-character commit SHA, including an earlier commit selected for code
rollback. Both routes use the same job:

```text
exact temporary checkout
        │ prove HEAD
        ▼
build and validate temporary candidate app
        │
        ▼
deploy candidate into current/app
```

The runner proves its host Python is at least 3.12 and Node is version 22. It builds the candidate
under runner-temporary storage and removes that build state when the job ends. The installed app
contains no Git metadata and carries a validated identity for the requested commit.

The workflow repository variables are `PANELS_CURRENT_ROOT`, `PANELS_BACKUP_SOURCE_DB`,
`PANELS_BACKUP_DIRECTORY`, `PANELS_HEALTH_URL`, `PANELS_SERVICE_MANAGER`, and
`PANELS_SERVICE_NAME`. For the single-user Mac, `PANELS_CURRENT_ROOT` is
`~/Deployments/Panels/current`.

## Existing-host precondition

Automatic app deployment starts only after the operator has established the single-app layout,
service path, workflow variables, and persistent `data` and `logs` paths on that host. This
repository change does not move a running host from an older installation layout and does not
rewrite an older app manifest. Perform that host cutover separately, prove the new app and
persistent state, and remove superseded host artifacts only after the operator accepts the cutover.
The checked-in systemd changes remain installation intent; this work does not alter the live VPS.

## Replacement and recovery

Deployment is serialized. It validates the candidate before changing the live app, then completes
the verified pre-deployment backup. After that it keeps the working app as a transaction-temporary
fallback, installs the candidate as `current/app`, restarts Panels, and requires local health to
report the requested commit.

If replacement, restart, or health proof fails, deployment restores the fallback app, restarts it,
and proves it healthy. A successful deployment removes the fallback. If recovery itself cannot be
proved, deployment reports the retained fallback path so the operator has an exact continuation
point. The fallback is recovery state for one transaction, not an installed second version.

Code rollback follows the same workflow with an earlier full SHA. It replaces the app only; it does
not restore the database. Database changes therefore pass a one-version compatibility gate that
proves the immediately previous app can start and pass health against the database upgraded by the
candidate.

The checked-in launchd and systemd files are installation intent. They launch
`current/app/bin/panels-launcher`; they do not install or modify the live host themselves.

Code paths: `.github/workflows/deploy.yml`, `src/planner/environments/app.py`,
`src/planner/environments/deployment.py`, and `src/planner/environments/cli.py`.

_Last verified: 2026-07-25._
