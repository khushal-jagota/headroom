# t_arch01 implementation report

## Result

Implemented the reviewed employee-runtime ownership plan from fixed base
`2a4f56cd9b61977de174384dcfeedfdd46bf0e76`.

- `TicketReadinessLoop` now owns optional today-board discovery only.
- `EmployeeStepRunner` is composed whenever the worker gateway is composed and owns
  automatic step eligibility, prompt construction, worker Chat, Hermes delivery,
  settlement, and direct Review revisions.
- Review revision is now `reserve -> canonical Ticket transaction -> release`, with
  cancellation on transaction failure.
- Revision resumes the stored Hermes session strictly. A missing stored session cannot
  create a replacement and settles asynchronously as errored.
- Runner shutdown closes admission and drains accepted automatic runs and parked/released
  revisions before gateway shutdown.

## Files changed

Runtime contracts and implementation:

- added `src/planner/runtime/contracts.py`
- renamed `src/planner/runtime/system_a.py` to
  `src/planner/runtime/ticket_readiness_loop.py`
- renamed `src/planner/runtime/system_b.py` to
  `src/planner/runtime/employee_step_runner.py`
- updated `src/planner/runtime/__init__.py`, `readiness.py`, and `lock.py`
- updated `src/planner/core/loops.py`, `server.py`, and `testmode.py`

Ticket and gateway seams:

- added `src/planner/tickets/actions.py`
- updated the revision seam in `src/planner/tickets/api.py` and `data.py`
- updated `src/planner/minds/shared_gateway.py` only with the reviewed
  `require_existing_session` / no-create path
- renamed the temporary readiness-poke dependency in `src/planner/tickets/api.py` and
  `src/planner/days/api.py`; its behavior is intentionally unchanged for t_arch02

Tests:

- renamed `tests/unit/test_system_a.py` to
  `tests/unit/test_ticket_readiness_loop.py`
- renamed `tests/unit/test_system_b.py` to
  `tests/unit/test_employee_step_runner.py`
- updated `test_return_for_revision.py`, `test_core_loops.py`, `test_minds.py`, and live
  terminology in the existing poke/e2e tests

Live naming, documentation, and build memory:

- updated `AGENTS.md`, `CLAUDE.md`, `PROGRESS.md`, `config.yaml`,
  `docs/employee-runtime.md`, `docs/systems.md`, `docs/systems.html`, and `docs/chat.md`

## RED evidence

Command:

```sh
.venv/bin/pytest -q tests/unit/test_return_for_revision.py -k without_employee_runner tests/unit/test_core_loops.py tests/unit/test_minds.py -k 'without_employee_runner or dispatch_disabled or strict_existing_session or default_resume_not_found'
```

Output after correcting the test fixture's event ordering column from the nonexistent
`seq` to the real public event id:

```text
FFF.                                                                     [100%]
=================================== FAILURES ===================================
_ test_return_for_revision_without_employee_runner_is_503_and_changes_nothing __

>       assert response.status_code == 503
E       assert 200 == 503
E        +  where 200 = <Response [200 OK]>.status_code

_ test_dispatch_disabled_keeps_employee_runner_without_acquiring_polling_lock __

>           assert handle.employee_step_runner is not None
E           AttributeError: 'BackgroundLoops' object has no attribute 'employee_step_runner'

_ test_shared_gateway_strict_existing_session_does_not_create_submit_or_consume_context _

>           result = gateway.run_ticket_step(
E           TypeError: SharedGateway.run_ticket_step() got an unexpected keyword argument 'require_existing_session'

=========================== short test summary info ============================
FAILED tests/unit/test_return_for_revision.py::test_return_for_revision_without_employee_runner_is_503_and_changes_nothing
FAILED tests/unit/test_core_loops.py::test_dispatch_disabled_keeps_employee_runner_without_acquiring_polling_lock
FAILED tests/unit/test_minds.py::test_shared_gateway_strict_existing_session_does_not_create_submit_or_consume_context
```

Complete captured RED output (including the temporary paths from that run):

