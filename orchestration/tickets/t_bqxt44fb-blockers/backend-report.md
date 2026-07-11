# Backend Report — t_bqxt44fb Blockers

## Scope

Implemented the backend/schema/runtime/API/CLI/docs slice only. No frontend files were modified. No live
database was opened or migrated. No commit, merge, delegation, or `./verify` run was performed.

## Frozen JSON Summary Shape

Ticket detail and `/api/tickets/by-session/{session_key}` now include:

```json
{
  "blocked": true,
  "blocker_summary": {
    "blocked": true,
    "blocked_by": [
      {
        "ticket_id": "t_blocker",
        "title": "Blocker title",
        "state": "needs_plan",
        "active": true,
        "href": "#/ticket/t_blocker"
      }
    ],
    "blocks": [
      {
        "target_id": "si_item",
        "target_kind": "sprint_item",
        "title": "Item title",
        "active": true,
        "href": "#/sprint?item=si_item"
      }
    ]
  }
}
```

`blocked_by` rows are incoming Ticket blockers. `blocks` rows are outgoing Ticket or Sprint-item targets.
`active` means the source Ticket is not `done` or `dropped`.

## RED/GREEN Evidence

- RED: `.venv/bin/python - <<'PY' ... import planner.core.links ... PY`
  - Result: failed with `AttributeError: type object 'LinkKind' has no attribute 'parent_child'`.
- GREEN: same command.
  - Result: printed `[<LinkKind.blocks: 'blocks'>]` and `links imported`.

- RED: `.venv/bin/python -m pytest tests/unit/test_db.py::test_fresh_schema_links_are_blocks_only_without_belongs_to_index -q`
  - Result: failed because obsolete kind inserts did not raise `sqlite3.IntegrityError`.
- GREEN: same command.
  - Result: `1 passed`.

- RED: `.venv/bin/python -m pytest tests/unit/test_db.py::test_create_schema_rebuilds_legacy_links_after_sprint_blocker_conversion -q`
  - Result: failed because legacy `belongs_to`, `parent_child`, and `relates` rows remained.
- GREEN: same command.
  - Result: `1 passed`.

- RED: `.venv/bin/python -m pytest tests/unit/test_seed.py::test_a19_seed_fixture_import_counts_mappings_idempotency_and_skip_list -q`
  - Result: first failed on `CHECK constraint failed: kind = 'blocks'` from the obsolete seed `belongs_to` insert; then failed on old report/event counts.
- GREEN: same command.
  - Result: `1 passed`.

- RED: `.venv/bin/python -m pytest tests/unit/test_links.py -q`
  - Result: failed on missing endpoint validation, inactive cycle handling, then missing `blocker_summary`.
- GREEN: same command.
  - Result: `5 passed`.

- RED: `.venv/bin/python -m pytest tests/unit/test_links.py::test_ticket_detail_and_copy_text_use_resolved_blocker_summary -q`
  - Result: failed with `KeyError: 'blocker_summary'`.
- GREEN: same command.
  - Result: `1 passed`.

- RED: `.venv/bin/python -m pytest tests/unit/test_sprints.py::test_x06_sprint_item_blocked_status_uses_active_blocker_summary -q`
  - Result: failed because a dropped direct blocker was not cleared.
- GREEN: same command.
  - Result: `1 passed`.

- RED: `.venv/bin/python -m pytest tests/unit/test_readiness_actions.py::test_source_state_deactivation_reports_blocked_targets_and_rings_once tests/unit/test_readiness_actions.py::test_reactivating_source_rejects_active_blocks_cycle_and_does_not_ring -q`
  - Result: failed because `affected_blocked_target_ids` was absent and reactivation returned 200.
- GREEN: same command.
  - Result: `2 passed`.

- RED: `.venv/bin/python -m pytest tests/unit/test_cli_blockers.py -q`
  - Result: failed with `Error: No such command 'block'`.
- GREEN: same command.
  - Result: `1 passed`.

## Migration Proof

