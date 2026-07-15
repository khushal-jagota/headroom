# Panels restart supervisor — implementation plan

This plan implements `contract.md` without changing configuration, database, HTTP, Worker-type,
or frontend contracts. `panels serve` remains the operator-owned foreground PID. It supervises one
replaceable application child; `panels restart` can only ask that supervisor for a replacement.
The independently landed stopgap commit `430cf92` already establishes the worker-instruction
boundary and stays as the baseline for this implementation.

## File and function ownership

### `src/planner/server_lifecycle/contracts.py`

Define only the lifecycle protocol types shared by client and supervisor:

- `SERVER_CONTROL_PROTOCOL_VERSION`
- `ServerControlRequest` and `ServerControlResponse`, with the single `restart` operation and
  `accepted` response
- `ServerLifecycleError`, `ServerLifecycleAlreadyOwnedError`,
  `ServerRestartConnectionError`, and `ServerRestartProtocolError`

The local wire format is one newline-terminated JSON object in each direction. It carries a protocol
version and operation/status only. It does not carry a PID, launch directory, command, environment,
or replacement arguments.

### `src/planner/server_lifecycle/control.py`

Own address resolution and local control transport:

- `resolve_server_control_socket_path(port, environ, launch_root)` uses
  `PLAN_SERVER_CONTROL_SOCKET` when supplied and otherwise returns the short per-user,
  port-scoped Unix socket path. Resolve an explicit relative path once against the captured launch
  root; the resulting absolute string is the exact value injected into the child.
- `resolve_server_lifecycle_lease_path(port)` returns a separate short per-user, port-scoped lock
  path. It does not depend on the control-socket override, so two supervisors cannot own one port
  through different socket names.
- `encode_server_control_request`, `decode_server_control_request`,
  `encode_server_control_response`, and `decode_server_control_response` validate the one versioned
  protocol.
- `request_server_restart(control_socket_path)` connects only to that Unix socket, sends one restart
  request, requires a compatible accepted response, and raises the connection/protocol errors above
  on every failure. It contains no process discovery, signalling, subprocess, or server-launch path.

### `src/planner/server_lifecycle/application.py`

Move the current application body of `src/planner/cli/main.py::serve` into
`run_application_process()`: resolve the imported source root, change to it, load `config.yaml`,
create DB/log directories and schema, build clock/adapters/connection factory, call the unchanged
`planner.core.server.create_app`, and run Uvicorn on the configured port. Add the module entry point
used only by the supervisor:

```text
<captured interpreter> -m planner.server_lifecycle.application
```

Do not move or duplicate FastAPI lifespan shutdown. `create_app` and
`Config.shutdown_grace_seconds` remain its sole owners.

### `src/planner/server_lifecycle/supervisor.py`

Own the foreground process lifecycle:

- `resolve_planner_launch_root()` resolves the absolute root from the installed `planner` source.
- `PortScopedServerLifecycleLease.acquire()` takes and retains one non-blocking `fcntl.flock` file
  descriptor until cleanup; failure raises `ServerLifecycleAlreadyOwnedError` without touching the
  live socket or process.
- `ServerSupervisor.run()` binds the control socket, installs signal wakeup handling, spawns the
  initial child, and serializes every stop/spawn transition.
- `ServerSupervisor._spawn_application_child()` uses the captured `sys.executable`, environment,
  and launch root; prepends `<launch-root>/src` to `PYTHONPATH`; injects the resolved
  `PLAN_SERVER_CONTROL_SOCKET`; inherits stdin/stdout/stderr; sets `cwd=launch_root`; and uses
  `start_new_session=True` so the child has its own process group.
- `ServerSupervisor._accept_control_request()` validates one request, writes the accepted reply
  completely, and waits for the client to close that accepted control exchange before returning a
  restart decision to the lifecycle loop. A client that has received but not closed the accepted
  exchange therefore holds the old child alive; this makes acknowledgement-before-termination a
  deterministic public protocol property rather than a scheduler race.
- `ServerSupervisor._stop_application_child()` sends `SIGTERM` only through the owned
  `Popen.terminate()` handle and waits for that exact child to exit. It never calls `killpg`, scans a
  port, or derives a second shutdown deadline.
- `run_server_supervisor()` captures root/interpreter/environment once, loads only enough existing
  config to identify the port, and runs the supervisor.

Use a selector plus a signal wakeup pipe for `SIGINT`, `SIGTERM`, and child exit; do not add a polling
interval or timing configuration. A requested restart performs `acknowledge -> terminate -> wait ->
spawn`. The loop cannot spawn until the previous `Popen.wait()` returns, so at most one application
child exists. Requests waiting during a replacement are accepted after the spawn and therefore
cause a later generation. An unrequested child exit returns non-zero; there is no retry loop.
Operator SIGINT/SIGTERM performs the same graceful child stop, removes the socket, releases the
lease, and returns zero.

