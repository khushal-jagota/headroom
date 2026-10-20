# Runtime environments

Panels uses one operating identity on the VPS: the existing UID-1000 `vps` user. Live,
staging, and Ticket processes are separated by explicit paths and launch configuration,
not by Linux accounts.

```text
~/Deployments/Panels/
└── current/
    ├── app/       deployed, Git-free application
    ├── data/      live database, files, config, locks, and sockets
    └── logs/      live logs

~/projects/Panels/
└── Panels/
    ├── source checkout
    └── data/environments/staging/   persistent fake staging state
```

Live uses loopback port 8767 behind the existing Tailscale Serve route. Staging and
Ticket servers use dynamic loopback ports and run only while active work needs them.
The checked-in user units under `ops/panels-environments/` launch live, the dedicated
GitHub Actions runner, and backups. There is no staging unit.

Code paths: `src/planner/environments/`, `src/planner/cli/main.py`.

## Live

The live environment root is the deployment root. Its manifest and persistent paths
resolve beneath `~/Deployments/Panels`, and its state shares the deployment transaction's
stable `current/data` and `current/logs` directories.

```sh
panels environment prepare \
  --kind live \
  --environment-root "$HOME/Deployments/Panels" \
  --json

panels environment inspect \
  --kind live \
  --environment-root "$HOME/Deployments/Panels" \
  --json
```

Preparation records and validates the live contract but does not pre-create
`current/data` or `current/logs`; the first exact-app deployment must be able to create
the complete `current` layout transactionally. Live carries no source-repository or
credential-file path: the user service runs only the deployed Git-free app with the
`vps` user's normal provider homes.

The deployed user service starts
`~/Deployments/Panels/current/app/bin/panels-launcher serve` with explicit live paths.
It binds to `127.0.0.1:8767`. `systemctl --user` starts, stops, restarts, and inspects
the service without sudo.

The supervisor records its parent identity at launch. If that parent is a verified
systemd manager, only that manager can request shutdown with SIGINT or SIGTERM.
An ordinary Ticket server accepts those signals from its owner as before. An
unknown launch parent selects guarded mode without an authorized manager.

Linux can deny access to the manager's executable even for its own user. In that
case, the supervisor verifies the manager through authenticated D-Bus owner
credentials and the kernel process start time. It repeats those checks before
it accepts a shutdown signal. An unavailable bus or changed owner causes refusal.

Guarded mode stays fixed. An unknown sender, a changed parent, or an unverifiable
manager causes refusal rather than an unguarded shutdown. Each refusal appears on
the server's own stderr with the signal, sender, reason, and exact-PID SIGKILL
guidance. If a legitimate service stop fails these checks, systemd waits for its
stop timeout and then uses SIGKILL. This delay is deliberate.

Workers stop their own Ticket servers by the PID recorded at launch. If a guarded
Ticket server refuses that signal, follow its diagnostic and use SIGKILL only
against that recorded PID. Never use a broad process pattern. The live service
remains operator-owned.

A detached dogfood launch can also enter guarded mode. For example, a `setsid`
launcher can exit before the supervisor captures its parent, so the parent is
already PID 1. If PID 1 is the verified systemd manager, the supervisor selects
guarded mode. The Worker's exact-PID SIGTERM then produces a refusal. Use the
recorded PID and the diagnostic's SIGKILL guidance for that dogfood server.
`setsid` alone changes the session, not the parent. The parent at capture decides
the shutdown mode.

Interactive commands use `~/Deployments/Panels/current/app/bin/panels` instead. The
global `panels` wrapper follows that command. The deployed command establishes its own
application root and exact SHA, preserves the rest of the caller context, and does not
enter the managed service launcher's allowlisted environment.

Live host migration is separate operator work. This repository change does not rename
the account, move existing host state, or alter Tailscale Serve.

## Staging

