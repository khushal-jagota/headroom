# Backend slice dispatch — t_gn7x278u

You are the leaf implementer. Edit directly; do not delegate. Read `implementation-dispatch.md` and `plan-review-disposition.md`, then implement only the backend lifecycle and migration slice with strict RED → GREEN TDD.

Own:

- `src/planner/worker_types/new_worker.py`
- `src/planner/core/db.py`
- directly affected backend source only when a focused failing test proves it is necessary
- backend/unit/e2e tests for exact manifest/order/default ownership, `first_worker_stage`, external-work/sprint consequences, paired automatic eligibility/proposal parking, and migration preservation/idempotence/startup integrity

Requirements:

1. `needs_understanding` / `understanding` is immediately after Kickoff, default ownership `paired`, and is `new_worker.first_worker_stage()`.
2. Existing later lifecycle order and behavior remain unchanged.
3. Add an idempotent canonical schema migration that inserts only a default empty `understanding` slot into existing `new_worker` rows missing it, before startup integrity audit. Preserve every other slot and Ticket column; leave coding/already-migrated rows unchanged.
4. Reuse shared paired ownership/readiness/proposal mechanics. Add no custom loop/status/chat mechanism.
5. Update exact comments/goldens/fixtures affected by the deliberate first-stage change.

Run each named focused test to meaningful RED before production edits, rerun to GREEN, then focused pytest, Ruff, and `git diff --check`. Do not edit skills, docs, frontend source/assets, `PROGRESS.md`, `decisions.md`, or orchestration files. Do not run `./verify`, commit, merge, touch live data, or propose the Ticket.

Use `PYTHONPATH=/Users/khushaljagota/.hermes/worktrees/planning-v2-t_gn7x278u/src` with `/Users/khushaljagota/.hermes/planning-v2/.venv/bin/python`. Finish with a concise report including exact RED/GREEN commands and changed files.
