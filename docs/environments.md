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

## Mac executor packages

The executor uses a standard wheel from the exact committed source. Setuptools
selects Python packages and declared data from `pyproject.toml`. The cold source
export includes package input roots, runtime and build locks, and backend manifests.
An immutable manifest records the requested revision and exported file hashes.
Build output stays in temporary source copies, outside that manifest.

The permanent native runtime contains the wheel and its pinned dependencies.
The build uses `requirements-build.txt` and `requirements-executor.txt` without
implicit dependency resolution. A separate clean environment checks the complete
dependency closure before installation into the permanent runtime. All native
dependencies use compatible binary wheels. The only source exception is
`http_ece==1.2.1`, which publishes no wheel. Its exact source digest, locked build
tools, pure wheel metadata and contents, and output digest receive explicit checks.
No native toolchain installation or broad source fallback exists.

The standard generated `panels` entrypoint serves the executor and Worker CLI.
The resident wrapper uses the isolated permanent interpreter and installed package.
It refuses `serve` and caller-supplied revision overrides. It injects the exact
committed revision for `executor run`. It does not add source paths to `sys.path`.
The separate deployment-tool bootstrap remains a standard-library cold tool.

Read-only preflight checks the installed wheel, distribution metadata, scripts,
Python files, native extensions, and bytecode against the receipt. The runtime
marker binds that receipt to the source manifest and permanent runtime path.
Changed or extra installed files refuse reuse and activation. Existing identity
fences, native Node locks, launchd quiescence, rollback, persistent data, normal
HOME, and provider credentials keep their existing boundaries.

The supported Mac target is Apple Silicon with native ordinary CPython 3.14.3.
The interpreter probe checks its actual OS, architecture, implementation, and ABI.
Every selected wheel must fit the real OS and interpreter before installation.
The native wheels require macOS 11 or later. Tags alone do not qualify a host.
Intel macOS is explicitly unsupported for this pinned dependency set because
cryptography 49 removes that target. The package refuses Intel before host mutation.
Linux VPS builds remain supported on their matching native interpreter and architecture.

The capability readout in `ops/panels-environments/executor-build-contract.json`
separates required support from verified host evidence. Its original handoff
provenance remains unchanged. Current packaging inputs live in `current_packaging`.
Physical Mac support, installed credentials, reboot, and pre-unlock behavior remain
unverified. The implementation does not authorize host installation or live probes.

The Mac-initiated executor connection requires explicit resolved host, port, user,
identity file, and known-hosts file. Deployment inputs are
`PANELS_EXECUTOR_SSH_PORT`, `PANELS_EXECUTOR_SSH_USER`,
`PANELS_EXECUTOR_SSH_IDENTITY_FILE`, and `PANELS_EXECUTOR_SSH_KNOWN_HOSTS_FILE`.
The identity and known-hosts paths belong to the Mac. They are separate from the
VPS-to-Mac delivery credentials. Ambient SSH configuration, agents, default keys,
aliases, proxies, and interactive key setup do not supply missing inputs.


---

_Last verified: 2026-08-09._
