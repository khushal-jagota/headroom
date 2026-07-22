# Plan review — ACP browser replay presentation transaction

## P1 — Replacement reset uses the wrong protocol state as its base

The plan says every admitted reset derives a fresh candidate from `committedState`. That is correct
only when no replay candidate exists. `admitEnvelope` currently permits an exact-next,
same-generation reset while replay is already in progress, and existing `session_reset` semantics
apply to the current protocol head. In particular, `preserveProtocolRejections: true` must preserve
rejections already admitted into the in-progress candidate. Re-deriving from `committedState` would
silently lose those ordered replay transitions and violate contract requirements 2 and 8.

Exact correction: when a reset starts or replaces replay, apply `session_reset` to
`replayCandidate ?? committedState`, then reduce the reset into that detached result. Keep
`committedState` unpublished and unchanged. Add a controller case with
`reset -> protocol_update_rejected -> exact-next same-generation reset -> ready` and assert the
rejection survives the committed snapshot.

## P1 — The test migration must preserve live controller-action coverage

Several existing assertions in `acp-browser-state.test.mjs` intentionally act on intermediate
fixture state (queue cancellation at sequence 9 and permission response at sequence 16), but that
fixture is a replay bounded by reset at sequence 1 and ready at sequence 21. Under the new contract,
those states must be invisible and those UI actions cannot be performed before ready. Merely
“finishing their replay with ready” would delete coverage for cancel-queued and permission-response
behavior; it would also make the old gap recovery continuation invalid because an abandoned
first-attach candidate leaves no committed cursor and therefore reconnect must receive a reset, not
a bare ready.

Exact correction: explicitly split these tests. Use the replay fixture only to assert hidden state
and final commit. Exercise queue/permission actions with envelopes admitted after a committed ready
(or with an appropriate committed initial state). Rewrite gap/invalid recovery continuations so the
reconnect attach is asserted from the last committed cursor and a cursorless first attach is
bootstrapped by reset before ready.

## P2 — Deferred-prompt test does not prove commit-before-send ordering

The plan checks that reset does not send and ready sends once, but that can pass even if the prompt
is sent before subscribers receive the committed ready snapshot. Requirement 7 is specifically an
ordering contract, and `deliverPrompt` itself publishes an optimistic message before transport send.

Exact correction: instrument the fake transport's prompt `send` to record the latest subscriber
snapshot at send time. Assert the subscriber has already observed the complete ready replay and
that the optimistic prompt publication occurs only after that replay commit; also retain the
single-delivery assertions across later ready/reconnect events.

## Browser RED assessment

The proposed real-controller/component test will genuinely fail before the fix if it loads the
production styles, asserts the transcript actually overflows, and waits for Svelte's post-tick
scroll work after each frame. Keep those three preconditions explicit in the test so a missing style
load or an unconstrained transcript cannot turn the regression into a false pass.

## Resolution review

The revised implementation plan resolves every finding:

- replacement resets now apply `session_reset` to the active protocol head and have the exact
  protocol-rejection preservation regression;
- replay visibility tests are explicitly separated from post-ready queue and permission action
  coverage, and recovery continuations distinguish committed-cursor reconnects from cursorless
  first-attach bootstrap;
- the deferred-prompt test now observes the subscriber snapshot at transport send time and asserts
  replay commit before optimistic publication/send;
- the browser regression explicitly requires production styles, real overflow, and post-tick frame
  synchronization.

**NO VIOLATIONS.**
