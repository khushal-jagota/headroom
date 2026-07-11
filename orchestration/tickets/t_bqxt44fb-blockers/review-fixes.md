# t_bqxt44fb review fixes

## Finding dispositions

- P1 `/api/links` attributed-agent mutation: accepted and fixed. Both add/remove routes now call `require_direct_write(ctx)`. The focused API test proves an attributed agent gets `agent_forbidden` and leaves links, events, and readiness rings unchanged.
- P1 blocker e2e state jump: refuted by orchestrator evidence. The test now makes the assumption explicit by asserting the created cleared blocker is already `needs_success` before posting `done`. The focused browser rerun was attempted here but Chromium is sandbox-blocked before app code.
- P1 Sprint child blocked status inline SQL: accepted and fixed. `read_item` now uses `core_links.blocker_summary` for direct item blockers and child ticket blockers.
- P2 raw backend blocker summary dict: accepted and fixed. `core/contracts.py` now owns frozen typed summary row/contracts, `core.links.blocker_summary` returns `BlockerSummary`, and Ticket views serialize to the frozen external JSON shape only at the view/API boundary.
- P2 frontend `target_kind` looseness: accepted and fixed. The TypeScript type is exactly `"ticket" | "sprint_item"`.
- P2 stale event-mapping relationship vocabulary/fallback list: accepted and fixed. Non-migration event fixtures use `kind: "blocks"`, and the fallback kind list includes the current backend `EventKind` values.

## RED

```bash
.venv/bin/python -m pytest tests/unit/test_links.py::test_blocker_summary_resolves_active_and_cleared_ticket_and_item_rows tests/unit/test_readiness_actions.py::test_agent_link_add_and_remove_are_rejected_without_writes_events_or_rings tests/unit/test_sprints.py::test_x06_child_ticket_blocked_status_uses_canonical_blocker_summary
```

Result: RED. Collection failed because `planner.core.contracts` had no `BlockerSummary` typed contract:

```text
ImportError: cannot import name 'BlockerSummary' from 'planner.core.contracts'
```

## GREEN

```bash
.venv/bin/python -m pytest tests/unit/test_links.py::test_blocker_summary_resolves_active_and_cleared_ticket_and_item_rows tests/unit/test_readiness_actions.py::test_agent_link_add_and_remove_are_rejected_without_writes_events_or_rings tests/unit/test_sprints.py::test_x06_child_ticket_blocked_status_uses_canonical_blocker_summary
```

Result: `3 passed, 1 warning`.

```bash
.venv/bin/python -m pytest tests/unit/test_links.py tests/unit/test_sprints.py tests/unit/test_readiness_actions.py tests/unit/test_cli_blockers.py tests/unit/test_ticket_delete.py tests/unit/test_db.py tests/unit/test_seed.py tests/unit/test_frontend_event_mapping.py
```

Result: `60 passed, 1 warning`.

```bash
.venv/bin/python -m ruff check src/planner/core/contracts.py src/planner/core/links.py src/planner/sprints/data.py src/planner/tickets/views.py src/planner/tickets/api.py tests/unit/test_links.py tests/unit/test_readiness_actions.py tests/unit/test_sprints.py tests/e2e/test_blockers_frontend.py
```

Result: `All checks passed!`

```bash
.venv/bin/python -m mypy src/planner/core/contracts.py src/planner/core/links.py src/planner/sprints/data.py src/planner/tickets/views.py src/planner/tickets/api.py
```

Result: `Success: no issues found in 5 source files`.

```bash
npm --prefix web test -- event-mapping
```

Result: passed.

```bash
npm --prefix web run check
```

Result: `svelte-check found 0 errors and 3 warnings in 1 file`. The warnings are the existing `TicketRoute.svelte` route-id capture warnings.

```bash
npm --prefix web run build
```

Result: passed. Vite rebuilt `web/dist`; the same existing Svelte warnings and external asset notices were emitted.

```bash
.venv/bin/python -m py_compile tests/e2e/test_blockers_frontend.py
```

Result: passed.

```bash
git diff --check
```

Result: passed.

## Browser rerun

```bash
.venv/bin/python -m pytest tests/e2e/test_blockers_frontend.py::test_ticket_blocker_summary_links_and_sprint_item_hash_selection
```

Result: blocked before app code. Chromium launch fails in this sandbox with:

```text
bootstrap_check_in org.chromium.Chromium.MachPortRendezvousServer... Permission denied (1100)
```

The factual e2e proof remains the orchestrator direct run noted in the correction brief.
