# Backend/runtime implementation report

## Changed

- Added nullable checked Ticket `implementer` storage, schema version 13, and an idempotent
  post-lifecycle migration. The typed values are `khushal`, `panels_worker`, `hermes_codex`, and
  `hermes_claude`.
- Threaded the value through Ticket creation, row mapping, serialization/copy text, and the ordinary
  atomic PATCH path. Implementer is direct-only; set/change/clear/no-op/invalid requests use the
  existing validation, event, and worker-context behavior without ringing readiness or changing
  state/status.
- Added one pure Plan-handoff rule. Direct acceptance, low-level auto-acceptance, and the real current
  worker proposal-with-recap path all select `user_takeover` when a Khushal-assigned Plan advances to
  `needs_implementation`. Agent and unassigned paths remain unchanged, and employee settlement
  preserves takeover.
- Added the current wire value (or `unassigned`) to the actual next-step Hermes prompt. Suitability
  guidance remains outside backend code.

## Test-first evidence

The first backend leaf stopped before handoff; its partial tree is treated as untrusted. Parent-run
classification nevertheless preserved its red-capable tests and showed the exact incomplete state:

```text
.venv/bin/pytest -q tests/unit/test_db.py tests/unit/test_ticket_edit_api.py \
  tests/unit/test_ticket_lifecycle.py tests/unit/test_tickets_engine.py \
  tests/unit/test_employee_step_runner.py tests/unit/test_authctx_routes.py

FAILED test_direct_plan_accept_routes_only_khushal_to_user_takeover[khushal]
FAILED test_auto_accepted_plan_routes_only_khushal_to_takeover_that_survives_settlement[khushal]
```

A recovery leaf made those two transition tests green. Parent spot-check then found that the normal
worker surface (`file_current_proposal_with_recap`) was not covered. A new focused test failed RED:

```text
.venv/bin/pytest -q \
  tests/unit/test_tickets_engine.py::test_current_worker_plan_proposal_routes_khushal_to_takeover_that_survives_settlement

E assert TicketStatus.agent_running_step is TicketStatus.user_takeover
```

After the minimal data-layer integration repair, the exact command passed GREEN.

The recovery leaf landed prompt tests without a durable report. Parent reconstructed baseline RED by
reverse-applying only `employee_step_runner.py` under guaranteed restoration, then running:

```text
.venv/bin/pytest -q \
  tests/unit/test_employee_step_runner.py::test_next_step_prompt_includes_implementer_wire_value_or_unassigned \
  tests/unit/test_employee_step_runner.py::test_worker_step_prompt_and_reply_are_visible_in_chat_history
```

Both failed because the old prompt ended at `for approval.` instead of carrying the implementer.
After restoration, the same two tests passed GREEN, including the submitted gateway frame for
`hermes_codex`.

## Focused verification

- All focused backend modules passed together:
  `test_db.py`, `test_ticket_edit_api.py`, `test_ticket_lifecycle.py`,
  `test_tickets_engine.py`, `test_employee_step_runner.py`, and `test_authctx_routes.py`.
- `.venv/bin/ruff check` over all touched Python and the focused e2e file: `All checks passed!`
- `.venv/bin/mypy src`: `Success: no issues found in 104 source files`
- `git diff --check`: passed with no output.
- Final frontend integration proof also passed:
  `test_ticket_implementer_assignment_edits_in_facts_without_changing_workflow`.

`./verify` is intentionally deferred to the parent after independent diff review. No commit was made.
