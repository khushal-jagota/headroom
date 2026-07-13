# Backend review fixes

Scope: second leaf repair for Codex implementation-review findings 1, 2, 4, and 5.
Finding 3 was already fixed in the incoming diff and was preserved. Finding 6 remains pending.

## Changes

- Finding 1: `EmployeeStepRunner.stop(deadline=...)` now enters an explicit stopping state when admission closes. Shutdown-time gateway interruption/error settles the visible worker turn as `interrupted` and leaves the Ticket at `agent_running_step` with its existing session key. Ordinary interruption outside stop still marks the Ticket `errored`.
- Finding 2: `recover_human_turn` now validates and rolls the stale row, admits one replacement turn, starts `_run_human_turn` on a daemon thread, and returns immediately. Server startup can proceed to background loop composition while the recovered stream is blocked.
- Finding 4: human recovery validates the stale active turn's `session_key` against the authoritative entity session before rollover. Null or mismatched stale keys fail the stale turn with an explicit recovery error, create no replacement, call no gateway, and do not remint.
- Finding 5: broad `except TypeError` lifecycle fallbacks were removed. Production and unit fake stop/shutdown interfaces accept keyword-only `deadline`. The same absolute deadline is passed through runtime and routed gateways. `LiveSessionManager.shutdown` now clears `_closing`, stores the shutdown error, and signals waiters on deadline failure.

## Commands and results

1. `.venv/bin/python -m pytest ...focused new tests...`

   Result: failed to start because this isolated worktree has no `.venv/bin/python`.

   Output:

   ```text
   zsh:1: no such file or directory: .venv/bin/python
   ```

2. `/Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest ...focused new tests...`

   Result: invalid RED attempt because it imported the installed package instead of this worktree. Re-run with `PYTHONPATH="$PWD/src"`.

3. `PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest tests/unit/test_employee_step_runner.py::test_service_stop_interruption_leaves_ticket_recoverable tests/unit/test_employee_step_runner.py::test_ordinary_interruption_outside_service_stop_still_errors tests/unit/test_chat_activity.py::test_human_recovery_rejects_null_stale_turn_session_key_without_gateway tests/unit/test_chat_activity.py::test_human_recovery_rejects_mismatched_stale_turn_session_key_without_remint tests/unit/test_chat_activity.py::test_human_recovery_returns_after_admission_and_continues_in_background tests/unit/test_core_loops.py::test_server_startup_human_recovery_does_not_block_background_loops tests/unit/test_core_loops.py::test_background_stop_propagates_internal_type_error_without_retry tests/unit/test_core_loops.py::test_entity_routing_gateway_passes_same_deadline_to_each_unique_gateway`

   Result: RED, expected failures.

   Key output:

   ```text
   6 failed, 2 passed, 1 warning in 6.49s
   ```

4. Same focused command after implementation.

   Result: GREEN.

   Output:

   ```text
   8 passed, 1 warning in 0.42s
   ```

5. `PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest tests/unit/test_minds.py::test_live_session_shutdown_deadline_failure_signals_waiters_and_clears_closing`

   Result: GREEN.

   Output:

   ```text
   1 passed in 0.14s
   ```

6. `PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest tests/unit/test_chat_activity.py tests/unit/test_employee_step_runner.py tests/unit/test_core_loops.py tests/unit/test_minds.py tests/unit/test_minds_sessions.py tests/unit/test_config.py`

   First result: one race in the new startup-order test; fixed the test to wait for background settlement.

   Final result:

   ```text
   163 passed, 2 warnings in 4.00s
   ```

7. `PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m ruff check src/planner/chat/data.py src/planner/chat/service.py src/planner/core/adapters/base.py src/planner/core/adapters/fakes.py src/planner/core/adapters/real.py src/planner/core/config.py src/planner/core/loops.py src/planner/core/server.py src/planner/minds/sessions/service.py src/planner/minds/shared_gateway.py src/planner/runtime/employee_step_runner.py src/planner/runtime/ticket_readiness_loop.py tests/unit/test_chat_activity.py tests/unit/test_config.py tests/unit/test_core_loops.py tests/unit/test_employee_step_runner.py tests/unit/test_minds.py`

   First result: Ruff found one long line and one import-order issue in new tests.

   Final result:

   ```text
   All checks passed!
   ```

8. `PYTHONPATH="$PWD/src" /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m mypy src/planner/chat/data.py src/planner/chat/service.py src/planner/core/adapters/base.py src/planner/core/adapters/fakes.py src/planner/core/adapters/real.py src/planner/core/config.py src/planner/core/loops.py src/planner/core/server.py src/planner/minds/sessions/service.py src/planner/minds/shared_gateway.py src/planner/runtime/employee_step_runner.py src/planner/runtime/ticket_readiness_loop.py`

   First result: mypy required an assertion because worker recovery uses a rollover path that cannot return `None`.

   Final result:

   ```text
   Success: no issues found in 12 source files
   ```

## Not run

- `./verify` was not run, per instruction.
- Playwright was not touched or run, per instruction. Finding 6 remains pending.
