# T06 — Sprints: item permissions, status proposals, freeze rules, overlap (stage 3)

## Scope

The sprints domain's pure logic and data layer, plus unit tests for acceptance items 10 and 20 (test names `test_a10_*`, `test_a20_*`).

Contracts implemented against (never modified): `src/planner/sprints/contracts.py`, `src/planner/core/contracts.py`, `core/errors.py`; infrastructure `core/db.py`, `core/events.py`, `core/ids.py`, `core/clock.py`.

## Files owned

- `src/planner/sprints/logic/` — pure: item-transition permissions (§3.2: agent-permitted `todo ↔ active` and setting `blocked` with a non-empty `blocked_by` ticket list; `done`/`deferred_next_sprint` only via proposal + human accept — the item's single gating field is `status`), `blockers_cleared` derivation (all blockers at ticket-state `done`), freeze admission (§5/§3.1: kickoff fields frozen after `kickoff_frozen_at` except append-only `weekly_addenda`; review fields frozen after `review_frozen_at`), sprint-range overlap detection (inclusive dates), current-sprint selection by planning date.
- `src/planner/sprints/data.py` — canonical writers: sprint create (rejects overlap), kickoff/review field writes (reject frozen with structured error), freeze-kickoff / freeze-review actions, weekly addenda append, item create/update, agent item transitions, item status proposal + human accept (no onward grant — item accepts are terminal for agent involvement per §4.4.7), item↔sprint assignment as plain event-logged field update (§3.2).
- `tests/unit/test_sprints.py` — items 10, 20.

## Test fences (SPEC §18.3, exact)

10. Agent `todo→active` succeeds; agent direct `active→done` write rejected; `done` via proposal + accept succeeds; `blocked_by` list stored and `blockers_cleared` computed when all blockers done.
20. Kickoff write after freeze rejected; `weekly_addenda` append still allowed; review same pattern; sprint overlap rejected.

## Constraints

- Pure logic imports stdlib + contracts only; no FastAPI/pydantic.
- Ticket-side blocking links are T05's; item `blocked_by` here is the §3.2 list field on the item row.
- Files outside the owned list untouched; shared conftest read-only.
- ruff + mypy strict clean; both named tests green.
