# Migration report — t_p6de8rje-lifecycle, database-migration slice only

Scope: `src/planner/core/db.py` and `tests/unit/test_db.py` only, per dispatch. Did not touch
`contracts.py`, `machine.py`, `resolution.py`, `test_ticket_lifecycle.py`, or any other domain
file (frozen/out of scope). Did not commit, did not run `./verify`.

## What changed

- `DDL` (tickets table): `state` and `ceiling` CHECK constraints now list
  `needs_implementation`/`needs_closeout` in place of `in_progress`/`needs_review`. `fields`
  column default now has five slots (`success`, `approach`, `plan`, `implementation`,
  `closeout`) in place of the old four (`success`, `approach`, `plan`, `result`).
- Added `_migrate_ticket_lifecycle(conn)`, called from `create_schema` right after
  `_migrate_project_columns(conn)` (so `project_id` already exists whenever this runs).
  - Detects whether migration is needed by reading `sqlite_master.sql` for the `tickets` table
    and checking whether the literal `needs_implementation` is present. This is structural
    (catches an old CHECK constraint even on a table with zero old-state rows), not data-driven,
    so an empty legacy table still gets rebuilt with the new constraints.
  - When needed, does a full `tickets` → `tickets_new` rebuild (same pattern as the existing
    `_rebuild_tickets_with_project_id`), computing new `state`, `ceiling`, and `fields` per row
    in Python before inserting (a blanket `INSERT ... SELECT` would violate the new CHECK on old
    values), then drops the old table and renames.
  - Runs a `PRAGMA foreign_key_check` after the rebuild and raises if it fails, matching the
    existing project-column migration's safety check.
- Three pure helpers implement the value mapping: `_migrate_lifecycle_state`,
  `_migrate_lifecycle_ceiling`, `_migrate_lifecycle_fields_json`.

## Mapping rules implemented

- `state`: `in_progress` → `needs_implementation` unconditionally. `needs_review` →
  `needs_closeout` when `ticket_status == 'empty'` (settled/idle); otherwise →
  `needs_implementation` (any non-empty `ticket_status`: `agent_running_step`,
  `awaiting_approval`, `user_takeover`, `errored`). All other state values pass through
  unchanged, including `done` and `dropped`.
- `ceiling`: every legacy `needs_review` row with non-empty runtime control is capped at
  `needs_implementation`, regardless of its prior ceiling. This keeps active, rejected, errored,
  takeover, and awaiting-approval Implementation work from auto-advancing into Closeout.
  Otherwise `in_progress` → `needs_implementation`, `needs_review` → `needs_closeout`, and every
  other value is unchanged.
- `fields`: `result` key renamed to `implementation`; `closeout` added as an empty slot
  (`{value: null, proposal: null, user_note: null}`). `success`/`approach`/`plan` carried over
  as-is (defaulted to empty slots if missing from a malformed/partial JSON blob).
  - Normal case (including an earlier-stage `awaiting_approval`): `implementation.value`,
    `.proposal`, `.user_note` copied straight from the old `result` slot. Success, Approach, and
    Plan slots pass through unchanged, including whichever one owns the earlier pending proposal.
  - Result-stage `awaiting_approval` case (`in_progress` or `needs_review`): reconstructs a true
    pending proposal with
    `implementation.value = null`. If the old slot already has a proposal, it is retained verbatim.
    Otherwise migration moves the old Result value into a synthesized proposal with
    `proposed_by: "migration"` and the row's `updated_at`. The user note is preserved. A row with
    neither a usable value nor proposal fails loudly rather than becoming an empty approval.

## Judgment call — synthesized proposal metadata

The old schema has no field recording who authored a value sitting in `result.value` under
`awaiting_approval`. A synthesized proposal therefore uses `"migration"` rather than inventing
an agent author. `created_at` uses the ticket row's `updated_at`; an existing legacy proposal keeps
its original author and timestamp.

## RED → GREEN

RED (before implementation, only the DDL/migration-detection was missing):
```
.venv/bin/python -m pytest tests/unit/test_db.py -k "lifecycle or fresh_schema_rejects" -v
```
→ 3 of 4 new tests failed decisively:
- `test_fresh_schema_rejects_old_lifecycle_state_values`: `UPDATE ... SET state = 'in_progress'`
  did not raise `sqlite3.IntegrityError` (old CHECK still accepted it).
- `test_create_schema_migrates_current_schema_old_lifecycle_rows`: `KeyError: 'implementation'`
  — fields JSON still had the old `result` key untouched.
- `test_create_schema_migrates_ticket_lifecycle_with_old_project_column_rebuild`: state stayed
  `'in_progress'` instead of becoming `'needs_implementation'`.

(`test_create_schema_ticket_lifecycle_migration_is_idempotent` trivially passed pre-implementation
since two consecutive no-op `create_schema` calls on unmigrated data are still identical to each
other — not evidence of correctness by itself, but it stayed green through the implementation.)

GREEN (after implementation):
```
.venv/bin/python -m pytest tests/unit/test_db.py -v
```
→ `8 passed`, including preservation of an existing proposal and explicit rejection of an
awaiting-approval row with no candidate body.

Two ceiling bugs were caught and fixed during GREEN/review. First, the old project-column rebuild
used an `awaiting_approval` row whose ceiling started at `done`, proving the cap cannot depend on
the old ceiling literal. Independent implementation review then caught that the same rule must
cover every non-empty runtime status on a legacy `needs_review` row, not only awaiting approval;
the active-revision expectation now proves that cap.

## Verification run

```
.venv/bin/python -m pytest tests/unit/test_db.py -v      # 8 passed
.venv/bin/ruff check src/planner/core/db.py tests/unit/test_db.py   # All checks passed!
.venv/bin/mypy src/planner/core/db.py                     # Success: no issues found in 1 source file
git diff --check -- src/planner/core/db.py tests/unit/test_db.py    # clean
```

## Integrated verification

The surrounding domain and test migration is now complete. The full unit suite passes, the five-field
codec reads the rebuilt rows, and no production/test code references the removed Ticket enums or
Result field outside intentional legacy migration fixtures.

## Post-landing repair

The first real startup after the lifecycle merge exposed four `needs_success` Tickets with
`ticket_status = 'awaiting_approval'`, a pending Success proposal, and an empty Result slot. The
original migration keyed Result reconstruction from `ticket_status` alone and therefore rejected
these valid earlier-stage approvals. D56 narrows reconstruction to the two legacy Result-stage
states. The focused regression failed with the production exception before the repair and passed
afterward; the full database also migrated successfully through a temporary SQLite backup with all
four Success proposals preserved. Independent Codex review returned `NO VIOLATIONS`.

## Remaining risks

- No known data-shape gap remains: earlier-stage approvals retain their actual gated proposal;
  Result-stage value-only and proposal-bearing approvals remain covered; and the integrated
  five-field codec reads the migrated output.
- `_migrate_ticket_lifecycle` is purely additive/structural (drop+rebuild), consistent with the
  existing `_rebuild_tickets_with_project_id` pattern in this file; it does not attempt to be
  transactional beyond SQLite's implicit connection-level atomicity already relied on elsewhere
  in this module.
