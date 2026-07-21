# ACP-00a — typed terminal-state wire correction

Small contract correction discovered while cutting ACP-02/03 against ACP-00. ACP embeds only a
`terminalId` in `ToolCallContent`; output and exit state are owned by the ACP Client through the
reverse `terminal/*` service. Panels therefore cannot satisfy the frozen terminal rendering rule by
forwarding `SessionNotification` alone. This ticket adds the one missing typed Panels envelope before
ACP-02 or ACP-03 implementation begins. It does not implement a terminal service or UI.

## Outcome

The server/browser wire has an eleventh server payload, `terminal_state`. Its Panels-owned payload
contains:

- the exact terminal ID;
- lifecycle `active | released`;
- the exact SDK `TerminalOutputResponse`, including accumulated output, truncation, and optional exit
  status.

Running versus exited is derived from the SDK response's optional `exitStatus`; Panels does not copy
that union. The reverse terminal owner will emit a new snapshot whenever observable output, exit, or
release state changes and replay the latest snapshots after typed session load. Terminal text remains
tool content and never becomes assistant prose.

## Frozen rules

- Add `ConversationTerminalState` to the Python Panels contracts. It validates a non-empty terminal
  ID and permits no free-form fields.
- Add `TerminalStateEnvelope` to the server discriminated union with discriminator
  `terminal_state`. It uses the same employee/session/generation/sequence identity as every envelope.
- Mirror only the Panels wrapper in `web/src/lib/acp/contracts.ts`; import `TerminalOutputResponse`
  from `@agentclientprotocol/sdk==1.2.1`.
- Update canonical Python-emitted JSON fixtures and structural TypeScript contract tests. Add no
  browser action, generic data payload, terminal polling action, JSON-RPC method, or transcript text
  fallback.
- Preserve all existing ten server variants and five browser actions byte-for-byte.

## Allowed files

- `src/planner/conversation/contracts.py`
- `src/planner/conversation/wire_contracts.py`
- `src/planner/conversation/__init__.py`
- `tests/unit/test_conversation_contracts.py`
- `tests/support/acp_fixture_writer.py`
- `tests/fixtures/acp/**`
- `web/src/lib/acp/contracts.ts`
- `web/tests/acp-contracts.test.mjs`
- `orchestration/tickets/acp-00-contracts-conformance/contract.md`
- `orchestration/tickets/acp-00a-terminal-state-wire/**`

No dependency, route, database, runtime, Svelte, CSS, legacy, config, docs, or Hermes-checkout changes.

## Named acceptance

1. Python tests accept active/running, active/exited, truncated, and released terminal snapshots and
   reject unknown lifecycle, empty ID, partial SDK output, and extra fields.
2. Server-envelope tests prove exact terminal payload serialization and all common identity/sequence
   validation.
3. Canonical Python fixture and TypeScript structural test agree; the TypeScript source imports the
   SDK terminal response rather than copying it.
4. Existing ACP-00 focused Python and web tests stay green.

## Review and integration

This is a bounded public-contract repair, so planning and implementation may be collapsed into one
sub-agent pass. A different sub-agent performs one focused implementation review. A second review is
used only for a material unresolved violation.
