# ACP-00b — typed Steer capability on connection state

Small public-wire correction discovered by ACP-03 planning. The reviewed program requires unsupported
Steer to be visibly unavailable and forbids browser inference from backend name, command catalog, or
display text. The frozen connection payload carries no machine-readable turn capability, so the UI
cannot meet that rule. This ticket adds the one missing boolean before ACP-03 implementation.

## Outcome and frozen shape

Add required `supports_steer: bool` / `supportsSteer: boolean` to `ConnectionPayload`. Every
`connection` envelope (`reset | ready | closed | error`) carries it. ACP-04 will source it from the
selected `AgentBackendDefinition.turn_capabilities.supports_steer`; fixtures use explicit values.

The browser uses this field only to enable/disable the separate mid-turn Steer delivery control.
Queue and Send Now remain common broker behavior. Slash-command visibility still comes only from
ACP `available_commands_update`; `/steer` appearing there is not capability evidence. No backend key,
new event variant, generic capability dictionary, display-text parser, or browser action is added.

## Allowed files

- `src/planner/conversation/wire_contracts.py`
- `src/planner/conversation/__init__.py`
- `tests/unit/test_conversation_contracts.py`
- `tests/support/acp_fixture_writer.py`
- `tests/fixtures/acp/**`
- `web/src/lib/acp/contracts.ts`
- `web/tests/acp-contracts.test.mjs`
- `orchestration/tickets/acp-00-contracts-conformance/contract.md`
- `orchestration/tickets/acp-00b-steer-capability-wire/**`

No other source, dependency, route, runtime, Svelte, database, CSS, config, legacy, documentation, or
Hermes-checkout file may change.

## Named acceptance

1. Python strictly requires a real boolean on every connection payload and rejects missing, null,
   string, integer, and extra capability fields.
2. Canonical fixtures cover reset/ready with Steer supported and closed/error with explicit values;
   TypeScript structurally requires the same field.
3. Existing eleven server variants, five browser actions, exact terminal state, and every unrelated
   payload remain unchanged.
4. Focused Python conversation contracts and web ACP contracts pass.

## Review and integration

Planning and implementation are collapsed for this bounded correction. A different sub-agent gives
one focused implementation review; a second round is reserved for a material production violation.
