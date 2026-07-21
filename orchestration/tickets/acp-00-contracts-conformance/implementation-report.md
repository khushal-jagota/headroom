# ACP-00 implementation report

## Outcome

ACP-00 is implemented within the ticket boundary. It adds the frozen Python conversation contract
package, the Panels-only TypeScript wire mirror, exact dependency pins, the byte-for-byte donor
slice, canonical Python-to-TypeScript fixtures, and a reusable official-SDK conformance harness.
It adds no route, runtime composition, database work, legacy change, or UI component.

## Dependency and donor work

- Pinned `agent-client-protocol==0.11.0` in both `pyproject.toml` and `requirements.txt`.
- Pinned `@agentclientprotocol/sdk@1.2.1`, `zustand@5.0.13`, and `zod@4.4.3` as exact direct
  dependencies and regenerated `web/package-lock.json`.
- Appended `node tests/acp-contracts.test.mjs` to the existing web test script.
- Copied only the four reviewed `packages/core` source files from commit
  `525a9d83c5ace577ac0417bf82bf983da4042663`. Their committed SHA-256 values are:
  - `src/types/index.ts`: `7e85ca21deb274ed4d28311e8249a34632f2a86d0cedace28cee379d27b8cfd4`
  - `src/store/sessionStore.ts`: `6d89afc95fbc2758d00293feb6da2c234781403e49abb3dc1d54a87aa34d902f`
  - `src/utils/id.ts`: `74deabced24a6e5342c2aa43c0df6b515d45a0496d144ce792aa74051315a939`
  - `src/transport/types.ts`: `c81be948d5dc9c458ad6fe06ae262154ee242e33f58cd6848e5bab79b9d22343`
- Added the required provenance record and standard MIT license. There are no local source
  corrections in ACP-00.

## Contract work

- Added frozen, extra-forbidding Python values for employee/session identity, activity, delivery,
  queues, compaction, permission request/outcome, backend definitions, strategies, child/factory
  protocols, callbacks, and the single 300-second permission timeout default.
- Embedded the exact generated ACP `PromptRequest`, `SessionNotification`, permission request, and
  permission response models. ACP payload unions are not copied into Panels types.
- Added closed discriminated server-envelope and browser-action unions, inner/outer session checks,
  nested sequence/generation checks, SDK alias serialization, and fail-closed conversion of
  unknown/missing/partial agent updates into `protocol_update_rejected`.
- Raw browser-action and agent-notification ingress now validates strictly by wire alias only. Trusted
  Python model construction still accepts field names, but snake-case wire keys and string-coerced
  numeric fields cannot cross either raw boundary.
- Added the TypeScript Panels envelope/action mirror. ACP types come from
  `@agentclientprotocol/sdk`; donor transcript state types come from the pinned vendor paths.
- Added deterministic canonical server/action fixtures emitted by the Python models through
  `acp.utils.serialize_params`. The browser test compiles those literals against the exported
  TypeScript unions and separately enforces the SDK/donor type-ownership boundary.

## Conformance harness

- Added an in-memory newline-delimited stdio agent implemented with `acp.run_agent` and the official
  agent-side connection. It supports initialize/new/load/prompt/cancel, typed replay before the load
  response, permission reverse calls, delayed/concurrent updates, missing IDs, automatic compaction
  provenance, cancellation, isolated malformed-update emission, and deterministic process death.
- Added an official client-side reference subject launched only through
  `acp.stdio.spawn_agent_process`. It uses the SDK stream observer for wire evidence and drives the
  returned `ClientSideConnection` for all normal protocol traffic.
- The only hand-shaped JSON-RPC notification is the explicitly isolated test-only malformed-update
  helper, because the SDK correctly refuses to construct unknown/partial session updates. There is
  no production JSON-RPC implementation.
- Added the ordered ten-probe manifest with the reviewed ACP-01/02/03 production owners, typed
  per-probe evidence, one assertion per probe, a reference subject that passes all ten, and ten
  targeted mutations. Each mutation fails only its corresponding assertion.
- The thought probe reduces live and load-replayed notifications independently through the same
  typed reducer and checks both chunk and concatenated thought forms against both assistant outputs.
  Its mutation corrupts replay output specifically.
- Probes 6–9 now derive evidence from exercised test-only mechanisms: a stored binding refreshed by
  an actual SDK load, a declared steer operation and broker-owned queue/send-now methods, observed
  explicit and automatic compaction transitions, and enqueue-only callback ingress feeding one
  ordered reducer plus an independently recorded broadcast.

## Implementation review round 1

All three findings in `implementation-review-round-1.md` were accepted and corrected. The closed
wire no longer admits Python aliases or integer coercion, replay thought flattening is now observable,
and durable refresh, delivery ownership, compaction, and callback-order probes no longer rely on
pre-shaped evidence.

## Verification note

The exact focused sequence from `plan.md` is green on the corrected settled tree; verbatim output is
in `focused-test-evidence.md`. Per dispatch, `./verify` was not run.
