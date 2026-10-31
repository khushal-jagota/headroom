# Run locally

A local Headroom is one source checkout, one Python environment, one loopback server, and one SQLite file under `data/`. That is the same shape as production: one host, one record. The difference is that you are running the checkout, not a sealed archive, and the server is bound to your machine’s loopback.

The install commands live in the repository [README](../README.md). They create `.venv`, install the pinned Python dependencies, install the package editable, install the `web/` and `agent_backends/` Node trees, build the browser app, and start `panels serve`. This page is what that server is once it is up.

## The process you started

`panels serve` listens on `127.0.0.1:8767` unless `PLAN_PORT` says otherwise. The built browser is served at `/`, with Vite chunks under `/_app/`. Configuration defaults live in `config.yaml`. Every value there can be overridden by the matching `PLAN_*` environment variable. Test-only switches such as `PLAN_TEST_MODE` and `PLAN_FAKE_NOW` are environment-only. They are absent from the file on purpose.

The database path defaults to `data/planning.db`. Logs, the dispatcher lock, and backups default under `data/` as well. That directory is gitignored runtime state. Production uses a different filename and directory, `~/Deployments/Panels/current/data/planner.db`, described in [Environments](environments.md). Do not point a local checkout at the live database to “just look.”

With dispatch enabled, which is the default, this process owns the readiness loop, the schedule loop, and the manager-wake loop, behind one machine lock. A second serve on the same lock does not become a second scheduler.

## Before any agent work

Install and authenticate the backend you mean to use. Codex needs the `codex` executable on `PATH`. Claude Code uses the CLI the backend dependencies installed, or `PLAN_CLAUDE_EXECUTABLE`. Hermes needs `PLAN_HERMES_PYTHON` pointed at its interpreter. Voice transcription needs `PLAN_GROQ_API_KEY`. Without it, dictation is off. The key is not an agent login.

Choose the Worker, model, and reasoning before approving a new Ticket’s kickoff when the Brief controls are still on screen. Provider limits still apply. Ticket gates still do not sandbox the machine.

A worktree that will run tests or a second server follows the setup in [`AGENTS.md`](../AGENTS.md): its own `.venv`, its own `PLAN_*` paths, and guarded `scripts/dev` commands so a dependency tree cannot alias the deployed application. Those guards are part of [Deployment](deployment.md).

## Where it shows up

Open `http://127.0.0.1:8767`. You get the same Workspace, Review, and Settings as production. Push notifications from a local server work on localhost. They do not reach a phone on the public internet unless you have put a real HTTPS origin in front of a host. That path is [Deployment](deployment.md) and [Notifications](notifications.md).

## An isolated server with an explicit workspace

Set `PLAN_WORKSPACE_ROOT` to an existing absolute directory for new agent conversations.
The matching YAML key is `workspace_root`. The environment value takes precedence.
An absent value or YAML null preserves the usual workspace default. An empty value,
relative path, missing directory, file, or failed path resolution stops startup.
Paths can use `~` and resolve to their canonical directory.

Use separate absolute paths for an isolated server's state and workspace. Create the
workspace before startup. Start from a shell without inherited Panels settings.
For example, after you select an available local port:

```sh
mkdir -p /absolute/path/to/isolated-state /absolute/path/to/isolated-workspace
PLAN_DB_PATH=/absolute/path/to/isolated-state/planning.db \
PLAN_LOGS_DIR=/absolute/path/to/isolated-state/logs \
PLAN_BACKUP_DIR=/absolute/path/to/isolated-state/backups \
PLAN_DISPATCHER_LOCK_PATH=/absolute/path/to/isolated-state/dispatcher.lock \
PLAN_SERVER_CONTROL_SOCKET=/absolute/path/to/isolated-state/server-control.sock \
PLAN_SERVER_LIFECYCLE_LEASE_PATH=/absolute/path/to/isolated-state/server-lifecycle.lock \
PLAN_WORKSPACE_ROOT=/absolute/path/to/isolated-workspace \
PLAN_PORT=8877 PLAN_DISPATCH_ENABLED=false panels serve
```

Startup adds `<workspace>/.agents/skills` as a symlink to `<database-parent>/skills`.
It creates `.agents` only when absent. That parent must be a real directory.
An existing `skills` symlink must resolve to the same managed directory. Any other
entry stops startup, including a real skills directory, a dangling link, or a link
to another source. The error names the conflict and expected target. Startup never
replaces or removes existing entries. Select a clean workspace or arrange its contents
before the next attempt. The accepted link remains after shutdown and exposes later
managed skill updates.

An explicit conversation workspace and a Worker's usable Project directory retain
precedence. Existing conversations retain their recorded workspace through resume.
After a configuration change, reset the relevant conversation to use the new root on
its next message. See [conversation workspaces](conversations.md#the-workspace-for-an-agent).

Code paths: `core/config.py`, `core/server.py`, `runtime/workspaces.py`, `skill_sources.py`.

## Pointers

`config.yaml`, `src/planner/core/config.py`, `src/planner/core/server.py`, and `panels serve` in `src/planner/cli/main.py`.

_Last verified: 2026-10-30._
