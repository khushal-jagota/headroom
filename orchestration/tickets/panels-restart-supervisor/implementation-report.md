# Panels restart supervisor — implementation report

## Outcome

Implemented the reviewed contract in the isolated worktree. `panels serve` now retains
the foreground supervisor PID, owns one port-scoped lease and control socket, and runs
the existing FastAPI composition in one replaceable child process. `panels restart`
uses only the versioned Unix-socket protocol, prints acceptance only after validating
the supervisor response, and cannot choose the replacement root, interpreter, or
environment.

The stopgap commit `430cf92` remains intact. The implementation is committed on the
isolated `codex/panels-restart-supervisor` branch, which includes current `main` through
merge `ddaf5c1`. Nothing was merged into `main`, no live Panels server was stopped or
restarted, and the corrected canonical `./verify` run passes every gate.

## RED -> GREEN record

All commands ran from the worktree root with the main worktree's virtual environment.

### Slice 1 — restart is control-only

RED:

```text
PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest -q tests/unit/test_server_lifecycle_control.py
FFF                                                                      [100%]
3 failed
```

The public command was absent. The no-owner assertion reported `No such command
'restart'`, and the fake supervisors received no request.

GREEN after adding the protocol contracts, control transport, and CLI command:

```text
PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest -q tests/unit/test_server_lifecycle_control.py
...                                                                      [100%]
3 passed
```

### Slice 2 — stable foreground supervisor

Initial RED attempt:

```text
PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest -q tests/e2e/test_server_lifecycle.py -k 'supervisor or second or operator or unexpected'
FFFFFF                                                                   [100%]
6 failed
```

That attempt exposed a Darwin test-harness defect before the product assertions:
Darwin `ps` does not support `-P`. The test was corrected to read `pid,ppid` from
`ps -axo` and filter direct children itself.

GREEN after adding the application entry point, lease, signal wakeup path, child
ownership, socket cleanup, duplicate-owner rejection, and CLI serve wiring:

```text
PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest -q tests/e2e/test_server_lifecycle.py -k 'supervisor or second or operator or unexpected'
......                                                                   [100%]
6 passed
```

The existing process-shutdown regression also remained green:

```text
PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest -q tests/unit/test_server_shutdown_process.py
.                                                                        [100%]
1 passed
```

### Slice 3 — controlled replacement from captured root

The full restart path was completed with the Slice 2 lifecycle loop, so the first
focused Slice 3 run was already green rather than producing an independent RED:

```text
PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest -q tests/e2e/test_server_lifecycle.py -k 'restart or caller or generation'
....                                                                     [100%]
4 passed
```

The complete lifecycle process file then passed:

```text
PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest -q tests/e2e/test_server_lifecycle.py
.........                                                                [100%]
9 passed
```

These tests cover stable supervisor identity, acknowledgement/client-close ordering,
single-child replacement, same-socket stale-cleanup safety, port-scoped duplicate
ownership, restart from a Ticket-worktree-shaped caller, serialized generations,
operator shutdown, and unexpected child exit.

### Slice 4 — worker boundary, documentation, and focused checks

Final focused tests:

```text
PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest -q tests/unit/test_server_lifecycle_control.py tests/unit/test_server_shutdown_process.py tests/unit/test_minds.py::test_provisioned_worker_skills_do_not_let_workers_own_the_panels_server
.....                                                                    [100%]
5 passed

PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest -q tests/e2e/test_server_lifecycle.py
.........                                                                [100%]
9 passed
```

Static checks:

```text
PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/ruff check src/planner/server_lifecycle src/planner/cli/main.py tests/unit/test_server_lifecycle_control.py tests/e2e/test_server_lifecycle.py tests/e2e/conftest.py
All checks passed!

PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/mypy src/ tests/typing/
Success: no issues found in 122 source files

git diff --check
(no output; exit 0)
```

## Changed files

Production:

- `src/planner/server_lifecycle/contracts.py`
- `src/planner/server_lifecycle/control.py`
- `src/planner/server_lifecycle/supervisor.py`
- `src/planner/server_lifecycle/application.py`
- `src/planner/server_lifecycle/__init__.py`
- `src/planner/cli/main.py`

Tests:

- `tests/unit/test_server_lifecycle_control.py`
- `tests/e2e/test_server_lifecycle.py`
- `tests/e2e/conftest.py`

Live documentation:

- `docs/cli.md`
- `docs/systems.md`
- `docs/employee-runtime.md`

Ticket artifacts:

- `orchestration/tickets/panels-restart-supervisor/implementation-report.md`

The worker skill files are unchanged from stopgap commit `430cf92`; their provisioned
boundary regression is green.

