# Backend/migration implementation dispatch

Implement `contract.md` using strict vertical TDD. Read AGENTS.md, PRINCIPLES.md, D55, plan-review.md, and ownership.md first.

## Ownership

You may edit `src/planner/**`, `tests/unit/**` except `tests/unit/test_minds.py`, and backend-only CLI e2e files (`tests/e2e/test_chief_external_work_cli.py`, `tests/e2e/test_cli_verbs.py`). Do not edit `web/**`, `skills/**`, `docs/**`, PROGRESS.md, decisions.md, or unrelated worktree pointers. Do not commit.

## Required behavior

- Five fields and six linear states exactly as contract.md defines; Done has no field.
- Both post-plan states use the existing generic gate/proposal/status/scope machinery. Delete obsolete Review-only approval and revised-result special cases rather than retaining dead compatibility APIs.
- Generic return-for-revision works for pending Implementation and Closeout proposals and still delivers guidance through the employee runner.
- Readiness, queues, views, sprint projections, seed/import, API, CLI, runtime prompts, adapters/fakes, and event behavior use the new contract. Preserve valid non-Ticket uses of words such as sprint-item `in_progress` and generic function results.
- Strict Chief external-work create/reconcile accepts exact settled prefixes: 0/1/2/3/4/5 values at Success/Approach/Plan/Implementation/Closeout/Done.
- Schema migration updates all DDL/rebuild paths. Map legacy result to implementation and add closeout. Idle needs_review rows become needs_closeout. Any needs_review row with non-empty ticket_status becomes needs_implementation. For awaiting_approval, reconstruct a pending implementation proposal from the stored result body, preserve its note/body, and cap the ceiling at needs_implementation so human approval is still required. Preserve all other ticket data and prove idempotence, foreign keys, fresh DB, current-schema old lifecycle, and old project-column rebuild.

## TDD and evidence

For each vertical slice, write a focused test first and run it to a decisive expected RED before production changes, then rerun GREEN. Start with contract/engine, then migration, external-work/API/CLI, runtime/queues/revision, and seed/projections. Run focused backend suites, Ruff on owned Python, Mypy, and `git diff --check`; do not run `./verify`.

Write the exact RED/GREEN commands, decisive RED lines, changed files, and remaining risks to `orchestration/tickets/t_p6de8rje-lifecycle/backend-report.md` before stopping.