```text
FFF.                                                                     [100%]
=================================== FAILURES ===================================
_ test_return_for_revision_without_employee_runner_is_503_and_changes_nothing __

tmp_path = PosixPath('/private/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/pytest-of-khushaljagota/pytest-1035/test_return_for_revision_witho0')

    def test_return_for_revision_without_employee_runner_is_503_and_changes_nothing(
        tmp_path: Path,
    ) -> None:
        app, db_path = _make_app(tmp_path)
        tid = _ticket_with_pending_plan(db_path)
        app.state.employee_step_runner = None

        conn = connect(str(db_path))
        try:
            ticket_before = conn.execute("SELECT * FROM tickets WHERE id = ?", (tid,)).fetchone()
            events_before = conn.execute(
                "SELECT * FROM events WHERE entity_id = ? ORDER BY id", (tid,)
            ).fetchall()
            context_before = conn.execute(
                "SELECT * FROM pending_worker_context WHERE worker_entity_id = ? ORDER BY context_key",
                (tid,),
            ).fetchall()
            turns_before = conn.execute(
                "SELECT * FROM chat_turns WHERE entity_id = ? ORDER BY id", (tid,)
            ).fetchall()
            messages_before = conn.execute(
                "SELECT * FROM chat_messages WHERE entity_id = ? ORDER BY id", (tid,)
            ).fetchall()
        finally:
            conn.close()

        with TestClient(app) as client:
            response = client.post(
                f"/api/tickets/{tid}/return-for-revision",
                json={"message": "Do not lose this guidance."},
            )

>       assert response.status_code == 503
E       assert 200 == 503
E        +  where 200 = <Response [200 OK]>.status_code

tests/unit/test_return_for_revision.py:239: AssertionError
_ test_dispatch_disabled_keeps_employee_runner_without_acquiring_polling_lock __

tmp_path = PosixPath('/private/var/folders/m1/ghygg_r133nc05srgprf9j5c0000gn/T/pytest-of-khushaljagota/pytest-1035/test_dispatch_disabled_keeps_e0')
fake_clock = <planner.core.clock.TestClock object at 0x108a75a90>
monkeypatch = <_pytest.monkeypatch.MonkeyPatch object at 0x10889d940>

    def test_dispatch_disabled_keeps_employee_runner_without_acquiring_polling_lock(
        tmp_path: Path,
        fake_clock: TestClock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        lock_path = tmp_path / "dispatcher.lock"
        config = load_config(
            path=None,
            env={
                "PLAN_DISPATCH_ENABLED": "0",
                "PLAN_DB_PATH": str(tmp_path / "planning.db"),
                "PLAN_DISPATCHER_LOCK_PATH": str(lock_path),
            },
        )

        def fail_if_called(path: str) -> bool:
            raise AssertionError(f"lock should not be acquired when dispatch is disabled: {path}")

        monkeypatch.setattr(loops, "ensure_machine_lock", fail_if_called)
        handle = loops.start_background_loops(
            config,
            fake_clock,
            shared_gateway=cast(SharedGateway, object()),
        )
        try:
>           assert handle.employee_step_runner is not None
                   ^^^^^^^^^^^^^^^^^^^^^^^^^^^
E           AttributeError: 'BackgroundLoops' object has no attribute 'employee_step_runner'

tests/unit/test_core_loops.py:40: AttributeError
_ test_shared_gateway_strict_existing_session_does_not_create_submit_or_consume_context _

    def test_shared_gateway_strict_existing_session_does_not_create_submit_or_consume_context() -> None:
        context = RecordingWorkerContext()
        context.set("t_demo", PendingWorkerContext("ticket_changed", "Ticket changed.", 3))
        fake = FakeGateway(
            {
                "session.resume": [Reply(error=(4007, "stored session not found"))],
                "session.create": [create_reply()],
                "prompt.submit": [submit_reply(complete_ev())],
            }
        )
        gateway = shared(fake, context)
        try:
>           result = gateway.run_ticket_step(
                STORED_KEY,
                "t_demo",
                "revision guidance",
                require_existing_session=True,
            )
E           TypeError: SharedGateway.run_ticket_step() got an unexpected keyword argument 'require_existing_session'

tests/unit/test_minds.py:652: TypeError
=============================== warnings summary ===============================
.venv/lib/python3.14/site-packages/fastapi/testclient.py:1
  /Users/khushaljagota/.hermes/planning-v2/.venv/lib/python3.14/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

src/planner/core/clock.py:27
  /Users/khushaljagota/.hermes/planning-v2/src/planner/core/clock.py:27: PytestCollectionWarning: cannot collect test class 'TestClock' because it has a __init__ constructor (from: tests/unit/test_core_loops.py)
    class TestClock:

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
FAILED tests/unit/test_return_for_revision.py::test_return_for_revision_without_employee_runner_is_503_and_changes_nothing
FAILED tests/unit/test_core_loops.py::test_dispatch_disabled_keeps_employee_runner_without_acquiring_polling_lock
FAILED tests/unit/test_minds.py::test_shared_gateway_strict_existing_session_does_not_create_submit_or_consume_context
```

