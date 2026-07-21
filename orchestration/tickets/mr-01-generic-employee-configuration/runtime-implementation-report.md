# MR-01 conversation runtime implementation report

Status: **READY after bounded review corrections**

## Intent

Implement the provider-neutral catalog and first-session configuration runtime from the
MR-01 contract. The Ticket's backend/model/reasoning trio is consumed as a historical
launch request only while the Ticket is unbound. After the first binding, the ACP
session is authoritative and no load, replacement, compaction, or New Conversation path
reapplies the stored launch values.

## Implemented

- Added immutable catalog option/catalog DTOs, a typed configuration error, a native
  default adapter, and a stable ACP adapter. The ACP adapter discovers semantic `model`
  and `thought_level` categories from a temporary unpublished session, applies model
  before reasoning, consumes refreshed options returned by the model selection, and
  always closes the temporary session and child. Empty ACP descriptions normalize to
  null.
- Added a server-lifetime catalog service cached by `(backend, candidate_model)`. One
  lock coalesces concurrent discovery, and failed discovery is never cached. Catalog
  discovery uses a synthetic identity only and cannot create a Ticket binding.
- Extended materialized backend registrations with a configuration adapter. Existing
  production registrations use the native-default adapter until their provider-specific
  MRs. Composition exposes the shared catalog service and injects the exact first-binding
  and Employee-resolution operations into the registry.
- Extended the ACP child contract with stable `set_config_option` and `close_session`
  calls. The SDK child implements both, while role-skill and Claude decorators forward
  them unchanged.
- Extended `ConversationEmployee` with nullable launch model/reasoning fields. The
  registry applies these only after the first `new_session` and before first binding.
  Model is applied first and its returned option set is used to validate/apply reasoning.
  Invalid or stale configuration retires the unpublished child and cannot bind, publish,
  or prompt.
- Made the initial Ticket binding seam mandatory and fail-closed. The SQLite repository
  compares the prepared complete trio with the current Ticket row inside the same
  `BEGIN IMMEDIATE` transaction that installs the first binding. Ordinary binding CAS
  cannot be used to bypass this check.
- When a first-binding CAS loses, the registry retires its configured candidate,
  re-resolves the already-bound Employee, requires null launch inputs, and loads the
  winning session without configuration. Published bound Employees always have null
  launch inputs. Existing binding, child replacement, compaction, and explicit New
  Conversation paths remain configuration-free.
- Extended the scripted ACP agent with model-dependent config options, set/close audit
  events, refreshed reasoning choices, deterministic failure controls, and prompt-time
  effective configuration evidence. The scripted E2E backend now uses the generic ACP
  adapter.

## Files

Production runtime:

- `src/planner/conversation/employee_configuration.py`
- `src/planner/conversation/backend_contracts.py`
- `src/planner/conversation/backend_catalog.py`
- `src/planner/conversation/composition.py`
- `src/planner/conversation/contracts.py`
- `src/planner/conversation/employee_registry.py`
- `src/planner/conversation/sdk_child.py`
- `src/planner/conversation/role_skill_kickoff.py`
- `src/planner/conversation/sqlite_binding_repository.py`
- `src/planner/conversation/claude_backend.py`
- `src/planner/conversation/__init__.py`

Focused test/support changes:

- `tests/support/probe.py`
- `tests/support/acp_scripted_agent.py`
- `tests/support/acp_e2e_server.py`
- `tests/support/acp_runtime_subject.py`
- `tests/support/acp_in_memory_binding_repository.py`
- `tests/unit/test_employee_configuration_catalog.py`
- `tests/unit/test_acp_binding_repository.py`
- `tests/unit/test_acp_employee_registry.py`
- `tests/unit/test_acp_employee_child.py`
- `tests/unit/test_role_skill_kickoff.py`
- `tests/unit/test_claude_acp_backend.py`
- `tests/unit/test_acp_step_gateway.py`
- `tests/unit/test_employee_step_runner.py`
- `tests/unit/test_conversation_hub.py`
- `tests/unit/test_conversation_turn_broker.py`
- `tests/unit/test_conversation_permission_broker.py`
- `tests/e2e/test_acp_conversation.py`

## Focused verification

Commands run on the settled runtime slice:

```text
.venv/bin/ruff check <27 scoped runtime/support/test files>
All checks passed!

.venv/bin/mypy src/planner/conversation
Success: no issues found in 29 source files

.venv/bin/pytest -q \
  tests/unit/test_employee_configuration_catalog.py \
  tests/unit/test_acp_binding_repository.py \
  tests/unit/test_acp_employee_registry.py \
  tests/unit/test_acp_employee_child.py \
  tests/unit/test_role_skill_kickoff.py \
  tests/unit/test_claude_acp_backend.py \
  tests/unit/test_acp_conversation_composition.py \
  tests/unit/test_acp_step_gateway.py \
  tests/unit/test_conversation_hub.py \
  tests/unit/test_conversation_turn_broker.py \
  tests/unit/test_conversation_permission_broker.py
........................................................................ [ 26%]
........................................................................ [ 52%]
........................................................................ [ 78%]
..........................................................               [100%]

.venv/bin/pytest -q tests/e2e/test_acp_conversation.py \
  -k employee_configuration_catalog_does_not_bind
.                                                                        [100%]
```

The end-to-end proof checks that catalog discovery creates no Ticket binding/session,
the explicit trio is applied before first prompt, the prompt observes the selected pair,
exactly one binding is installed, and the Ticket keeps the requested trio as historical
launch data. Pytest emitted only the existing Starlette `httpx` deprecation warning.
`git diff --check` is clean. The program-level `./verify` remains reserved for final
integration.

## Bounded review corrections

The independent runtime review found no production defect and requested two missing
load-bearing proofs. Both were added without changing product code:

- `test_first_automatic_prompt_uses_selected_model_then_reasoning` runs an eligible
  Ticket through the real `EmployeeStepRunner`, `AcpStepGateway`, conversation
  composition, SDK child, scripted subprocess backend, and generic ACP adapter. Its
  initial-binding observer proves both selected config writes already exist before the
  binding transaction, the subprocess audit proves binding precedes the first prompt,
  and that prompt observes `probe-alt` / `probe-low`. The test also proves exactly one
  generation-one binding and one completed Employee-step run, while the Ticket retains
  the pair as historical launch data.
- `test_bound_load_replacement_compaction_and_new_conversation_do_not_reapply_kickoff_values`
  seeds a bound Employee carrying deliberately stale historical launch inputs, then
  exercises bound load and attach, requested-cancel replacement, compaction recovery,
  and explicit New Conversation with an observing generic adapter. It proves zero
  adapter configuration calls and zero child `set_config_option` calls across the whole
  lifecycle.

Correction gates:

```text
.venv/bin/ruff check tests/unit/test_employee_step_runner.py \
  tests/unit/test_acp_employee_registry.py
All checks passed!

.venv/bin/pytest -q tests/unit/test_employee_step_runner.py \
  tests/unit/test_acp_employee_registry.py
.............................................................            [100%]
```
