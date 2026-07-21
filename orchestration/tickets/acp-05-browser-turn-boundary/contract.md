# ACP-05 browser turn-boundary correction

## Why this ticket exists

Real Computer Use dogfood on the production-served Panels Workspace queued a second prompt while a
Hermes turn was active. Both turns settled in order, but the browser rendered their missing-ID agent
chunks as one paragraph: `BASE TURN COMPLETEQUEUED MESSAGE DELIVERED.`

Hermes ACP legitimately omits message IDs on these live text updates. Panels publishes the queued
human echo before broker delivery, so that optimistic user boundary can precede the first turn's
final agent chunk. The broker's terminal activity/receipt is therefore the authoritative place to
close the current fallback agent-message group.

## Frozen behavior

1. A terminal activity state (`idle`, `interrupted`, or `failed`) closes only the browser reducer's
   current missing-ID agent group. It does not delete transcript content, reset the turn ordinal, or
   invent a user message.
2. An `interrupted` delivery receipt also closes that group because Send Now can start its successor
   without an intervening idle activity.
3. Non-terminal activity and delivery states—including thinking, working, compacting,
   waiting-for-permission, accepted, queued, started, and steer acknowledgement—do not split a live
   agent response.
4. After a queued or Send Now successor starts, its first missing-ID agent chunk creates a distinct
   message block even when the queued human echo arrived before the predecessor's final chunk.
5. Existing persistent delivery acknowledgement remains unchanged. The composer-level `started`
   receipt is intentional and is not part of this correction.

## Required proof

- Browser reducer/controller regression reproducing the real sequence: queued optimistic/human echo
  before reply 1, reply 1, terminal idle, queued successor start, reply 2. Assert two distinct agent
  messages and exact separate text.
- Focused Send Now proof that an interrupted receipt separates consecutive missing-ID agent chunks.
- Existing ACP browser state and component suites remain green.
- Focused TypeScript/Svelte checks and production frontend build pass. Do not run canonical
  `./verify`; ACP-10 owns the one final run.

## Allowed files

- `web/src/lib/acp/conversationState.ts`
- `web/tests/acp-browser-state.test.mjs`
- this ticket's plan/report/review/evidence files
- `PROGRESS.md`
- `decisions.md`

No backend, wire contract, donor, component, generated distribution, config, runtime database, or
Hermes-checkout file is in scope.
