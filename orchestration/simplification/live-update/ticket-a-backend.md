# Ticket A — backend: change signal, SSE, wake rewiring, event-log deletion, migration

Implements plan.md §1–§6 (as revised after plan review; read plan-review-disposition.md too).

## Owns
`src/planner/**` (except `conversation2/`; `conversation/` only the two named fallout lines),
`tests/unit/**`, `tests/typing/**`, `config.yaml`.

## Must not touch
`web/`, `tests/e2e/`, `docs/`, root `CLAUDE.md`/`AGENTS.md`, `orchestration/`,
`src/planner/conversation2/`, `src/planner/skills/`. No commits — leave the working tree for the
orchestrator to integrate.

## Contract fixed for the parallel frontend ticket (do not vary)
- `GET /api/changes`, `text/event-stream`; one unnamed `data: change` frame per (coalesced)
  committed write; comment lines as heartbeats every `sse_heartbeat_ms` (default 15000).
- `/api/meta` becomes exactly `{test_mode, release_sha}`.

## Deliverables
1. `core/change_signal.py` hub; commit-detecting Connection subclass in `core/db.connect`;
   SSE endpoint + active-stream registry; `application.py` uvicorn Server subclass closing SSE
   streams in `handle_exit`.
2. Wake rewiring: discovery loop subscribes to the signal in `core/loops.py`; delete the wake
   module, every wake parameter/call (tickets/actions, days/actions, both API dependencies,
   EmployeeStepRunner, server.py app.state); collapse actions that become pure pass-throughs
   (plan names them).
3. Deletions: `core/events.py`, `core/ws.py` + WS endpoint, `EventRow`,
   `GET /api/tickets/{id}/events` + `list_events_for_entity` + `event_json`, CLI
   `panels ticket events`, config knobs (`ws_poll_ms`, `ws_heartbeat_ms`, `ui_debounce_ms`,
   `events_read_limit`) everywhere including `config.yaml` and environment launchers, every
   `append_event` call site. `EventKind`/`EventSpec`/`Decision` STAY (resolution-engine contract).
4. Migration: add `tickets.ticket_status_changed_at` (backfill latest `ticket_status_changed`
   event, else `updated_at`), drop `events` + index; both ticket INSERT sites and the status write
   door maintain the column; `review_view` reads it. Frozen `PRE_ALEMBIC_*` constants untouched.
5. Tests: door tests, hub tests, SSE endpoint tests, shutdown process test, migration test
   (fixtures per plan), rewrite/delete the ~25 unit files referencing events/wake (canonical-row
   assertions stay; event-row assertions go; wake-wiring tests become signal tests). Delete
   `test_server_events.py`, `test_frontend_event_mapping.py`,
   `test_automatic_employee_step_eligibility_wake.py`.

## Gates (run through this worktree's `.venv`; show output in your report)
- `.venv/bin/ruff check .`
- `.venv/bin/mypy src/ tests/typing/`
- `.venv/bin/pytest tests/unit`

tests/e2e will be temporarily broken after this ticket; that is expected and owned by ticket C.
