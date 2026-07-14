# Architecture deepening × restart recovery implementation

## Outcome

The conflict resolution keeps architecture head `9e51df2` as the public and domain
shape while retaining restart-recovery and bounded-shutdown behavior from current main
`49660f5`.

- Ordinary human Chat and stranded Employee steps recover through their architecture
  owners after both role gateways are ready and before automatic discovery starts.
- Recovery strictly resumes the stored identity, never replays the original input, and
  never creates a replacement Hermes session. Partial visible output is preserved.
- Employee recovery reads an already-running Ticket. It does not claim again, run
  Automatic Employee-step eligibility, or rebuild the ordinary step prompt.
- A stale worker turn whose Ticket has already left `agent_running_step` is settled
  without another Employee prompt.
- Chat rollover uses the same canonical first-wins settlement implementation as every
  other terminal Chat transition.
- One absolute monotonic deadline flows through discovery, the Employee runner, routed
  gateways, live-session cleanup, and child cleanup. Shutdown interruption leaves the
  Ticket and `employee_session_id` recoverable.
- Required immutable Worker type, direct stored Stage reads, the complete eight-factor
  eligibility decision, typed human observations, Panels-owned Chat state, and explicit
  Ticket-only Employee session history remain intact. No readiness compatibility name,
  Ticket Chat identity, retired route, generic Chat result bag, or live Worker-type
  default was restored.

## Integration paths

The reviewed conflict paths were resolved in:

- `PROGRESS.md`, `docs/employee-runtime.md`, and `docs/systems.md`
- `src/planner/chat/service.py`
- `src/planner/core/adapters/base.py`, `fakes.py`, and `real.py`
- `src/planner/core/loops.py`
- `src/planner/minds/shared_gateway.py`
- `src/planner/runtime/employee_step_runner.py`
- `tests/unit/test_chat_activity.py`, `test_core_loops.py`, and `test_minds.py`

The required auto-merge repairs were made in:

- `src/planner/chat/data.py`: one non-owning in-transaction terminal settlement now
  serves both public settlement and atomic restart rollover.
- `src/planner/core/server.py`: startup calls the composed `ChatTurnLifecycle` and keeps
  gateway-ready → human recovery → Employee recovery → discovery ordering.
- `src/planner/runtime/automatic_employee_step_discovery_loop.py`: the auto-merged
  deadline-aware stop remains the sole AD03 signature supersession.
- `tests/unit/test_employee_step_runner.py`: restart proofs use the canonical automatic
  claim, eligibility wake, Worker type, and `EmployeeSessionIdTransition` interfaces.
- `tests/e2e/test_live_chat_state.py`: the restart fixture uses explicit Worker type and
  canonical Employee identity assignment while retaining internal Chat-turn identity.

## Additional repair paths

Each path outside the declared conflict/automatic-repair list has a concrete integration
reason:

- `tests/unit/test_human_chat_turn.py` locks the exact admitted value, lifecycle surface,
  normal/strict flag values, and typed gateway signature added by this merge.
- `tests/unit/test_chat_images.py`, `test_chat_commands.py`, and `test_chat_seed.py`
  update their typed gateway doubles for the keyword-only strict-resume choice. Their
  image, command, causal-binding, activity, and first-wins behavior is unchanged.
- `tests/unit/test_chat_ingress_contract.py` updates the surviving exact-interface lock
  for that same strict-resume keyword.
- `tests/unit/test_automatic_employee_step_eligibility_wake.py` receives an import-only
  Ruff repair exposed by the combined tree; executable behavior is unchanged.

## Focused verification

All commands used the merge worktree with `PYTHONPATH="$PWD/src"` where applicable.

- `python -m compileall -q src/planner` — pass.
- Required focused unit set plus the affected Chat image, command, and seed suites —
  pass. This covers Chat rollover/strict continuation, Employee recovery, loop ordering,
  gateway deadlines, session history, complete eligibility, discovery, eligibility
  wake, and config.
- `tests/unit/test_chat_ingress_contract.py`,
  `tests/unit/test_worker_type_stage_contracts.py`, and the three focused v20/default
  migration locks — 13 passed.
- `ruff check src tests/unit tests/e2e/test_live_chat_state.py` — pass.
- `mypy src` — success across 113 source files. The project gate type-checks source;
  tests were exercised rather than presented as mypy-clean because the repository's
  test tree has pre-existing typing diagnostics outside this integration.
- Playwright-backed pytest cases
  `test_startup_recovery_preserves_partial_chat_and_continues_without_duplicate_input`
  and `test_ticket_panels_rows_do_not_fall_back_to_employee_session_history` — 2 passed.
- `git diff 9e51df2 --check` — pass for the integration delta, and the staged merge is
  clean when the byte-identical reviewed-parent generated bundle is excluded. A raw
  first-parent staged check reports one inherited false positive in
  `web/dist/assets/index-CsT3n4Se.js`: a Svelte runtime template-literal line ends with
  intentional whitespace. The worktree blob exactly matches architecture parent
  `9e51df2` (`4b3bd83e`), so it was not rewritten during conflict resolution.
- Exact conflict-marker, retired runtime, retired human transport, and forbidden Ticket
  identity scans — no live matches. Structural deletion/interface tests also pass.
- The live Worker-type scan found only the `coding` definition/imports, required CLI
  examples, the existing coding-specific sprint rollup, and the terminal historical
  migration assignment. It found no Worker-type parameter default, fallback, registry
  order selection, or Stage inference.
- `git ls-files -u` — empty after staging the conflict resolutions.

The full canonical `./verify` was intentionally not run before independent read-only
integration review, as required by the integration plan.

## Independent review repair

The fresh merge-diff review found one High omission in the no-Employee-session restart
branch: the canonical Ticket writer marked the still-running Ticket errored, but its
stale worker-origin Chat turn remained active. The accepted repair is limited to:

- `src/planner/runtime/employee_step_runner.py`: before the existing Ticket error
  transition, read the active worker-step turn and settle it through
  `chat_data.settle_chat_turn` as `errored`, using the exact error
  `restart recovery has no existing Employee session`. The branch still creates no
  replacement turn and calls no gateway.
- `tests/unit/test_employee_step_runner.py`: seed `agent_running_step`, no
  `employee_session_id`, and a stale worker turn; prove the Ticket and turn carry the
  error, no active turn remains, and the gateway receives no call.

Post-repair evidence:

- The new focused regression — 1 passed.
- `pytest tests/unit/test_employee_step_runner.py tests/unit/test_core_loops.py -q` —
  pass.
- Ruff on both changed Python files — pass.
- mypy on `src/planner/runtime/employee_step_runner.py` — pass.
- compileall on `src/planner/runtime/employee_step_runner.py` — pass.
- `git diff --check` for the repair — pass.

No full `./verify` was run for this review repair.

## Post-merge gate repair

The first canonical gate on merge commit `2384159` ran every suite and reported one
Ruff-only failure: the combined import block in
`tests/typing/tt02b_field_seam_cases.py` was not ordered. Mypy passed across 115 source
files, 760 unit tests passed, the complete frontend gate passed, and 88 Playwright tests
passed. Ruff applied its import-only ordering fix; executable tests and production code
were unchanged. This is trivial integration glue handled directly by the orchestrator.
