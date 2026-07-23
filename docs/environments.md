# Runtime environments

Panels has two prepared runtime environments: `live` and `staging`. They use separate
checkouts, databases, managed files, logs, locks, control sockets, and credential-file
references. Hermes-backed Panels runs use the operator's normal Hermes home by default
unless `PLAN_HERMES_HOME` is explicitly overridden.

```
environment root
  |
  +-- live/       operator-owned state, fixed ingress port
  |
  +-- staging/    persistent fake state, port chosen when started
```

Ticket worktree servers are not prepared environments. They are temporary processes
started from isolated worktrees while active work needs them.

The local commands prepare and inspect runtime state and can render Linux intent. The
checked-in files under `ops/panels-environments/` are inputs for an operator-owned
Linux installation. They do not create accounts, install services, change ingress, or
start a server by themselves.

Code paths: `src/planner/environments/`, `src/planner/cli/main.py`.

## Live environment

Live is a prepared external environment attached to a separate `main` checkout. Its
manifest records the checkout, durable state paths, credential-file reference, and a
fixed port for known ingress. Live state and credentials remain operator-owned.

Use an absolute environment root and repository root:

```sh
ENV_ROOT=/var/lib/panels/environments
LIVE_RELEASE_ROOT=/opt/panels/releases

panels environment prepare \
  --kind live \
  --environment-root "$ENV_ROOT" \
  --repository-root "$LIVE_REPO_ROOT" \
  --credentials-env-file /etc/panels/environments/live.env \
  --json
```

Preparation creates an empty layout. It does not seed fake data or silently copy a
production database. Inspect the prepared contract without printing credential values:

```sh
panels environment inspect \
  --kind live \
  --environment-root "$ENV_ROOT" \
  --json
```

Import existing state only while both the source server and prepared live environment
are stopped:

```sh
panels environment import-live \
  --environment-root "$ENV_ROOT" \
  --source-db /path/to/source/data/planning.db \
  --source-managed-files-root /path/to/source/data/files \
  --source-hermes-home /path/to/source/data/hermes-home \
  --source-runtime-user-home /path/to/source/user-home \
  --source-logs-root /path/to/source/data/logs \
  --json
```

The command requires an already prepared live environment. It reads the committed
SQLite state through the backup API, including committed WAL data, and stages the
database, managed files, worker settings, managed skills, complete Hermes home, Codex and Claude
identity/configuration/session trees, and an archive of the source logs. Worker
settings must exist at `worker-settings` beside the source database; that path is
inferred so it cannot be omitted accidentally. The runtime user home locates its
`.codex` and `.claude` children. Database, Hermes, managed-file, and log sources may
otherwise live below that home. Sockets and other special files are skipped; private
directory and file modes and ordinary symlinks are preserved. Panels-owned Hermes
skill links are repaired to the managed skills home in the imported generation.

All source paths must exist and remain outside the prepared live instance. Durable
state is built as one new generation and made current with one atomic stable-pointer
switch. Failure before that switch leaves the previous complete generation current;
failure to remove the old generation after the switch does not roll back committed
state. The same command restores a captured backup by supplying its corresponding five
source locations. The prepared credential-file reference is configuration and is not
replaced by import.

Starting and stopping live is an operator action. `run` validates the caller-provided
checkout against the prepared contract, changes to that checkout, and starts the
ordinary foreground Panels server:

```sh
panels environment run \
  --kind live \
  --environment-root "$ENV_ROOT" \
  --repository-root "$LIVE_REPO_ROOT"
```

Live cannot be reset or removed through the environment CLI.

## Staging environment

Staging is prepared once and attached to the separate `staging` checkout. Its fake
database, managed files, logs, credentials reference, and testing activity persist
across runs. Its durable configuration has no server port.

```sh
STAGING_REPO_ROOT=/opt/panels/staging

panels environment prepare \
  --kind staging \
  --environment-root "$ENV_ROOT" \
  --repository-root "$STAGING_REPO_ROOT" \
  --credentials-env-file /etc/panels/environments/staging.env \
  --json
```

`inspect` reports `runtime_port_policy` as `dynamic` and `bind_attempts` as `10`. It
does not report a configured `port` or a `running` value.

```sh
panels environment inspect \
  --kind staging \
  --environment-root "$ENV_ROOT" \
  --json
```

Start staging only while testing, browsing, computer use, exploration, or other active
work needs it:

```sh
panels environment run \
  --kind staging \
  --environment-root "$ENV_ROOT" \
  --repository-root "$STAGING_REPO_ROOT"
```