### `src/planner/cli/main.py` and `src/planner/server_lifecycle/__init__.py`

- Reduce `serve()` to calling `run_server_supervisor()` and translating a lifecycle ownership/start
  failure into a non-zero Click result.
- Add the top-level `restart()` Click command. It resolves the installed launch root and existing
  config only to select the control address, calls `request_server_restart`, prints exactly
  `Panels restart accepted.` after a valid acceptance, and translates connection/protocol failures
  to clear stderr plus a non-zero exit.
- Keep lifecycle exports narrow in `__init__.py`; no domain code imports the CLI.

### Tests and current documentation

- Add `tests/unit/test_server_lifecycle_control.py` for the public restart CLI/error protocol seam.
- Add `tests/e2e/test_server_lifecycle.py` for real foreground-supervisor/application processes.
- Extend `tests/e2e/conftest.py::ServerHandle` with the configured port and control-socket path while
  keeping `proc` as the foreground `panels serve` process. Existing readiness, log inheritance,
  environment isolation, and teardown continue unchanged: `proc.terminate()` now targets the stable
  supervisor and waits for its child cleanup.
- Keep `tests/unit/test_server_shutdown_process.py` unchanged as the regression that a foreground
  SIGINT still reaches the existing application shutdown and recovery contracts.
- Keep and rerun
  `tests/unit/test_minds.py::test_provisioned_worker_skills_do_not_let_workers_own_the_panels_server`.
  The stopgap text already satisfies the fixed skill contract; do not create another instruction
  mechanism.
- Update `docs/cli.md`, the CLI/lifecycle section of `docs/systems.md`, and the shutdown/restart
  paragraph of `docs/employee-runtime.md` in present tense. Describe the stable supervisor,
  controlled command, captured launch root, single child, graceful recovery boundary, and
  operator-owned fallback only; do not document implementation internals as user concepts.

No edit to `src/planner/core/server.py` is planned: its existing lifespan is deliberately preserved.

## Vertical RED -> GREEN slices

Run every command from the worktree root. Add each public regression first, run it against the
unchanged production code, and record the expected missing-command/process failure before adding
that slice's production code.

### Slice 1 — restart is control-only

RED in `tests/unit/test_server_lifecycle_control.py`:

1. Invoke `panels restart` with an explicit nonexistent temporary socket. Assert non-zero, a clear
   stderr connection error, no success line, the socket remains absent, an unrelated sentinel
   subprocess remains alive, and no listener appears on an isolated port.
2. Run a tiny test-owned Unix listener that returns malformed JSON, then a supported JSON shape with
   an incompatible version. Assert both invocations fail as protocol errors and never print the
   acceptance line.
3. Run a tiny listener that captures the request and replies with the compatible accepted object.
   Assert the exact versioned restart request, exact stdout line, and zero exit.

RED command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_server_lifecycle_control.py
```

GREEN by adding `contracts.py`, the encode/decode and client half of `control.py`, and the
`main.py::restart` command. The success branch must be unreachable until a response has parsed as a
compatible acceptance. Repeat the exact command.

### Slice 2 — `panels serve` is one stable foreground supervisor

RED in `tests/e2e/test_server_lifecycle.py` and by the existing real-process shutdown test:

1. Start the normal test-mode `panels serve`; discover direct children through the OS process table
   and assert exactly one application child while `/api/meta` and `/` are ready.
2. Start a second `panels serve` with the same `PLAN_PORT` but a different explicit control-socket
   value. Assert it exits non-zero with the ownership error, does not signal or replace the first
   supervisor/child, and the first HTTP endpoint remains ready.
3. Start another second `panels serve` with both the same `PLAN_PORT` and the same live control
   socket. Assert lease acquisition fails before stale-socket cleanup: the first socket remains in
   place, a real `panels restart` can still use it, and the first supervisor remains the owner.
4. Terminate the foreground PID. Assert it exits zero, its exact application child exits, its
   control socket is removed, and nothing listens on the test port. Preserve the existing fixture
   teardown behavior for every other e2e test.
5. Kill only the application child in a separate instance. Assert the stable supervisor exits
   non-zero and cleans its socket rather than retrying.

RED commands:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/e2e/test_server_lifecycle.py -k 'supervisor or second or operator or unexpected'
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/unit/test_server_shutdown_process.py
```

