# ACP browser replay presentation transaction

## Problem

The server already constructs and validates one ordered `reset -> replay -> ready` snapshot, but it
delivers that snapshot as individual WebSocket envelopes. The browser controller currently publishes
after every accepted envelope. Svelte therefore renders a long restored transcript in several DOM
commits and the existing follow-scroll code advances to each new bottom. The pane is technically at
the bottom throughout, but the user sees the conversation travel from its beginning to its end.

The server replay queue, ACP wire protocol, and backend adapters are not the defect. The missing
boundary is between browser protocol admission and browser presentation.

## Owned boundary

`web/src/lib/acp/conversationController.ts` owns admission, ordered reduction, recovery, and
subscriber publication for one browser conversation. It must treat an admitted replay from `reset`
through `ready` as one presentation transaction.

## Required behavior

1. An admitted `connection/reset` starts a detached replay candidate. The candidate begins from the
   controller's current committed state and applies the existing `session_reset` semantics.
2. Every replay envelope is still validated, cursor-checked, and reduced in exact wire order. This
   change does not weaken protocol correctness or skip any state transition.
3. Subscribers and `snapshot()` continue to see the last committed conversation while the candidate
   is incomplete. On a first attach, that is the ordinary empty/connecting conversation. On a
   refresh or generation cutover, that is the previously complete transcript.
4. No intermediate replay candidate is published. An admitted `connection/ready` is reduced into the
   candidate, clears a prior recoverable connection error under the existing rules, commits the
   candidate, and publishes the complete replay exactly once.
5. After `ready`, ordinary live envelopes retain their current incremental publication behavior.
6. If an invalid envelope, sequence gap, socket close, socket error, replay-unavailable close, or
   disposal occurs before `ready`, the candidate is discarded. No partial restored transcript may
   become visible. Existing recovery/error publication and reconnect behavior remains intact and is
   applied to the last committed state.
7. A deferred first prompt remains pending until the admitted `ready`; it is delivered once only
   after the complete candidate has committed.
8. Same-generation and newer-generation resets keep their existing identity, generation, sequence,
   and protocol-rejection rules. Duplicate and stale envelopes keep their existing behavior.

## Explicit non-goals

- No change to the Hub, outbound queue, WebSocket frame format, replay byte limits, or adapters.
- No giant replay array/frame, browser-side canonical transcript cache, pagination, or virtualization.
- No new visual control, animation, placeholder, or loading state.
- No change to live follow-scroll behavior after a conversation is ready.

## Acceptance evidence

1. A controller regression proves a long replay produces no transcript-bearing subscriber snapshots
   before `ready`, then one complete publication at `ready`.
2. A controller regression proves a reset over an existing committed conversation retains that
   complete conversation until `ready`, then swaps to the new complete replay once.
3. Controller regressions prove failure before `ready` exposes no partial replay and preserves the
   existing recoverable error/reconnect behavior, while post-ready live envelopes publish normally.
4. The component/browser seam proves a long replay produces one transcript height/bottom placement,
   rather than multiple bottom-following height jumps.
5. Existing controller, browser state, component, type, and production build checks remain green,
   followed by the repository's single canonical `./verify` on the settled tree.
