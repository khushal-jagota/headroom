# Panels restart supervisor — contract

## Outcome

`panels serve` remains the foreground command the operator owns, but it becomes a
small, stable process supervisor. The FastAPI application and Employee runtime run in
one replaceable child process. `panels restart` asks the existing supervisor to replace
that child gracefully from the supervisor's captured launch root.

No caller discovers a listener PID, signals an arbitrary process, or launches a
replacement from its current worktree.

## Existing contracts to preserve

- `src/planner/cli/main.py::serve` is the public server-start command.
- `src/planner/core/server.py::create_app` owns FastAPI composition and application
  lifespan shutdown.
- `Config.shutdown_grace_seconds` remains the application's one shutdown budget.
- Restart recovery in `ChatTurnLifecycle` and `EmployeeStepRunner` remains the owner of
  interrupted human and Employee turns. The supervisor adds no second recovery model.
- `panels serve` stays attached to the invoking terminal and continues streaming the
  application's stdout and stderr there.
- Test-mode `panels serve` processes keep the existing environment-driven database,
  port, fake clock, and gateway behavior.

## Public interface

### `panels serve`

- Resolve one absolute launch root from the installed `planner` source before spawning
  anything. The caller's later working directory cannot change it.
- Hold one port-scoped lifecycle lease and one local control socket for the full command
  lifetime.
- Spawn the application in a fresh Python interpreter, with the captured launch root as
  its working directory and that root's `src` first on `PYTHONPATH`.
- Put the application child in its own process group while inheriting the supervisor's
  stdout and stderr.
- Keep the foreground supervisor PID alive across controlled restarts.
- On operator SIGINT or SIGTERM, gracefully terminate the current child, wait for it to
  finish application shutdown, clean up the control socket and lease, and then exit.
- A second supervisor for the same local port fails without signalling or replacing the
  first one.
- An unexpected application-child exit ends the supervisor with a non-zero result. This
  ticket adds no crash-retry daemon.

### `panels restart`

- Connect only to the owning supervisor's control socket. It must not inspect ports,
  processes, process groups, or PID files and must not start a server itself.
- Use `PLAN_SERVER_CONTROL_SOCKET` when present. The supervisor injects that exact value
  into the application child, so Employee terminal commands retain the right process
  owner even when their current directory is a Ticket worktree.
- Without the explicit environment value, resolve the same short, port-scoped local
  socket address as `panels serve`.
- Print `Panels restart accepted.` and exit zero only after the supervisor has accepted
  responsibility for one replacement generation.
- When no live supervisor is available, print a clear connection error to stderr, exit
  non-zero, and perform no other action.
- A malformed or incompatible control reply is an error, never permission to fall back
  to `kill` or `panels serve`.

## Restart ordering and ownership

1. The worker completes merge, verification, recap, and every other durable pre-restart
   write.
2. `panels restart` sends one versioned local control request.
3. The supervisor acknowledges the request before initiating shutdown, allowing the
   hosted terminal command to finish cleanly.
4. The supervisor sends SIGTERM only to its owned application child.
5. The existing application lifespan performs runtime and gateway shutdown.
6. The supervisor waits for that exact child to exit before spawning a replacement.
7. The replacement uses the original supervisor launch root, interpreter, environment,
   stdout, and stderr. The restart caller cannot supply any of them.
8. A fresh interpreter imports the newly merged code and startup recovery resumes any
   interrupted turns through the existing recovery contracts.

Requests accepted before the replacement spawn begins may be coalesced into that one
generation. A request accepted after a replacement spawn begins requires a later
generation. At most one application child exists at any point.

## Worker-instruction stopgap

The independently landable stopgap commit must keep both rules true:

- The shared `panels-worker` skill says the server is operator-owned and prohibits
  `kill`, `pkill`, `lsof`, direct PID signalling, replacement launch, and `panels serve`.
- `panels-worker-new-worker` makes `panels restart` the final pre-restart operation when
  available; otherwise the worker reports that restart is required and stops.

## RED-first acceptance seams

Tests observe only the public CLI/process interface and provisioned worker-skill text.

1. **No owner:** `panels restart` against an absent control socket exits non-zero, emits
   the connection error, and neither launches nor signals a process.
2. **Controlled replacement:** a real test-mode `panels serve` becomes ready; invoking
   `panels restart` prints the acceptance line; the foreground supervisor PID remains;
   the application child PID changes; and the same HTTP endpoint becomes ready again.
3. **Caller isolation:** the restart command may run from a different temporary working
   directory while the replacement still serves the launch root's application and
   built frontend.
4. **Single generation:** the old application child exits before the replacement is
   spawned, and no second supervisor can take ownership while the first is alive.
5. **Operator shutdown:** terminating the foreground supervisor gracefully removes its
   child and control socket, leaving no orphan that still listens on the configured
   port.
6. **Skill boundary:** the provisioned shared and new-worker skills contain the exact
   server-ownership and fallback rules.

Focused tests run during implementation. One complete `./verify` run is the final
completion claim after review corrections.

## Contract-scoped files

- `src/planner/server_lifecycle/contracts.py`
- `src/planner/server_lifecycle/control.py`
- `src/planner/server_lifecycle/supervisor.py`
- `src/planner/server_lifecycle/application.py`
- `src/planner/server_lifecycle/__init__.py`
- `src/planner/cli/main.py`
- focused unit/process tests under `tests/unit/` and `tests/e2e/`
- `skills/panels-worker/SKILL.md`
- `skills/panels-worker-new-worker/SKILL.md`
- `docs/cli.md`, `docs/systems.md`, and `docs/employee-runtime.md` only where their live
  lifecycle description changes
- this ticket's plan, review, and implementation artifacts
- `PROGRESS.md` and `decisions.md`

No database schema, HTTP route, Worker-type contract, frontend source, or built frontend
artifact belongs to this ticket.

## Explicit exclusions

- No `launchd`, daemon installation, backgrounded replacement shell, PID-file restart,
  in-process module reload, hot-reload registry, or automatic crash retry.
- No activity-lease/quiescence subsystem. Existing graceful settlement and restart
  recovery own interrupted work.
- No promise that same-Unix-user code is a hostile security sandbox. The controlled
  command and worker rules remove the sanctioned accidental path; OS identity separation
  is separate work.
- No Git branch mutation, merge into `main`, deployment, or restart of the user's live
  server from this implementation worktree.
