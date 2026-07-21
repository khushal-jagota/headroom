# ACP-05 browser turn-boundary implementation plan

Plan authored from the focused sub-agent diagnosis and frozen by the orchestrator. This trivial
correction collapses a separate plan-review round: the diagnosis already inspected the live Hermes
emission, hub ordering, broker settlement order, reducer, donor, and existing tests.

## Production change

In `web/src/lib/acp/conversationState.ts`, add one small helper that returns the same conversation
state with only these fallback fields cleared:

- `activeMissingIdGroup: null`
- `currentAgentMessageId: null`

Keep `turnOrdinal` and `currentRole` unchanged.

Apply the helper after storing an activity whose state is `idle`, `interrupted`, or `failed`. Apply
it after storing an `interrupted` delivery receipt as well. All other activity and receipt states
retain the current reducer behavior.

## Focused proof

Extend `web/tests/acp-browser-state.test.mjs` with:

1. The exact queued sequence from dogfood, including an optimistic queued human prompt before the
   predecessor's final missing-ID agent chunk, terminal idle, then the successor's missing-ID agent
   chunk. Assert two agent message IDs and two exact text values.
2. A Send Now sequence in which an interrupted receipt separates two missing-ID agent chunks.
3. A non-terminal control proving thinking/working/queued/started does not split one response.

Run the ACP browser state and component suites, TypeScript/Svelte diagnostics, and production web
build. Record exact commands/output in `implementation-report.md`. Do not touch generated
distribution in this ticket and do not run canonical `./verify`.

## Review

One independent focused diff review checks only the frozen terminal-state set, preservation of turn
ordinal/current role, and the three regressions. A second review occurs only for a concrete finding.
