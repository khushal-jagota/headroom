# MR-01 domain/API implementation report

Status: **READY for focused independent review**

## Intent

Implement the Ticket-owned storage, Worker-type defaults, pure normalization, atomic
Kickoff writer, REST shell, and server-owned editable flag for MR-01. Provider discovery,
first-session ACP application, binding implementation, and the browser component remain
the parallel runtime/UI slices.

## Implemented

- Schema v27 adds nullable `employee_launch_model` and
  `employee_launch_reasoning_effort` columns to Tickets. The additive migration is one
  immediate transaction, preserves existing Ticket and binding values, and seeds both
  launch values to null. The binding schema has no duplicate configuration columns.
- `EmployeeLaunchConfiguration` is the Ticket-owned complete launch snapshot. Ticket
  JSON serves both historical launch inputs. Ticket detail also serves the exact
  `employee_configuration_editable` predicate shared by the writer: Kickoff stage,
  `empty`/`awaiting_approval`, no Ticket session mirror, and no binding row.
- Worker profiles now truthfully name and manifest
  `default_employee_model` / `default_employee_reasoning_effort`. Production types stay
  Hermes/null/null. Creation copies the complete Worker default once; a changed
  create-time backend override clears backend-specific model/reasoning values.
- The old backend-only route/writer is replaced by exact complete-body
  `PUT /api/tickets/{ticket_id}/employee-configuration`. The API fetches dependent
  options before the write, and the immediate writer compares the exact pre-fetch
  Ticket snapshot before normalization and commit. It emits one complete
  `employee_configuration` Ticket event. A complete no-op remains a no-op after freeze.
- Pure Ticket logic clears model/reasoning when Worker changes, validates every
  same-backend explicit model, preserves only reasoning advertised by the effective
  model, and clears a carried reasoning value when a model change makes it inapplicable.
  Switching back to a Worker does not restore Worker-type defaults.
- `GET /api/employee-configuration-catalog` serializes the shared runtime catalog DTO.
  Missing composition and adapter/discovery failures become the calm product 503
  envelope without leaking backend details; cancellation is not caught.
- The existing CLI `ticket set ... employee-backend` command now sends the complete
  configuration body and resets dependent launch values to backend-native defaults.

## Focused verification

Commands run on the settled domain/API tree:

```text
.venv/bin/ruff check <13 scoped production files and 5 scoped test files>
All checks passed!

.venv/bin/mypy --strict <13 scoped production files>
Success: no issues found in 13 source files

.venv/bin/pytest -q tests/unit/test_db.py tests/unit/test_worker_type_registry.py \
  tests/unit/test_worker_type_manifest_endpoint.py tests/unit/test_ticket_edit_api.py \
  tests/unit/test_external_work_generic.py
........................................................................ [ 55%]
..........................................................               [100%]
```

The only pytest output beyond passing tests was the existing Starlette `httpx`
deprecation warning. `git diff --check` is clean for the complete scoped diff. The
program-level `./verify` remains reserved for the final integrated MR-01 through MR-04
tree.