Why RED was correct:

- the old route returned HTTP 200 and mutated the Ticket when no runtime owner existed;
- dispatch-off returned no employee runner because runner construction was incorrectly
  behind readiness polling and its lock;
- the old gateway had no strict stored-session option and would create a replacement
  session after resume-not-found;
- the companion default-fallback regression already passed, proving that the new strict
  test was not asking to remove ordinary automatic fallback behavior.

## GREEN evidence

Focused unit command:

```sh
.venv/bin/pytest -q \
  tests/unit/test_return_for_revision.py \
  tests/unit/test_employee_step_runner.py \
  tests/unit/test_ticket_readiness_loop.py \
  tests/unit/test_core_loops.py \
  tests/unit/test_minds.py \
  tests/unit/test_worker_context.py \
  tests/unit/test_request_identity.py \
  tests/unit/test_chief_external_work.py \
  tests/unit/test_chat_images.py
```

Full summary:

```text
........................................................................ [ 49%]
........................................................................ [ 99%]
.                                                                        [100%]
=============================== warnings summary ===============================
.venv/lib/python3.14/site-packages/fastapi/testclient.py:1
  StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.

src/planner/core/clock.py:27
  PytestCollectionWarning: cannot collect test class 'TestClock' because it has a __init__ constructor (from: tests/unit/test_ticket_readiness_loop.py)

src/planner/core/clock.py:27
  PytestCollectionWarning: cannot collect test class 'TestClock' because it has a __init__ constructor (from: tests/unit/test_core_loops.py)
```

Focused browser command:

```sh
.venv/bin/pytest -q tests/e2e/test_flows_a.py -k return_for_revision
```

Full output:

```text
.                                                                        [100%]
```

Static commands and output:

```sh
.venv/bin/ruff check src/planner/runtime src/planner/core/loops.py src/planner/core/server.py src/planner/core/testmode.py src/planner/tickets/actions.py src/planner/tickets/api.py src/planner/tickets/data.py src/planner/days/api.py src/planner/minds/shared_gateway.py tests/unit/test_employee_step_runner.py tests/unit/test_ticket_readiness_loop.py tests/unit/test_core_loops.py tests/unit/test_return_for_revision.py tests/unit/test_minds.py tests/unit/test_day_api.py tests/unit/test_value_edit_api.py tests/unit/test_ticket_delete.py tests/unit/test_chief_external_work.py tests/e2e/test_cli_verbs.py
```

```text
All checks passed!
```

```sh
.venv/bin/mypy src/planner
```

```text
Success: no issues found in 99 source files
```

`git diff --check` passed. The required live naming search returned no matches:

```sh
rg -n 'System A|System B|SystemA|SystemB|system_a|system_b' \
  src tests docs AGENTS.md CLAUDE.md config.yaml
```

`./verify` was intentionally not run inside this ticket. The orchestrator owns the one
authoritative full run after all three architecture tickets integrate.

## Independent implementation review resolution

The independent implementation review reported five valid gaps. All five were accepted and
resolved:

1. Replaced the deleted System A/B names in live root guidance (`CLAUDE.md`) with
   `TicketReadinessLoop` and `EmployeeStepRunner` and their actual ownership split.
2. Updated `PROGRESS.md` to record t_arch01's implemented/reviewed state without claiming that
   t_arch02 or t_arch03 has begun or that a full `./verify` has run.
3. Added a released real-revision shutdown test. It reaches a blocking fake gateway, proves
   `stop()` rejects new reservations while waiting, then proves the runner drains only after the
   gateway completes.
4. Added a stale automatic-discovery test that leaves the Ticket on today, changes its scope to
   make `readiness.is_runnable` false, and proves there is no claim, prompt, or status event.
5. Replaced the unusable placeholder gateway in the readiness-loop construction-failure test
   with a real `SharedGateway` over `FakeGateway`, then proved the retained runner can reserve,
   cancel, and drain direct work.

Review-proof test commands and results:

