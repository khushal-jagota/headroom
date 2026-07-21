# ACP-10 canonical-gate fixture closure

Status: **bounded correction after retained frozen-snapshot failure**.

## Outcome

Close only the four Ruff findings and 14 unit failures recorded in
`data/verify/acp-10-final.log`. Preserve the delivered product contracts: every Ticket and Worker
manifest carries its selected/default `employee_backend`; no production behavior or assertion
strength is weakened.

The failed full run remains retained and is not rerun by implementers.

## Lane A — Ruff-only formatting

Allowed files:

- `src/planner/conversation/__init__.py`
- `tests/unit/test_conversation_hub.py`
- `tests/unit/test_days.py`
- `tests/unit/test_sprints.py`

Organize the existing import block and wrap only the three reported SQL strings. Run Ruff only on
these four files. No behavior or assertion change.

## Lane B — Worker-manifest fixtures

Allowed files:

- `tests/unit/test_exploration_worker_type.py`
- `tests/unit/test_go_no_go_gate.py`
- `tests/unit/test_initiative_planning_worker_type.py`
- `tests/unit/test_stage_ownership_backend.py`
- `tests/unit/test_worker_type_stage_contracts.py`

Update old expected manifests/registry fixtures to include the already-required
`default_employee_backend`, using the production value `hermes` or the exact configured fake backend.
Do not remove the field from actual output, weaken equality, or change production source. Run only the
five failing test files plus Ruff on them.

## Lane C — Ticket-constructor fixtures

Allowed files:

- `tests/unit/test_generic_field_storage.py`
- `tests/unit/test_value_edit_logic.py`

Supply the already-required `employee_backend` in direct `Ticket(...)` fixtures, using `hermes` unless
the test installs another exact backend. Do not add a production default or change product logic. Run
only the two failing test files plus Ruff on them.

## Closeout

Each lane records its commands/results. Root inspects the combined diff, runs one combined focused
Ruff + named-unit gate, records a bounded review disposition, and establishes a fresh no-writer
manifest before any later full-verification decision. No lane may run `./verify`, start a server or
browser, change docs/memory/evidence, touch packages/generated assets, or modify files outside its
allowlist.
