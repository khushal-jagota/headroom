# t_5m7fmdk3 Kickoff backend report

## Scope

Implemented the backend vertical slice only. No `web/` files, browser tests, full
`./verify`, commit, merge, or delegation.

## Changed files

- `PROGRESS.md`
- `docs/tickets-and-gates.md`
- `orchestration/tickets/t_5m7fmdk3-kickoff/backend-report.md`
- `src/planner/cli/main.py`
- `src/planner/core/contracts.py`
- `src/planner/core/db.py`
- `src/planner/runtime/readiness.py`
- `src/planner/seed/importer.py`
- `src/planner/tickets/actions.py`
- `src/planner/tickets/api.py`
- `src/planner/tickets/contracts.py`
- `src/planner/tickets/data.py`
- `src/planner/tickets/logic/admission.py`
- `src/planner/tickets/logic/external_work.py`
- `src/planner/tickets/logic/machine.py`
- `src/planner/tickets/views.py`
- `tests/unit/test_authctx_routes.py`
- `tests/unit/test_chief_external_work.py`
- `tests/unit/test_db.py`
- `tests/unit/test_employee_step_runner.py`
- `tests/unit/test_readiness_actions.py`
- `tests/unit/test_return_for_revision.py`
- `tests/unit/test_seed.py`
- `tests/unit/test_ticket_edit_api.py`
- `tests/unit/test_ticket_lifecycle.py`
- `tests/unit/test_ticket_readiness_loop.py`
- `tests/unit/test_tickets_engine.py`
- `tests/unit/test_worker_context.py`

`decisions.md` already carried D99 in this worktree; I read it and left it untouched.

## Implementation notes

- Added `needs_kickoff` and ticket-level `KickoffProposal` storage via nullable
  `tickets.kickoff_proposal`.
- Renamed stored top-level intake context to `kickoff_note`; API/CLI still accept
  `user_note` as a compatibility alias where existing callers use it.
- Ordinary ticket creation now atomically writes `needs_kickoff`,
  `awaiting_approval`, and the pending ticket-level proposal.
- Kickoff approval uses an explicit non-field route/action, optionally applies edited
  title/note, clears the proposal, advances to `needs_success`, sets control to
  `empty`, and rings readiness through actions.
- Seed/import and Chief external-work create/reconcile write already-settled Kickoff
  state with no synthetic proposal.
- Direct title/note edits plus takeover/release are guarded until Kickoff settlement.
- Readiness uses a generic parked-proposal predicate that sees ticket-level Kickoff
  proposals and ordinary field proposals; no Kickoff-specific runner exclusion was
  added.
- Review queue emits `kind: "kickoff"` for pending Kickoff and does not emit
  `field: "kickoff"` proposal events.

## Verification

Planner import source:

```text
$ PYTHONPATH=/Users/khushaljagota/.hermes/worktrees/planning-v2-t_5m7fmdk3/src /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python - <<'PY'
import planner
print(planner.__file__)
from planner.core.db import connect, create_schema
from tempfile import NamedTemporaryFile
f = NamedTemporaryFile(delete=True)
conn = connect(f.name)
create_schema(conn)
print(conn.execute("PRAGMA table_info(tickets)").fetchall()[8][1])
conn.close()
PY
/Users/khushaljagota/.hermes/worktrees/planning-v2-t_5m7fmdk3/src/planner/__init__.py
recap
```

Focused Ruff:

```text
$ PYTHONPATH=/Users/khushaljagota/.hermes/worktrees/planning-v2-t_5m7fmdk3/src /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m ruff check src/planner/core/contracts.py src/planner/core/db.py src/planner/runtime/readiness.py src/planner/tickets src/planner/seed src/planner/cli/main.py tests/unit/test_tickets_engine.py tests/unit/test_ticket_readiness_loop.py tests/unit/test_ticket_edit_api.py tests/unit/test_chief_external_work.py tests/unit/test_readiness_actions.py tests/unit/test_employee_step_runner.py tests/unit/test_return_for_revision.py tests/unit/test_worker_context.py tests/unit/test_authctx_routes.py tests/unit/test_ticket_lifecycle.py tests/unit/test_seed.py
All checks passed!
```

Mypy:

```text
$ PYTHONPATH=/Users/khushaljagota/.hermes/worktrees/planning-v2-t_5m7fmdk3/src /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m mypy src/planner
Success: no issues found in 106 source files
```

Focused pytest:

```text
$ PYTHONPATH=/Users/khushaljagota/.hermes/worktrees/planning-v2-t_5m7fmdk3/src /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest tests/unit/test_tickets_engine.py tests/unit/test_ticket_readiness_loop.py tests/unit/test_db.py::test_kickoff_migration_preserves_existing_user_note_as_settled_kickoff tests/unit/test_seed.py tests/unit/test_ticket_edit_api.py tests/unit/test_chief_external_work.py tests/unit/test_readiness_actions.py tests/unit/test_board_view.py tests/unit/test_employee_step_runner.py tests/unit/test_return_for_revision.py tests/unit/test_worker_context.py tests/unit/test_authctx_routes.py tests/unit/test_ticket_lifecycle.py -q
........................................................................ [ 40%]
........................................................................ [ 81%]
.................................                                        [100%]
=============================== warnings summary ===============================
../../planning-v2/.venv/lib/python3.14/site-packages/fastapi/testclient.py:1
  /Users/khushaljagota/.hermes/planning-v2/.venv/lib/python3.14/site-packages/fastapi/testclient.py:1: StarletteDeprecationWarning: Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.
    from starlette.testclient import TestClient as TestClient  # noqa

src/planner/core/clock.py:27
  /Users/khushaljagota/.hermes/worktrees/planning-v2-t_5m7fmdk3/src/planner/core/clock.py:27: PytestCollectionWarning: cannot collect test class 'TestClock' because it has a __init__ constructor (from: tests/unit/test_ticket_readiness_loop.py)
    class TestClock:

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
```

Diff whitespace:

```text
$ git diff --check
```

No output.