Disposable legacy DB coverage is in `tests/unit/test_db.py::test_create_schema_rebuilds_legacy_links_after_sprint_blocker_conversion`.
It creates a legacy `links` table with `belongs_to`, `parent_child`, `relates`, and `blocks`, plus a legacy
`sprint_items.blocked_by` value. `create_schema` then:

- converts legacy `blocked_by` to a `blocks` row;
- preserves canonical `tickets.sprint_item_id`;
- rebuilds `links` with `CHECK (kind = 'blocks')`;
- removes obsolete rows;
- drops `idx_links_one_belongs_to`;
- preserves `idx_links_to`;
- bumps `PRAGMA user_version` to `SCHEMA_VERSION` 15.

Focused migration commands:

- `.venv/bin/python -m pytest tests/unit/test_db.py::test_fresh_schema_links_are_blocks_only_without_belongs_to_index -q` → `1 passed`
- `.venv/bin/python -m pytest tests/unit/test_db.py::test_create_schema_rebuilds_legacy_links_after_sprint_blocker_conversion -q` → `1 passed`

## Final Focused Green Evidence

- `.venv/bin/python -m pytest tests/unit/test_links.py tests/unit/test_db.py tests/unit/test_seed.py tests/unit/test_sprints.py tests/unit/test_ticket_delete.py tests/unit/test_readiness_actions.py tests/unit/test_cli_blockers.py tests/unit/test_ticket_readiness_loop.py tests/unit/test_tickets_engine.py -q`
  - Result: `112 passed`; warnings only: Starlette/httpx deprecation and `TestClock` collection warning.
- `.venv/bin/python -m ruff check src/planner/core/contracts.py src/planner/core/db.py src/planner/core/links.py src/planner/core/link_actions.py src/planner/seed/contracts.py src/planner/seed/importer.py src/planner/sprints/data.py src/planner/sprints/views.py src/planner/sprints/logic/blockers.py src/planner/tickets/data.py src/planner/tickets/views.py src/planner/cli/main.py tests/unit/test_links.py tests/unit/test_db.py tests/unit/test_seed.py tests/unit/test_sprints.py tests/unit/test_ticket_delete.py tests/unit/test_readiness_actions.py tests/unit/test_cli_blockers.py`
  - Result: `All checks passed!`
- `.venv/bin/python -m mypy src/planner/core/contracts.py src/planner/core/db.py src/planner/core/links.py src/planner/core/link_actions.py src/planner/seed/contracts.py src/planner/seed/importer.py src/planner/sprints/data.py src/planner/sprints/views.py src/planner/sprints/logic/blockers.py src/planner/tickets/data.py src/planner/tickets/views.py src/planner/cli/main.py`
  - Result: `Success: no issues found in 12 source files`

## Files Changed

- `src/planner/core/contracts.py`
- `src/planner/core/db.py`
- `src/planner/core/links.py`
- `src/planner/seed/contracts.py`
- `src/planner/seed/importer.py`
- `src/planner/sprints/data.py`
- `src/planner/sprints/logic/blockers.py`
- `src/planner/sprints/views.py`
- `src/planner/tickets/data.py`
- `src/planner/tickets/views.py`
- `src/planner/cli/main.py`
- `tests/unit/test_links.py`
- `tests/unit/test_db.py`
- `tests/unit/test_seed.py`
- `tests/unit/test_sprints.py`
- `tests/unit/test_ticket_delete.py`
- `tests/unit/test_readiness_actions.py`
- `tests/unit/test_cli_blockers.py`
- `docs/systems.md`
- `docs/cli.md`
- `docs/tickets-and-gates.md`
- `PROGRESS.md`
- `decisions.md`
- `orchestration/tickets/t_bqxt44fb-blockers/backend-report.md`

## Remaining Risks

- Frontend invalidation and rendering are intentionally not implemented in this backend-only slice.
- The full repository verifier was intentionally not run per instruction.
- Generated docs HTML was not rebuilt; only live Markdown docs were updated.