Staging is an on-demand foreground process from `~/projects/Panels/Panels`. Its fake
database, managed files, and logs persist below
`~/projects/Panels/Panels/data/environments/staging`.

```sh
STAGING_ROOT="$HOME/projects/Panels/Panels/data/environments"
STAGING_REPOSITORY="$HOME/projects/Panels/Panels"

panels environment prepare \
  --kind staging \
  --environment-root "$STAGING_ROOT" \
  --repository-root "$STAGING_REPOSITORY" \
  --credentials-env-file "$HOME/projects/Panels/Panels/data/staging.env" \
  --json

panels environment run \
  --kind staging \
  --environment-root "$STAGING_ROOT" \
  --repository-root "$STAGING_REPOSITORY"
```

Each run reserves an available loopback port through server startup and prints the
actual URL. Stop the process when active work finishes. `environment reset` restores
the canonical fake fixture without changing staging's prepared identity or paths.

## Ticket servers

Ticket servers run from isolated worktrees. Their database, managed files, Hermes
state, logs, locks, and sockets stay in the worktree's local `data/`; their loopback
port is selected for that run. Confirm the imported `planner` package and every
printed `PLAN_*` path resolve to the worktree before starting a service.

Ticket servers stay on the machine loopback interface. Panels does not expose them
through its application. Use a Ticket-owned HTML, image, Markdown, or other file for
work that the user must review. Store that durable work in the live Ticket file tree and
link it through `/files/tickets/<ticket-id>/<relative-path>`.

Stop Ticket services after active work, then remove their local state, worktree, and
branch at Consequences.

## Launch isolation and provider homes

Every environment launch rebuilds its process environment from an allowlist. It keeps
the `vps` user's normal `HOME`, locale, terminal, path, and temporary-directory values,
adds validated provider credentials, and supplies explicit contract-owned `PLAN_*`
paths. Ambient `PLAN_*` values are not forwarded.

Hermes, Codex, and Claude therefore use the `vps` user's normal homes. Prepared
environment manifests do not emulate another user's home and do not copy provider
identity or session trees. Manifests from the retired role-account model must be
re-prepared; Panels does not translate their account or generated-home fields.

Live, staging, and Ticket repository and state paths may not share or nest across
environment boundaries. Credential contents are never written to a manifest or
printed by inspection.

## Host status and maintenance

`panels environment status --json` reads the full local filesystem and process
inventory without contacting the Panels HTTP server. `GET /api/vps-status` keeps that
same response for compatibility and diagnostics.

The browser uses the smaller `GET /api/vps-status-summary`. It contains only the
deployed SHA, relevant deployment outcome and public-safe detail, CPU/RAM/disk use, and
latest verified-backup age. Linux CPU use comes from two bounded `/proc/stat` samples;
RAM uses `MemTotal` and `MemAvailable`; disk uses the filesystem holding runtime data.
Every measure has a fixed healthy, warning, or critical threshold. Missing or malformed
host evidence is returned as an explicit unavailable value and reason. Collection runs
off the server event loop because the CPU sample waits briefly.

`panels environment cleanup` is a dry run. `--apply` collects a fresh inventory and
re-proves every target before changing it. It can rotate configured oversized logs,
remove expired backup temporaries, and prune verified backups beyond seven. It never
kills a process or removes a worktree, cache, environment, deployed app, or ambiguous
path.

## Handoffs

- **Production deployment** (`deployment.md`) — replacing the live app with an exact
  commit.
- **Database backups** (`backups.md`) — verified live snapshots and restore.
- **The command-line tool** (`cli.md`) — the command tree that exposes environment
  lifecycle verbs.
- **Worker orchestration** (`worker-orchestration.md`) — the scheduled-Ticket and
  worker-readiness loops the foreground server started by `environment run` owns.
- **The conversation system** (`conversation-system.md`) — the three agent backends
  that use the configured provider homes.

---

_Last verified: 2026-08-09._
