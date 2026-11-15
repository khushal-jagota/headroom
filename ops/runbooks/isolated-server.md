# Run an isolated server with an explicit workspace

Use this to run a second Headroom server on one machine with its own state and its
own default workspace for new agent conversations. The design is in
[docs/environments.md](../../docs/environments.md). Worktree setup for agents is in
the root `AGENTS.md`.

## 1. Choose paths and a port

Use separate absolute paths for the server's state and its workspace. Create the
workspace before startup. Select an available local port. Start from a shell without
inherited Panels settings.

`PLAN_WORKSPACE_ROOT` (YAML key `workspace_root`; the environment value wins) must be
an existing absolute directory. An absent value or YAML null keeps the usual workspace
default. An empty value, a relative path, a missing directory, a file, or a failed
path resolution stops startup. Paths can use `~` and resolve to their canonical
directory.

## 2. Start the server

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

## 3. If startup refuses the workspace

Startup adds `<workspace>/.agents/skills` as a symlink to `<database-parent>/skills`.
It creates `.agents` only when absent, and that parent must be a real directory. An
existing `skills` symlink must resolve to the same managed directory. Any other entry
stops startup, including a real skills directory, a dangling link, or a link to
another source. The error names the conflict and the expected target. Startup never
replaces or removes existing entries. Select a clean workspace or arrange its
contents, then start again. The accepted link remains after shutdown and exposes later
managed skill updates.

## 4. Existing conversations

An explicit conversation workspace and a Worker's usable Project directory still take
precedence. Existing conversations keep their recorded workspace through resume.
After a configuration change, reset a conversation to use the new root on its next
message.

## 5. Stop

Stop the server when the work that needed it is finished.
