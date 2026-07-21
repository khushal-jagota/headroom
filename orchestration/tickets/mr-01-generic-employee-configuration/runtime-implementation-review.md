# MR-01 generic runtime/domain implementation review

Verdict: **READY**

The reviewed production implementation matches the contract in the inspected paths:

- `employee_launch_model` and `employee_launch_reasoning_effort` remain Ticket fields and
  are cleared from bound `ConversationEmployee` resolution;
- the only call to `configure_initial_session` is in the unbound `new_session` branch,
  before initial binding;
- the SQLite initial CAS compares the complete prepared trio under the same
  `BEGIN IMMEDIATE` transaction that installs the binding;
- a first-binding loser closes its candidate, re-resolves the bound Employee, requires
  null launch inputs, and loads the winner without configuration;
- the stable ACP adapter applies model before reasoning using the refreshed option set;
- catalog discovery closes both the temporary session and child in `finally` and caches
  only successful results.

The initial review found two missing load-bearing proofs. Both are resolved by focused
tests without product-code changes.

## Findings

### P1 — The automatic-first launch path was not proved — RESOLVED

The contract requires browser-first and first-automatic-work demand to apply the same
launch configuration before binding (`orchestration/acp-model-selection/contract.md:145`),
and the plan names `test_first_automatic_prompt_uses_selected_model_then_reasoning`
(`orchestration/tickets/mr-01-generic-employee-configuration/plan.md:205`). The only
scripted end-to-end launch proof is browser-first:
`tests/e2e/test_acp_conversation.py:693-834` opens `/api/conversation`, attaches the
browser, and only then prompts. No test starts an unbound Ticket through
`AcpStepGateway` with an explicit model/reasoning pair and asserts the ordered
`set_config_option` audit precedes its first automatic prompt and binding.

Resolved by
`tests/unit/test_employee_step_runner.py:184-304`. The test runs an eligible unbound
Ticket through the real `EmployeeStepRunner`, `AcpStepGateway`, conversation
composition, SDK child, scripted subprocess, and generic adapter. Its initial-binding
observer proves ordered model/reasoning writes already exist before the initial CAS;
the subprocess audit proves the prompt follows them and observes the selected pair.
It also proves one generation-one binding, one completed Employee-step run, and the
Ticket's retained historical launch values.

### P1 — Bound lifecycle paths lacked the required negative configuration-call proof — RESOLVED

The plan explicitly requires
`test_bound_load_replacement_and_new_conversation_do_not_reapply_kickoff_values`
(`orchestration/tickets/mr-01-generic-employee-configuration/plan.md:193`), covering
bound load, replacement, compaction recovery, and New Conversation. The new registry
test proves only New Conversation: after clearing the operation audit it calls
`registry.new_conversation(...)` and asserts `operations == ["new_session"]`
(`tests/unit/test_acp_employee_registry.py:599-603`). Existing attach,
requested-cancel replacement, and compaction tests exercise those mechanisms, but do
not install an observing configuration adapter or assert zero `set_config_option`
calls.

Resolved by
`tests/unit/test_acp_employee_registry.py:608-694`. It seeds a bound Employee carrying
deliberately stale historical launch inputs, installs an observing adapter, then
exercises ordinary bound load, attach replay load, requested-cancel replacement,
compaction recovery, and explicit New Conversation. It proves zero adapter
configuration calls and zero child `set_config_option` calls across the full sequence.

## Narrow correction gate

```text
.venv/bin/pytest -q \
  tests/unit/test_employee_step_runner.py::test_first_automatic_prompt_uses_selected_model_then_reasoning \
  tests/unit/test_acp_employee_registry.py::test_bound_load_replacement_compaction_and_new_conversation_do_not_reapply_kickoff_values
..                                                                       [100%]
```

## Other reviewed seams

No P0/P1 violation was found in the exact PUT/catalog shape, dependency
normalization, first-binding transaction/race behavior, fail-closed Ticket initial-CAS
seam, winner re-resolution, model-before-reasoning sequencing, or temporary catalog
session cleanup.