Each run asks the operating system for an available loopback port and holds that bound
listener through server startup. If a bind still reports that its address is in use,
staging retries up to the prepared policy's bound. The command prints
`staging http://127.0.0.1:<actual-port>` to standard error. Stop the foreground process
when the active work is finished; its database and other durable state remain available
for the next run.

Reset staging only when the canonical fake fixture is wanted again:

```sh
panels environment reset \
  --kind staging \
  --environment-root "$ENV_ROOT" \
  --json
```

Reset rebuilds the fake database and managed files but keeps staging's prepared
identity and durable paths.

## Ticket worktree servers

Create each Ticket branch and isolated worktree from current `staging`. Set up its
dependencies and local runtime state, then confirm its source, dependencies,
configuration, database, files, Hermes state, and logs resolve to that worktree.

Ticket worktree servers use the repository's ordinary server and test tools. They have
no environment kind, external manifest, registry entry, fixed port, or central port
allocator. Survey current port use on each start, select an available port, and retry
with another if startup loses a bind race.

Run services only while active work needs them and stop them afterward. The worktree's
database and other local state may remain for reuse until Closeout. After the verified
result reaches `staging`, remove the Ticket services, state, worktree, and branch.

## Credentials and launch isolation

Credential files are references in prepared environment state. Their contents are
never written to a manifest or printed by `inspect` or `render-linux`.

Use a separate credential file for live and staging. The checked-in examples contain
only supported names with empty values. Copy them outside Git before filling real
values.

The parser accepts only the runtime model-provider names allowed by the environment
policy: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, and `GOOGLE_API_KEY`. It rejects
malformed, duplicate, unknown, or contract-owned keys.

`environment run` starts from a scrubbed process environment. It keeps the operator's
normal `HOME` for Hermes and provider CLIs such as Claude and Codex, plus basic locale,
terminal, path, and temporary-directory values. It adds the validated credential values
and supplies the contract-owned database, port, log, lock, socket, and Hermes executable
values. Other ambient `PLAN_*` values are not forwarded.

The caller must provide exactly one `--repository-root`. The command checks that
existing checkout against the prepared environment and uses it as the launch working
directory. Repository roots are working directories, not runtime state directories,
and live and staging cannot share or nest them.

## Current-host cutover

Moving an existing server into prepared live is an operator checkpoint, not an
automatic side effect of preparation. Before asking the operator to act, the Ticket
must record the current revision and launch command, every state and configuration
path, an online rehearsal backup, the final quiesced backup location, the import or
restore commands, expected command output, health checks, and the unchanged old
startup used for fallback.

The operator then stops the old server, takes the final backup, imports the preserved
database, managed files, worker settings, and Hermes identity and sessions with
`environment import-live`, confirms the prepared configuration and archived logs, and
starts live from the accepted `main` checkout. The health check covers the application,
managed files, durable sessions, and active work.

If any check fails, stop the new process, restore the final backup, and return to the
recorded old checkout and startup. Do not advance `staging`, change the primary
checkout, or remove the fallback until live has passed those checks and the worker has
reconnected.

Nightly SQLite backups and operator restore are described in [database backups](backups.md).
Automatic deployment and public ingress changes remain separate work.

## Linux intent

Render the account, ownership, and service intent for a prepared environment:

```sh
panels environment render-linux \
  --kind live \
  --environment-root "$ENV_ROOT" \
  --environment-manager-root /opt/panels/environment-manager \
  --json
```

The rendered live service runs as `panels-live`; staging runs as `panels-worker`. The
renderer lists each environment's writable state and repository paths. Live ownership
is intended to keep live state, config, and credentials unreadable and unwritable by
the staging account.

The output is render-only and reports `vps_enforcement_verified` as false. Linux
isolation exists only after an operator has installed and checked the accounts,
permissions, credential files, services, and ingress on the target host.

The live unit uses the operator-owned `/opt/panels/current` release pointer. Staging uses
`/opt/panels/staging` as its development checkout and the root-owned manager checkout.
The live service receives only external writable state paths; the release root is read-only.

## Handoffs

- **The command-line tool** (`cli.md`) — the command tree that exposes environment
  lifecycle verbs.
- **The employee runtime** (`employee-runtime.md`) — the foreground server runtime
  that `environment run` starts.
- **Hermes gateway** (`systems.md`) — the external worker gateway whose homes stay
  separate between live, staging, and Ticket worktrees.

---

_Last verified: 2026-07-23._
