# Backend recovery handoff

The first backend leaf stopped making progress after landing strict RED/GREEN storage and edit slices.
Its partial tree is untrusted but parent classification shows only two current failures:

```text
.venv/bin/pytest -q tests/unit/test_db.py tests/unit/test_ticket_edit_api.py \
  tests/unit/test_ticket_lifecycle.py tests/unit/test_tickets_engine.py \
  tests/unit/test_employee_step_runner.py tests/unit/test_authctx_routes.py

FAILED tests/unit/test_tickets_engine.py::test_direct_plan_accept_routes_only_khushal_to_user_takeover[khushal]
FAILED tests/unit/test_tickets_engine.py::test_auto_accepted_plan_routes_only_khushal_to_takeover_that_survives_settlement[khushal]
```

Storage, enum, PATCH, serialization, and current edit tests otherwise pass. Remaining owned work:

1. Implement the pure transition decision and canonical data-layer status write for both direct and
   auto-accepted Plan transitions. Khushal must become `user_takeover`; an in-flight auto-accept must
   not have that status cleared by employee settlement. Agent/null paths remain unchanged.
2. Add strict RED then GREEN tests proving `_next_step_prompt` sends the current wire value or
   `unassigned` to the real gateway prompt. Suitability prose must not enter backend code.
3. Run all backend focused modules, Ruff, Mypy, and `git diff --check`.
4. Write `backend-report.md` with inherited test evidence clearly labeled as inherited, new exact
   prompt RED/GREEN evidence, final commands/results, and no unsupported authorship claim for the
   stalled first leaf.

Do not touch frontend/skills/docs, unrelated dirty files, or run `./verify`. Do not commit.
