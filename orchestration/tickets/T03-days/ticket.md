# T03 — Days: planning date, day tickets, plan tree, boundary deterministic pass (stage 3)

## Scope

The days domain's pure logic and data layer, plus unit tests for acceptance items 1, 12, 17, 18 (test names `test_a01_*`, `test_a12_*`, `test_a17_*`, `test_a18_*`).

Contracts implemented against (never modified): `src/planner/days/contracts.py`, `src/planner/core/contracts.py`, `core/errors.py`; infrastructure `core/db.py`, `core/events.py`, `core/ids.py`, `core/clock.py`, adapter Protocols in `core/adapters/`.

## Files owned

- `src/planner/days/logic/` — pure: planning-date math (§6.1: planning date = calendar date of (now − boundary hours); boundary hour from config), plan-tree operations (§6.3: accept node, accept-all, invalidate root/child, reject-all — as pure tree transforms returning new tree + required side-effect descriptions), carryover/overdue computation rules (§6.2 deterministic pass as pure functions over row data).
- `src/planner/days/data.py` — day materialization on first read/write (no "missing day" state), day-ticket list add/remove with contiguous positions from 0 (removal deletes association only, re-packs positions, logs `day_ticket_removed`), plan storage, brief/notes writes.
- `src/planner/days/boundary.py` — the boundary job: runs once per planning date guarded by `boundary_runs`; deterministic pass (materialize day, carryover candidates = yesterday's day-tickets whose ticket state not `done`/`dropped`, overdue list, approval-queue digest, `day_closed` event with {counts of done/not-done day tickets}); judgment pass through the BoundaryAdapter Protocol (one call, 60s timeout from config, failure event-logged, day survives with empty brief and no plan); judgment skipped entirely if the human already planned the day (any day-ticket or accepted plan exists).
- `tests/unit/test_days.py` — items 1, 12, 17, 18.

## Test fences (SPEC §18.3, exact)

1. 2026-07-05T04:59 local → planning date 2026-07-04; T05:00 → 2026-07-05; boundary hour honored from config.
12. Removal deletes the association only; positions re-pack contiguously; ticket state unchanged; `day_ticket_removed` logged.
17. Root invalidation marks root + all children invalidated and requests exactly one replan; child invalidation replaces only that child; accept-all accepts every node and adds child tickets to the day list exactly once.
18. Fake clock: first tick ≥ 05:00 creates the day, computes carryover (yesterday's non-done day tickets), overdue list, logs `day_closed`; second tick same date does nothing; explicit prior planning skips the judgment pass.

Tests use the shared conftest temp-DB and fake clock, and fake adapters from `core/adapters` for item 18/17 replan requests. Replan serialization (R5 latest-wins) is stage-4 runtime behavior; here the logic layer must expose invalidation → replan-request as data the runtime can serialize.

## Constraints

- Pure logic imports stdlib + contracts only; data layer imports core db/events; no FastAPI/pydantic.
- Files outside the owned list untouched; shared conftest read-only.
- ruff + mypy strict clean; all four named tests green.
