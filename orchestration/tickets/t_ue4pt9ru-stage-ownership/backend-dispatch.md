# Backend implementation dispatch

Implement the backend slice of `contract.md` in this worktree.

Read `AGENTS.md`, `PRINCIPLES.md`, `contract.md`, `ownership.md`, and current source/tests. Use strict vertical RED→GREEN TDD. An abandoned prior worker left `tests/unit/test_stage_ownership_backend.py`; inspect and keep only tests that fit the frozen contract.

Own only `src/planner/**`, `tests/unit/**`, `tests/support/**`, `tests/typing/**`, and backend/CLI e2e tests. Do not edit `web/**`, `skills/**`, `docs/**`, `PROGRESS.md`, `decisions.md`, or other orchestration files. Do not commit and do not run `./verify`.

Implement every backend requirement in the frozen contract: StageOwnershipMode/defaults/manifest, Ticket override storage, v21 migration, paired_work, ExecutionRoute replacement, removal of transition hooks/implementer live contracts, effective/resting lifecycle, all stage-changing and run-settlement paths, automatic eligibility, paired proposal parking, Chief reconciliation scope preservation, API/actions/CLI/takeover/release, prompt/views/read-model/status rollups/events, and focused tests. Existing shipped coding/new_worker Stage defaults remain worker; probe must prove mixed modes.

Run focused pytest, Ruff, and mypy/compile checks for the changed backend. Finish with a concise stdout report: files changed, RED and GREEN commands/results, and any unresolved integration assumption.