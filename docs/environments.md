# Runtime environments

Panels can prepare and run three runtime shapes from one repository contract:
`live`, `staging`, and disposable `preview` instances. Each prepared instance has its
own database, managed files, Hermes home, logs, lock, control socket, port, manifest,
and credential-file reference.

```
environment root
  |
  +-- live/                 operator-owned state, Linux account panels-live
  |
  +-- staging/              stable fake state, Linux account panels-worker
  |
  +-- previews/<id>/        disposable fake state, Linux account panels-worker
```

The local command proves configuration and process isolation only. It can also render
Linux intent. The checked-in files under `ops/panels-environments/` are the Linux
account, systemd, tmpfiles, and environment-file inputs for a later VPS install, but
they do not install Linux users, permissions, systemd units, networking, or services by
themselves. The service text calls the ordinary `panels environment run` path, which
replaces itself with `python -m planner serve`. There is no separate deployment mode.

Code paths: `src/planner/environments/`, `src/planner/cli/main.py`.

## Local commands

Use an absolute environment root. These examples keep runtime state outside the
current live instance.

```sh
ENV_ROOT=/tmp/panels-environments
LIVE_REPO_ROOT=/opt/panels/live
STAGING_REPO_ROOT=/opt/panels/staging
PREVIEW_REPO_ROOT=/opt/panels/previews/feature-123
```

Prepare live. This creates an empty layout and records the credential-file reference;
it does not seed data.

```sh
panels environment prepare \
  --kind live \
  --environment-root "$ENV_ROOT" \
  --repository-root "$LIVE_REPO_ROOT" \
  --credentials-env-file ops/panels-environments/live.env.example \
  --json
```

Prepare staging with resettable fake state and a stable port:

```sh
panels environment prepare \
  --kind staging \
  --environment-root "$ENV_ROOT" \
  --repository-root "$STAGING_REPO_ROOT" \
  --credentials-env-file ops/panels-environments/staging.env.example \
  --json
```

Prepare a disposable preview. The preview id must use lowercase letters, digits, and
hyphens; the allocated port stays with that preview until removal.

```sh
panels environment prepare \
  --kind preview \
  --instance-id feature-123 \
  --environment-root "$ENV_ROOT" \
  --repository-root "$PREVIEW_REPO_ROOT" \
  --credentials-env-file ops/panels-environments/preview.env.example \
  --json
```

Inspect a prepared instance without printing credential values:

```sh
panels environment inspect \
  --kind preview \
  --instance-id feature-123 \
  --environment-root "$ENV_ROOT" \
  --json
```

Run a prepared instance:

```sh
panels environment run \
  --kind staging \
  --environment-root "$ENV_ROOT" \
  --repository-root "$STAGING_REPO_ROOT"
```

Render Linux unit and ownership intent:

```sh
panels environment render-linux \
  --kind staging \
  --environment-root "$ENV_ROOT" \
  --json
```

Reset staging or a preview back to the canonical fake seed:

```sh
panels environment reset \
  --kind staging \
  --environment-root "$ENV_ROOT" \
  --json
```

Remove a stopped preview:

```sh
panels environment remove \
  --kind preview \
  --instance-id feature-123 \
  --environment-root "$ENV_ROOT" \
  --json
```

Run the opt-in real-Hermes cross-home smoke after staging and one preview are already
prepared and independently authenticated:

```sh
panels environment smoke-hermes \
  --environment-root "$ENV_ROOT" \
  --preview-id feature-123 \
  --hermes-python "$HOME/.hermes/hermes-agent/venv/bin/python" \
  --json
```

## State model

Staging and previews share one canonical versioned fake seed: `fake-fixture-v1`.
They do not share state. Each materialization runs the current fixture builder
separately and builds a new SQLite database and managed-file tree, so ids, database
bytes, managed Markdown, and file placeholders are independent even when the logical
seed is the same.

Staging is stable. It keeps the same identity, port, Hermes home, and credential-file
reference across resets. Previews are disposable. A preview id keeps its allocated port
until removal; removing it deletes only that preview root and releases the allocation.

Live preparation creates an empty layout and manifest. It never imports fake data and
never accepts a production database as a seed. Live state and credentials remain
operator-owned.

## Credentials

Credential files are references in the manifest. Their contents are never written to the
manifest or printed by `inspect` or `render-linux`.

Use a separate credential file for each prepared instance. The checked-in examples
show only supported names with empty values. Copy them outside git before filling real
values, then pass that per-instance file through `--credentials-env-file`.

