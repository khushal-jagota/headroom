# Specialist/public-flow implementation report — t_gn7x278u

## Scope completed

- Inserted paired `needs_understanding` / `understanding` immediately after Kickoff
  in the shipped `new_worker` lifecycle.
- Updated the `new_worker` specialist so the visible lifecycle is
  `Understanding → Stages → Thinking → Drafting → Closeout → Done`, with a focused
  Understanding protocol for purpose/outcome, hard judgment, and boundaries.
- Updated Chief external-work guidance so `new_worker` intake to `needs_drafting`
  carries the complete `understanding → stages → thinking` settled prefix.
- Preserved shared paired mechanics by pointing the specialist to `panels-worker`
  rather than restating chat/session/approval machinery.
- Backfilled legacy `new_worker` rows with empty missing new-worker-specific field
  slots (`understanding`, `stages`, `thinking`, `drafting`) without rewinding stages,
  ceilings, status, sessions, notes, proposals, or other field slots.
- Updated docs and focused tests for the new paired Understanding stage, durable
  Employee chat-session reuse, proposal parking, Chief external-work prefix handling,
  and manifest-driven browser progression.

## RED evidence

Command:

```text
PYTHONPATH=/Users/khushaljagota/.hermes/worktrees/planning-v2-t_gn7x278u/src /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest tests/unit/test_minds.py::test_provisioned_new_worker_skill_carries_understanding_protocol -q
```

Result before the specialist update: failed as expected because the provisioned
skill still lacked the Understanding lifecycle/protocol text.

Historical harness boundary, now resolved outside the sandbox: the first attempted
focused e2e runs in the restricted Codex harness were blocked before app assertions
by local socket binding and Chromium Mach bootstrap denial. Those were environment
failures only; the same focused e2e file and affected e2e batch passed in the final
parent-side run below.

## Final parent-side GREEN evidence

Command:

```text
PYTHONPATH=/Users/khushaljagota/.hermes/worktrees/planning-v2-t_gn7x278u/src /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest tests/unit/test_minds.py::test_provisioned_new_worker_skill_carries_understanding_protocol tests/unit/test_human_chat_turn.py::test_new_worker_understanding_chat_route_reuses_session_and_proposal_parks -q
```

Result:

```text
..                                                                       [100%]
```

Command:

```text
PYTHONPATH=/Users/khushaljagota/.hermes/worktrees/planning-v2-t_gn7x278u/src /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest tests/e2e/test_new_worker_public_flow.py -q
```

Result:

```text
..                                                                       [100%]
```

Command:

```text
PYTHONPATH=/Users/khushaljagota/.hermes/worktrees/planning-v2-t_gn7x278u/src /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest tests/unit/test_worker_type_registry.py tests/unit/test_new_worker_type.py tests/unit/test_db.py tests/unit/test_automatic_employee_step_eligibility.py tests/unit/test_automatic_employee_step_discovery_loop.py tests/unit/test_employee_step_runner.py tests/unit/test_review_ticket_decisions_type_driven.py tests/unit/test_minds.py::test_provisioned_new_worker_skill_carries_understanding_protocol tests/unit/test_human_chat_turn.py::test_new_worker_understanding_chat_route_reuses_session_and_proposal_parks -q
```

Result:

```text
.................................................................................................................................................................................................................. [100%]
210 passed
```

Command:

```text
PYTHONPATH=/Users/khushaljagota/.hermes/worktrees/planning-v2-t_gn7x278u/src /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest tests/e2e/test_chief_external_work_cli.py tests/e2e/test_new_worker_public_flow.py -q
```

Result:

```text
........                                                                 [100%]
```

Command:

```text
/Users/khushaljagota/.hermes/planning-v2/.venv/bin/ruff check tests/unit/test_minds.py tests/unit/test_human_chat_turn.py tests/e2e/test_new_worker_public_flow.py
```

Result:

```text
All checks passed!
```

Command:

```text
git diff --check
```

Result: passed with no output.

## Correction evidence

Command:

```text
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest tests/unit/test_minds.py::test_provisioned_new_worker_skill_carries_understanding_protocol tests/unit/test_minds.py::test_provisioned_chief_external_work_new_worker_example_carries_understanding_prefix -q
```

Result:

```text
..                                                                       [100%]
```

Command:

```text
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest tests/unit/test_minds.py::test_provisioned_new_worker_skill_carries_understanding_protocol tests/unit/test_human_chat_turn.py::test_new_worker_understanding_chat_route_reuses_session_and_proposal_parks -q
```

Result:

```text
..                                                                       [100%]
```

Command:

```text
.venv/bin/ruff check tests/unit/test_minds.py tests/e2e/test_new_worker_public_flow.py
```

Result:

```text
All checks passed!
```

Command:

```text
git diff --check
```

Result: passed with no output.

The correction leaf also attempted the exact e2e file in the managed Codex harness:

```text
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest tests/e2e/test_new_worker_public_flow.py -q
```

That local harness still denied `127.0.0.1` socket binding and Chromium Mach
bootstrap before the test bodies ran. This does not supersede the parent-side green
e2e evidence above.

## Accepted High review correction: historical typed `new_worker` rows

Finding: supported historical schemas can preserve `worker_type = 'new_worker'`
with coding-shaped field JSON. The first Understanding migration added only
`understanding`, so a full `create_schema()` followed by
`tickets_data.audit_ticket_registry_integrity()` could still fail on missing
`stages`, `thinking`, and `drafting`.

Correction: `_migrate_new_worker_understanding_field` now adds every missing
new-worker-specific declared slot in deterministic lifecycle order:
`understanding`, `stages`, `thinking`, `drafting`. It does not add universal
`kickoff`/`closeout`, does not reinterpret coding field values, and appends all
existing legacy top-level keys unchanged as extra data. Rows that already contain
the complete new-worker-specific set are skipped byte-for-byte; coding rows are
not selected.

RED command, replayed against this worktree's `src` with the pre-fix migrator:

```text
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_db.py::test_create_schema_backfills_historical_new_worker_coding_fields_for_audit -q
```

RED result:

```text
FAILED tests/unit/test_db.py::test_create_schema_backfills_historical_new_worker_coding_fields_for_audit
RuntimeError: ticket integrity audit failed: id=t_legacy_new_worker reason=corrupt ticket fields JSON detail={}
```

GREEN commands:

```text
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_db.py::test_create_schema_backfills_historical_new_worker_coding_fields_for_audit tests/unit/test_db.py::test_create_schema_adds_missing_new_worker_understanding_slot_idempotently -q
```

Result:

```text
..                                                                       [100%]
```

```text
PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_db.py tests/unit/test_worker_type_persistence.py tests/unit/test_new_worker_type.py -q
```

Result:

```text
91 passed, 2 warnings
```

```text
PYTHONPATH=src .venv/bin/ruff check src/planner/core/db.py tests/unit/test_db.py
```

Result:

```text
All checks passed!
```

```text
git diff --check
```

Result: passed with no output.

## Changed-file scope

- `PROGRESS.md`
- `decisions.md`
- `docs/worker-types.md`
- `skills/panels-chief-of-staff/SKILL.md`
- `skills/panels-worker-new-worker/SKILL.md`
- `src/planner/core/db.py`
- `src/planner/runtime/employee_step_runner.py`
- `src/planner/sprints/data.py`
- `src/planner/tickets/data.py`
- `src/planner/worker_types/new_worker.py`
- `tests/e2e/test_board_stage_indicators.py`
- `tests/e2e/test_chief_external_work_cli.py`
- `tests/e2e/test_new_worker_public_flow.py`
- `tests/unit/test_automatic_employee_step_discovery_loop.py`
- `tests/unit/test_automatic_employee_step_eligibility.py`
- `tests/unit/test_db.py`
- `tests/unit/test_employee_step_runner.py`
- `tests/unit/test_human_chat_turn.py`
- `tests/unit/test_minds.py`
- `tests/unit/test_new_worker_type.py`
- `tests/unit/test_review_ticket_decisions_type_driven.py`
- `tests/unit/test_worker_type_registry.py`
- `orchestration/tickets/t_gn7x278u-understanding/backend-slice-dispatch.md`
- `orchestration/tickets/t_gn7x278u-understanding/implementation-dispatch.md`
- `orchestration/tickets/t_gn7x278u-understanding/implementation-report.md`
- `orchestration/tickets/t_gn7x278u-understanding/plan-review-disposition.md`
- `orchestration/tickets/t_gn7x278u-understanding/specialist-public-flow-dispatch.md`

## Final review and canonical verification

The first canonical run exposed one stale test fixture: it directly moved a fresh
`new_worker` Ticket from paired Understanding to worker-owned Stages but retained the old
`paired_work` status. The fixture now sets the matching worker-owned resting status; the exact
Playwright case passes, and a fresh read-only Codex review reports `NO VIOLATIONS`.

The corrected tree then ran the repository's canonical verifier:

```text
PYTHONPATH=/Users/khushaljagota/.hermes/worktrees/planning-v2-t_gn7x278u/src ./verify
```

It passed Ruff, mypy across 116 source files, 823 unit tests with nine existing warnings,
compile/static/CSS checks, Svelte with zero errors and warnings, production build, all frontend
tests, and 102 Playwright tests. Every gate is `ok`; final line: `VERIFY: PASS`.

Full transcript: [verify implementation](/files/tickets/t_gn7x278u/artifacts/verify-implementation.txt)

SHA-256: `115f678308f4bf6970f5eb824a0d891e3646035a8362f32638944cd4c517e735`.

No merge, deployment, restart, or live database migration was performed during Implementation.