```sh
.venv/bin/pytest -q tests/unit/test_employee_step_runner.py \
  -k 'rechecks_readiness_predicate or stop_waits_for_released'
```

```text
..                                                                       [100%]
```

```sh
.venv/bin/pytest -q tests/unit/test_core_loops.py \
  -k readiness_loop_start_failure
```

```text
.                                                                        [100%]
```

The complete focused unit gate was then rerun:

```sh
.venv/bin/pytest -q \
  tests/unit/test_return_for_revision.py \
  tests/unit/test_employee_step_runner.py \
  tests/unit/test_ticket_readiness_loop.py \
  tests/unit/test_core_loops.py \
  tests/unit/test_minds.py \
  tests/unit/test_worker_context.py \
  tests/unit/test_request_identity.py \
  tests/unit/test_chief_external_work.py \
  tests/unit/test_chat_images.py
```

```text
........................................................................ [ 48%]
........................................................................ [ 97%]
...                                                                      [100%]
```

This is 147 passing focused unit tests. The same three pre-existing warnings remain: one
Starlette `httpx` deprecation warning and two pytest collection warnings for imported
`TestClock`. The Ruff and Mypy commands recorded above were rerun unchanged after these
follow-up edits and again returned `All checks passed!` and
`Success: no issues found in 99 source files`. Both `git diff --check` and
`git diff --cached --check` returned no output, and the updated live-name search returned no
matches. Follow-up independent review remains the orchestrator's next gate.

## Contract checklist

- [x] Runner construction is independent of dispatch flag and polling lock.
- [x] Readiness polling remains optional and singleton-lock guarded.
- [x] `TicketReadinessLoop.poll_once()` calls only `run_ready_step(ticket_id)`.
- [x] `EmployeeStepRunner` builds the next-step prompt and checks both current
  `is_runnable` and current membership on today's board inside the claim transaction.
- [x] Human guidance syntax is validated before reservation.
- [x] Direct revision reserves a counted, parked thread before canonical mutation.
- [x] Transaction failure calls idempotent cancellation and submits no Hermes prompt.
- [x] Commit is followed by idempotent release; HTTP success never depends on readiness
  polling or completion of the async Hermes turn.
- [x] Missing, stopped, and gateway-unavailable runners return `gateway_offline` before
  Ticket, event, pending-context, or Chat changes.
- [x] Revision delivery uses the existing Hermes session, exact framing,
  `show_prompt_in_chat=False`, and no Panels guidance copy.
- [x] Strict resume-not-found never calls `session.create`, `prompt.submit`, context
  prepare, context acknowledge, or session-key reminting.
- [x] Default automatic resume-not-found still falls back to create and submit.
- [x] A pre-existing running Chat turn is rejected in the canonical revision transaction
  and leaves every snapshotted durable surface unchanged.
- [x] A post-commit worker-turn collision submits no prompt, preserves the winning human
  turn, and moves the claimed Ticket to `errored`.
- [x] Runner stop closes admission, blocks on accepted parked and released gateway-bound work,
  and drains both.
- [x] Runtime stop order is readiness loop, employee runner, polling lock; server shutdown
  awaits runtime stop before closing worker and Chief gateways.
- [x] Test mode uses only `TestModeAcceptingEmployeeRevisionRunner`; the separate real
  runner + fake SharedGateway FastAPI test proves actual same-session delivery.
- [x] Existing proposal settlement, auto-accept, busy, interruption, worker Chat,
  session-ownership-loss, crash, pending-context, Chief intake, request identity, and
  chat-image regressions remain green.

## Exclusions audit

- No schema, migration, database object, event kind, Ticket state, or Ticket status added.
- No durable reservation, queue, retry, crash recovery, IPC, or cross-process doorbell added.
- No frontend source or build output changed.
- No Chief external-work writer/route behavior changed.
- No worker-context producer, storage, prepare, or acknowledgement behavior changed.
- No chat-image behavior changed.
- No SharedGateway completion-correlation or composition redesign was attempted. The
  gateway diff is limited to the reviewed strict-existing-session keyword/path.
- No candidate 4, candidate 6, t_arch02 readiness-doorbell, or t_arch03 atomic PATCH work
  was implemented.

## Unresolved issues

None in scope. The two warnings in focused pytest output are pre-existing tooling warnings:
Starlette's `httpx` deprecation notice and pytest seeing the imported `TestClock` class name.