## Unresolved issues

No known product, focused-test, review, or verification issue remains. The isolated
branch is ready for the user's merge instruction.

## Orchestrator integration repair

The load-bearing spot-check found that the accepted control exchange waited for client
close in a blocking receive. That preserved acknowledgement ordering, but it also delayed
operator SIGINT/SIGTERM while a compatible client deliberately stayed open.

The public process regression was RED first:

```text
PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest -q tests/e2e/test_server_lifecycle.py::test_operator_shutdown_overrides_an_accepted_client_that_stays_open
F                                                                        [100%]
1 failed
```

The accepted-client wait now observes the same signal wakeup pipe as the outer lifecycle
loop. Operator shutdown or unexpected child exit takes priority without weakening the
rule that an ordinary accepted client keeps the old child alive until it closes. The
client also validates the accepted response before closing its side of the exchange.

Corrected focused proof:

```text
PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest -q tests/e2e/test_server_lifecycle.py
..........                                                               [100%]
10 passed

PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest -q tests/unit/test_server_lifecycle_control.py tests/unit/test_server_shutdown_process.py tests/unit/test_minds.py::test_provisioned_worker_skills_do_not_let_workers_own_the_panels_server
.....                                                                    [100%]
5 passed
```

Ruff, mypy across 122 source files, and `git diff --check` remain clean after the repair.

## Two-axis review corrections

The standards review found duplicated, implementation-heavy live documentation and two
copies of the bounded control-message framing rule. The spec review found that an
incomplete request could delay operator shutdown and that a child crash already pending
beside a queued restart could be misclassified as a planned replacement.

Both lifecycle regressions were RED against the reviewed implementation:

```text
PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest -q tests/e2e/test_server_lifecycle.py::test_operator_shutdown_overrides_an_incomplete_control_request tests/e2e/test_server_lifecycle.py::test_unexpected_child_exit_wins_over_a_queued_restart
FF                                                                       [100%]
2 failed
```

The lifecycle loop now checks operator and child state before accepting ready control
work. Incomplete-request reads use the signal wakeup path, and one shared function owns
bounded newline framing for both client and supervisor. The docs now put the lifecycle
shape only in `docs/systems.md`; CLI and Employee docs keep short behavior and handoff
descriptions.

Corrected focused proof:

```text
PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest -q tests/e2e/test_server_lifecycle.py
............                                                             [100%]
12 passed

PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest -q tests/unit/test_server_lifecycle_control.py tests/unit/test_server_shutdown_process.py tests/unit/test_minds.py::test_provisioned_worker_skills_do_not_let_workers_own_the_panels_server
.....                                                                    [100%]
5 passed

PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/ruff check src/planner/server_lifecycle src/planner/cli/main.py tests/unit/test_server_lifecycle_control.py tests/e2e/test_server_lifecycle.py tests/e2e/conftest.py
All checks passed!

PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/mypy src/ tests/typing/
Success: no issues found in 122 source files
```

`git diff --check` is also clean.

## Current-main integration and closing review

The isolated branch was merged with current `main` at `d6d5d1a` without conflict. The
skill stopgap remains present after that integration. The integrated focused results are:

```text
tests/e2e/test_server_lifecycle.py
............                                                             [100%]

tests/unit/test_server_lifecycle_control.py tests/unit/test_minds.py tests/unit/test_server_shutdown_process.py
........................................................................ [ 75%]
.......................                                                  [100%]

ruff
All checks passed!

mypy
Success: no issues found in 120 source files
```

The final Standards and Spec reviews both report `NO FINDINGS`. The final read-only
Codex implementation review reports `NO VIOLATIONS`.

## Canonical verification

The first `./verify` attempt used the main worktree's borrowed virtualenv without a
source-path override. Its editable install therefore imported old `main` source while
collecting this branch's tests: the new command and package appeared absent. This was a
worktree setup failure, not a production or test failure.

The same completion gate was rerun with this worktree's `src` first on `PYTHONPATH`:

```text
PYTHONPATH="$PWD/src" ./verify
...
854 passed, 9 warnings in 19.51s
...
svelte-check found 0 errors and 0 warnings
...
114 passed in 161.44s (0:02:41)

[verify] gate ruff: ok
[verify] gate mypy: ok
[verify] gate unit suite: ok
[verify] gate build check: ok
[verify] gate frontend: ok
[verify] gate e2e suite: ok

VERIFY: PASS
```

The full transcript is `verify-final.txt`, SHA-256
`2ccfe2a325214e276abd717cc1e6116fb9d8c05cbb7ec500d5c997b7f00e5ce3`.
