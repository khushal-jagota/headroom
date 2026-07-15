# Closeout correction report

## RED evidence

- `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_automatic_employee_step_eligibility.py -q`
  failed after adding paired-opening eligibility coverage: 11 failures. The failures showed
  `new_worker.needs_understanding`, explicit paired ownership, `paired_work` compatibility,
  later paired-stage compatibility, and the paired one-factor-false matrix were all still
  treated as ineligible by the old worker-only branch.
- `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_employee_step_runner.py::test_paired_opening_runs_once_then_rests_in_paired_work tests/unit/test_employee_step_runner.py::test_later_paired_opening_reuses_existing_employee_session_once tests/unit/test_employee_step_runner.py::test_paired_opening_gateway_busy_releases_claim_without_immediate_wake -q`
  failed 3/3. The prompt still demanded an immediate proposal for paired stages, and
  `SharedGatewayBusy` settled the paired opening as `paired_work` instead of releasing it
  back to `empty`.
- Parent expanded RED before these corrections:
  `PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q tests/unit/test_automatic_employee_step_eligibility.py tests/unit/test_automatic_employee_step_discovery_loop.py tests/unit/test_employee_step_runner.py tests/unit/test_automatic_employee_step_eligibility_actions.py tests/unit/test_human_chat_turn.py tests/e2e/test_new_worker_public_flow.py`
  failed with three unit failures and two e2e failures. The unit failures were the stale
  `test_new_worker_paired_understanding_is_not_automatically_dispatched`, plus human chat tests that
  expected `paired_work` immediately after entering paired instead of establishing a completed
  automatic opening first. The e2e tests were waiting for an automatic loop that the server
  deliberately does not compose in test mode (`server.py:181`).

## GREEN evidence

- `PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_automatic_employee_step_eligibility.py tests/unit/test_employee_step_runner.py tests/unit/test_automatic_employee_step_eligibility_actions.py -q`
  passed: 126 tests passed, with one existing `StarletteDeprecationWarning`.
- Focused ownership/external-work/eligibility regressions passed:
  `PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q tests/unit/test_stage_ownership_backend.py tests/unit/test_chief_external_work.py tests/unit/test_automatic_employee_step_eligibility.py`
  passed: 78 tests passed, with one existing `StarletteDeprecationWarning`.
- Broader corrected unit set passed:
  `PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q tests/unit/test_stage_ownership_backend.py tests/unit/test_chief_external_work.py tests/unit/test_automatic_employee_step_eligibility.py tests/unit/test_automatic_employee_step_discovery_loop.py tests/unit/test_human_chat_turn.py tests/unit/test_automatic_employee_step_eligibility_actions.py`
  passed: 139 tests passed, with existing `StarletteDeprecationWarning` and `TestClock` collection warnings.
- Parent unit portion passed:
  `PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q tests/unit/test_automatic_employee_step_eligibility.py tests/unit/test_automatic_employee_step_discovery_loop.py tests/unit/test_employee_step_runner.py tests/unit/test_automatic_employee_step_eligibility_actions.py tests/unit/test_human_chat_turn.py`
  passed: 167 tests passed, with existing `StarletteDeprecationWarning` and `TestClock` collection warnings.
- The expanded parent command now passes all selected unit and e2e tests. The e2e tests invoke the real
  `EmployeeStepRunner` with the shared fake gateway against the test server's SQLite database, then
  continue through the server's public Ticket Chat and browser surfaces from the durable opening session.
- `PYTHONPATH=src .venv/bin/ruff check src/planner/runtime/automatic_employee_step_eligibility.py src/planner/runtime/employee_step_runner.py src/planner/tickets/data.py src/planner/tickets/actions.py tests/unit/test_automatic_employee_step_eligibility.py tests/unit/test_employee_step_runner.py tests/unit/test_automatic_employee_step_eligibility_actions.py tests/e2e/test_new_worker_public_flow.py`
  passed: `All checks passed!`
- `PYTHONPATH=$PWD/src .venv/bin/ruff check src/planner/runtime/automatic_employee_step_eligibility.py src/planner/runtime/employee_step_runner.py src/planner/tickets/actions.py src/planner/tickets/data.py tests/e2e/test_new_worker_public_flow.py tests/unit/test_automatic_employee_step_discovery_loop.py tests/unit/test_automatic_employee_step_eligibility.py tests/unit/test_automatic_employee_step_eligibility_actions.py tests/unit/test_chief_external_work.py tests/unit/test_employee_step_runner.py tests/unit/test_human_chat_turn.py tests/unit/test_stage_ownership_backend.py`
  passed: `All checks passed!`
- `git diff --check` passed with no output.
- Stale wording search passed with no matches:
  `rg -n 'paired.*never|never.*paired|paired Stages continue through ordinary|paired Stages rest|user-owned and paired Stages|automatic dispatch does not|are never dispatched automatically' docs skills src tests`.

## E2E evidence

- `PYTHONPATH=$PWD/src .venv/bin/python -m pytest -q tests/e2e/test_new_worker_public_flow.py`
  passed: 2 tests passed. The opening uses the production runner path because the e2e server intentionally
  omits background loops in test mode; subsequent user chat reuses `fake-sess-1` through the public API.

## Implementation notes

- No schema flag was added. Paired opening eligibility uses current Ticket state plus
  `events`/`chat_turn_started` history.
- Entering a worker- or paired-owned Stage writes `empty`; user-owned entry writes
  `user_takeover`. Successful paired opening completion still rests at `paired_work`.
- Reconciliation at the current Stage keeps the resting-status path; only a real Stage change uses
  entered status. Drop also stays on resting status.
- Ownership events now include `previous_effective_ownership_mode`. Same-effective paired events with
  `previous_effective_ownership_mode = "paired"` are not compatibility markers; historical paired events
  without that key still are markers.
- Ownership override writes persist any changed explicit override map, including same-effective set/clear.
  Future-Stage overrides do not alter current status, and same-effective current-Stage changes preserve
  current status.
- `SharedGatewayBusy` releases `agent_running_step` back to `empty` and returns without
  the runner wake, leaving retry to periodic discovery.
- Independent review found that non-gating proposal acceptance could leave an unchanged paired Stage at
  entered status (`empty`) and reopen it. Acceptance now chooses entered status only when the decision
  advances Stage; `test_accept_non_gating_proposal_keeps_opened_paired_stage_resting` proves an unchanged
  paired Stage returns to `paired_work`.
- Corrected review found that a later silently parked paired Stage could remain blocked when its durable
  session came from earlier ordinary Ticket Chat rather than an earlier worker step. Compatibility now
  asks only whether a worker-step opening started after the current Stage marker. The later-stage test uses
  an earlier human turn and proves that session still receives its one opening.
- Final corrected independent review reported `NO VIOLATIONS` across production behavior, compatibility,
  concurrency/status semantics, tests, docs, decisions, progress, and the public-flow harness.