GREEN by adding `application.py`, the lease/spawn/signal/cleanup foundation in `supervisor.py`,
switching `main.py::serve`, and minimally extending `tests/e2e/conftest.py`. Do not redirect the
child's standard streams and do not alter the test `PLAN_*` environment; the existing suite's log,
DB, fake-clock, port, recovery, and gateway behavior must pass through unchanged. Repeat both exact
commands.

### Slice 3 — accepted restart replaces exactly one generation from the captured root

RED in `tests/e2e/test_server_lifecycle.py`:

1. Capture supervisor PID and direct application-child PID from a ready test server. Invoke the real
   `panels restart` with its explicit control-socket environment. Assert exact acceptance output,
   unchanged live supervisor PID, old child exit, one different direct child PID, and `/api/meta`
   ready again with the same test-mode values.
2. Open a raw versioned restart exchange, receive the complete accepted response, and deliberately
   keep that client socket open. Assert the old application child remains alive and no replacement
   appears until the client closes the accepted exchange; only then may shutdown begin. This is the
   deterministic proof that acknowledgement is fully delivered before child termination.
3. Invoke restart from a Ticket-worktree-shaped temporary directory containing its own `.git` marker,
   a conflicting `config.yaml`, and no `web/dist`. Compare `/` before and after, require the built app
   rather than the missing-`web/dist` fallback, and prove the replacement still comes from the
   supervisor's captured launch root rather than the caller's repository-shaped cwd.
4. Sample the supervisor's direct children throughout replacement. Assert the old PID is gone before
   the new PID is observed and no sample contains more than one child. While the first supervisor is
   alive, retry the same-port second-supervisor assertion.
5. Send two requests around the spawn boundary and assert serialized generations: every accepted
   command yields one eventual replacement, child PIDs change in order, and never overlap. Do not
   assert an implementation-specific coalescing count for requests accepted before spawning.

RED command:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/e2e/test_server_lifecycle.py -k 'restart or caller or generation'
```

GREEN by completing `ServerSupervisor._accept_control_request` and the serialized replacement path.
The client supplies no root/interpreter/environment; all replacement arguments come from the
supervisor snapshot. Repeat the exact command, then run the complete lifecycle e2e file once:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/e2e/test_server_lifecycle.py
```

### Slice 4 — shipped boundary and live documentation

Keep the existing provisioned-skill test green, and strengthen it only if the final wording changed:
shared `panels-worker` must include operator ownership plus the explicit `kill`/`pkill`/`lsof`/
`panels serve` prohibition; `panels-worker-new-worker` must say durable writes first, `panels restart`
last when available, and operator restart plus stop when unavailable.

Update only the three named docs after process behavior is green. Then run:

```sh
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q \
  tests/unit/test_server_lifecycle_control.py \
  tests/unit/test_server_shutdown_process.py \
  tests/unit/test_minds.py::test_provisioned_worker_skills_do_not_let_workers_own_the_panels_server
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q tests/e2e/test_server_lifecycle.py
.venv/bin/ruff check \
  src/planner/server_lifecycle src/planner/cli/main.py \
  tests/unit/test_server_lifecycle_control.py tests/e2e/test_server_lifecycle.py \
  tests/e2e/conftest.py
.venv/bin/mypy src/ tests/typing/
git diff --check
```

## Review and final completion

The plan reviewer checks the protocol contains no process authority, the lease is port-scoped, the
child receives the exact selected control socket, acknowledgement precedes termination, and all
spawn inputs come from the captured supervisor state. The implementation reviewer checks the public
process tests plus `git diff` against `contract.md`, with special attention to signal cleanup and
the unchanged `create_app` lifespan.

After review findings are fixed or dispositioned, the orchestrator runs one complete `./verify` and
records its full output. Do not run `./verify` during the slices and do not merge, restart the user's
live server, or mutate `main` from this worktree.

## Delegated implementation choices

- Use a per-user directory under `/tmp` with short port-derived socket/lease basenames; create it
  owner-only. This avoids macOS Unix-socket path limits without adding configuration.
- Use `fcntl.flock` on a retained descriptor, not PID-file truth. A stale socket may be removed only
  after the lease is acquired; a failed lease attempt touches nothing owned by the live supervisor.
- Use `start_new_session=True` only for process-group isolation. Planned shutdown signals the owned
  child PID, not its process group; FastAPI lifespan owns gateway/runtime cleanup.
- Treat every unexpected child exit, including exit zero, as supervisor failure. Only explicit
  restart and operator shutdown are planned transitions.
- Preserve the process-level e2e fixture instead of adding a test-only lifecycle API, PID endpoint,
  schema row, or alternate application composition path.
