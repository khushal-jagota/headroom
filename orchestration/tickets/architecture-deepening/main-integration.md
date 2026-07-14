# Architecture deepening × restart recovery integration

## Purpose

Integrate current `main` commit `49660f5` (restart/crash continuation and bounded shutdown) with the
reviewed architecture-deepening head `9e51df2`. Preserve both products while adopting the deeper AD01–AD09
contracts as the final public/domain shape.

## Required behavior

- Keep restart continuation for stranded Ticket Employee work and ordinary Panels Chat work in the same
  durable Hermes session, without replaying the original prompt.
- Keep partial visible Chat output, strict missing/mismatched-session failure, startup recovery after gateway
  readiness and before automatic discovery, and the one configured shutdown deadline.
- Adapt recovery to the canonical AD06 human-turn observation/lifecycle interfaces and AD09 explicit
  Employee-session history. Do not restore deleted generic `stream`/history result bags, retired HTTP routes,
  Chat-state Hermes fallback, `chat_session_key` on Tickets, or any compatibility alias.
- Keep Worker type explicit and immutable. No live path may choose `coding`; only the terminal migration may
  classify genuinely historical rows that predate Worker type.
- Keep Panels Chat state Panels-owned and Employee history explicit/Ticket-only.
- Preserve the AD03 complete Automatic Employee-step eligibility decision and atomic claim.
- Preserve all accepted non-conflicting changes from both parents.

The restart-recovery parent's later bounded-shutdown contract narrowly supersedes AD03's original
`AutomaticEmployeeStepDiscoveryLoop.stop(self) -> None` lifecycle signature: the merged method accepts the
same absolute deadline as the runner so a stuck discovery poll cannot restart or exceed the one total
shutdown budget. This changes no eligibility factor, candidate query, claim, wake, ownership, or ordinary
start/stop ordering.

## Conflict scope

- `PROGRESS.md`
- `docs/employee-runtime.md`
- `docs/systems.md`
- `src/planner/chat/service.py`
- `src/planner/core/adapters/base.py`
- `src/planner/core/adapters/fakes.py`
- `src/planner/core/adapters/real.py`
- `src/planner/core/loops.py`
- `src/planner/minds/shared_gateway.py`
- `src/planner/runtime/employee_step_runner.py`
- `tests/unit/test_chat_activity.py`
- `tests/unit/test_core_loops.py`
- `tests/unit/test_minds.py`

Additional files may change only for a concrete integration repair or regression test required to preserve
both parent contracts; record the reason in the implementation report.

## Acceptance

- No conflict markers or unmerged paths remain.
- Focused restart recovery, Chat lifecycle, Employee runner/history, loop, and gateway tests pass.
- The existing restart browser proof and the AD09 history-separation browser proof pass.
- Ruff and mypy pass for touched Python.
- Static scans prove retired names/defaults/interfaces were not resurrected.
- A read-only independent review reports no violations before the merge commit is created.
- The full canonical `./verify` passes after the merge commit and again after `main` is fast-forwarded.