The parser accepts only runtime model-provider names allowed by the environment policy:
`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `GOOGLE_API_KEY`. It rejects malformed,
duplicate, unknown, or forbidden keys. Forbidden keys include contract-owned
`PLAN_*`, Hermes session/config keys, `PYTHONPATH`, actor/ticket identity, process
identity, and launch roots.

`panels environment run` resolves the operator's `PLAN_HERMES_PYTHON` selection (or the
normal Hermes default) before replacing `HOME` with the instance Hermes home. It then
starts from a scrubbed process environment. It keeps only locale, terminal, `PATH`,
`TMPDIR`, and `LC_*` basics from the ambient shell, then adds validated credential-file
values and the contract-owned runtime values:
`PLAN_DB_PATH`, `PLAN_PORT`, `PLAN_LOGS_DIR`, `PLAN_DISPATCHER_LOCK_PATH`,
`PLAN_SERVER_CONTROL_SOCKET`, `PLAN_HERMES_HOME`, `PLAN_HERMES_PYTHON`, and `HOME`.
`HOME` is set to the instance's Hermes home. Other ambient `PLAN_*` values are not
forwarded.

The real-Hermes smoke creates unrelated durable sessions in the already-authenticated
staging and preview homes, prompts each one, closes its first ACP child, and loads the
same session from a fresh child before reporting the ids. It does not delete those
sessions; the stored sessions belong to the homes used for the smoke and remain there
for operator inspection or later cleanup.

`run` requires exactly one caller-provided `--repository-root`. The command validates
that existing worktree against the prepared manifest's allowed repository roots, then
uses exactly that caller-trusted path as the current working directory before execing
`python -m planner serve`. The manifest records the allowed roots, but it does not
choose the launch directory by itself. Repository roots are working directories, not
runtime state directories.

Repositories cannot be shared across live, staging, or preview instances. They also
cannot be nested inside one another. Repository roots participate in the same registry
overlap and live-nesting checks as database paths, managed files, Hermes homes, logs,
locks, sockets, and credential-file references.

## Local And VPS Guarantees

The rendered live service runs as `panels-live`. Rendered staging and preview services
run as `panels-worker`. The renderer also lists the writable paths expected for each
instance: the instance root, database directory, managed files, Hermes home, logs,
runtime lock/socket directory, and that instance's repository root. The repository is
writable because Panels workers operate their checked-out repository on the VPS.

Repository roots are working directories only. Runtime writes stay under the prepared
instance root. Live Linux ownership is meant to keep live state, config, and credentials
unreadable and unwritable by `panels-worker`; staging and previews are isolated by
configuration and state paths, not by hostile sandboxing from one another.

The exact guarantee boundary is:

- **Local development:** prepare, inspect, run, reset, remove, render, and fake tests
  prove separate paths, ports, manifests, process environment values, and foreground
  server processes. They do not prove Linux account isolation.
- **VPS operation:** live gets an OS security boundary only after an operator-owned VPS
  account, filesystem permissions, credentials file, systemd unit, ingress path, and
  installed service are actually created and checked.

Linux output is render-only with `vps_enforcement_verified` set to `false` until that
operator-owned VPS account, permissions, and installed service have actually been
checked. On macOS and other local development machines, the renderer proves text and
paths only. It does not claim the local OS has enforced Linux accounts or modes.

The checked-in static systemd files use distinct repository conventions:
`/opt/panels/live`, `/opt/panels/staging`, and `/opt/panels/previews/%i`. In each unit,
`WorkingDirectory`, the `ExecStart --repository-root` argument, and the repository
entry in `ReadWritePaths` must agree.

## Cleanup

Use `reset` for staging or preview fake state. Use `remove` for stopped previews, or
for staging only when retiring the stable non-production identity. Both commands prove
the instance is stopped by taking the same port-scoped lifecycle lease used by
`panels serve`; they do not inspect or signal processes.

Do not remove `live` through this CLI. Live cleanup, promotion, backups, monitoring,
public ingress, and final VPS installation are separate operator or later-ticket work.

## Handoffs

- **The command-line tool** (`cli.md`) — the command tree that exposes environment
  lifecycle verbs.
- **The employee runtime** (`employee-runtime.md`) — the foreground server runtime that
  `panels environment run` eventually starts.
- **Hermes gateway** (`systems.md`) — the external worker gateway whose homes are kept
  separate per instance.

## Deferred

- **Installed Linux service management.** The repo renders units and ownership intent
  only. Trigger: the final VPS ticket installs accounts, paths, units, ingress, and
  service policy.
- **Production operations.** Backups, recovery, monitoring, immutable release layout,
  and cleanup automation remain outside this environment contract. Trigger: live VPS
  operation beyond a single prepared service.

---

_Last verified: 2026-07-19._
