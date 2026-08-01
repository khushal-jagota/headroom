# Implementation review disposition

## Decision

The first implementation is not accepted for integration. Every review finding is
resolved before the branch meets `origin/staging`.

## Structural standards finding

**Accepted.** The first ticket lock placed framework-free policy and six contract
shapes beside a Svelte component. That lock was written by this run; it was not a live
owner decision overriding `PRINCIPLES.md`.

The contract is corrected so:

- shared shapes live in
  `web/src/lib/conversation/runControls/contracts.ts`;
- pure rules live in
  `web/src/lib/conversation/runControls/logic/runSelection.ts`;
- the Svelte renderer remains in the component layer and imports its shapes from the
  contract.

This creates one semantic run-controls package and keeps business rules out of the
component layer.

## Missing refusal proof

**Accepted.** The browser harness must choose non-null model and effort values, observe
their ordinary-send clearing, refuse that send, and observe the exact values return
through the public component DOM. The test may not inspect Svelte local state.

## Verification evidence

**Accepted.** The corrected branch will run the affected focused gates fresh and retain
their complete output. The final combined revision will then run the one canonical
`./verify` required by the owner.

## Incorrect size sentence

**Accepted.** The evidence will report the actual final line counts once, consistently.
