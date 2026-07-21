# ACP-07/08 shared backend activation — implementation plan

## 1. Extend only the materialized registration boundary

Add resolved `repository_root` to `EmployeeBackendBuildContext` and optional async
`startup_preflight` to `MaterializedEmployeeBackendRegistration`. Update static and Hermes
construction explicitly with no preflight. Pass `repository_root` from `ConversationComposition`.
Do not add a second catalog, provider switch, or registry method.

## 2. Compose ordered startup preflights

Store the materialized registrations in `ConversationComposition` and add
`run_employee_backend_startup_preflights()`. Await non-null hooks serially in catalog order exactly
once for a composition. Keep `backend_available()` as a live query over materialized executability.
Test that preflight does not call the registry or create an ordinary employee child.

## 3. Put preflight before admission

In the production lifespan, build the composition, await its preflights, then publish app state and
start loops. If the await fails, use the existing absolute shutdown deadline to close admission and
shut down the composition, leave app state unset, and re-raise. Test both success ordering and failure
cleanup. Test-mode registrations with no preflight retain existing behavior.

## 4. Register the settled provider builders once

After the provider modules expose their no-argument catalog builders, add them after Hermes in the
sole production tuple. Materialization uses the build context to resolve the locked local package,
data-owned log path, repository root, and Claude preflight. Update only exact expected catalog/default
assertions; do not change Worker defaults or Chief.

## 5. Focused gate and review handoff

Run Ruff on touched files, strict Mypy on affected source, focused composition/server/catalog/Worker
tests, the common production-catalog e2e assertion, and `git diff --check`. Record commands and full
results in `focused-checks.txt` and summarize behavior in `implementation-report.md`. Do not run the
canonical verifier or real dogfood. After Codex, Claude, and this shared slice are settled, one fresh
sub-agent reviews the integrated provider/preflight/compaction diff against all three contracts.
