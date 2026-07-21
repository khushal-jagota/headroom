# ACP-03 implementation review — round 1

## Verdict

**NOT READY.** The focused gates pass, but the production browser boundary admits invalid frozen-wire
payloads, higher-generation reset admission can change the attached entity, and a server human echo
does not terminate the active missing-ID agent group. Those are production correctness failures. The
conformance and component suites also do not exercise the subjects required by the reviewed plan, so
their green result is not sufficient integration evidence.

## Findings

### 1. High — the browser transport admits invalid Panels-owned payloads instead of failing closed

`web/src/lib/acp/panelsTransport.ts:149-157` validates only the receipt discriminator and queue
position. It does not require `reason` for `rejected`, and it permits a reason on `accepted`, `queued`,
or `started`, contrary to the frozen `TurnDeliveryReceipt` invariants. The envelope-specific fold at
`web/src/lib/acp/panelsTransport.ts:247-275` also omits the frozen cross-field sequence checks:
`activity.sequence`, permission `openedSequence`, and permission `settledSequence` are not required
to equal the outer envelope sequence.

Focused runtime calls to the production `validateServerEnvelope` confirmed that all of these invalid
values return `ok: true`:

- a rejected receipt with no reason;
- an accepted receipt with a reason;
- an activity whose payload sequence is 1 under outer sequence 2;
- a permission request/outcome whose opened/settled sequence is 1 under outer sequence 2.

These are Panels-owned payload rules, not SDK deep-schema duplication. Once admitted they are cast to
`ServerEnvelope` and reduced as trusted state. That breaks the strict browser boundary and permits
cursor sequence and displayed payload state to disagree. The only negative payload assertion in
`web/tests/acp-browser-state.test.mjs:417-423` is a partial thought update, so the named strict payload
validation proof does not cover the frozen Panels invariants.

### 2. High — a higher-generation reset can silently switch the controller to the wrong entity

The same-generation path calls `sameIdentity` at
`web/src/lib/acp/conversationController.ts:165-169`, but the higher-generation reset path at
`web/src/lib/acp/conversationController.ts:183-190` checks only the reset discriminator and matching
generation. It does not preserve the established `entityKind`/`entityId`.

A focused production-controller probe admitted generation 2 with `entityId: "ticket-wrong"` after a
generation-1 reset for `ticket-good`; the public cursor moved to the wrong ticket with no recoverable
error and without closing the socket. A reset may establish a new ACP session, but it may not move an
attached employee conversation to another entity. The fixture test covers wrong identity only in the
same generation (`web/tests/acp-browser-state.test.mjs:384-392`), leaving this branch unproved.

### 3. High — a non-optimistic human echo does not break missing-ID grouping or preserve turn order

`replacePromptMessage` at `web/src/lib/acp/conversationState.ts:382-409` inserts the user message but
does not advance the turn or change `fallback.currentRole`, clear `activeMissingIdGroup`, or clear
`currentAgentMessageId`. Consequently, this valid sequence:

1. missing-ID agent text `before`;
2. `human_echo` for a client message not present in local optimistic metadata;
3. missing-ID agent text `after`;

produces one pre-echo agent message containing `beforeafter`, followed by the human message in the
timeline. The later assistant text is therefore rendered before the human turn that caused it. This
violates turn/role-scoped deterministic fallback and transcript ordering. The current human-echo
fixture has no later missing-ID agent chunk, so the live controller test cannot detect the fault.

### 4. High — ACP-00 browser conformance evidence bypasses the required production pipeline

`web/tests/acp-browser-conformance.test.mjs:19-20` bundles only `conversationState.ts`, and
`web/tests/acp-browser-conformance.test.mjs:35-43` feeds envelopes directly to
`reduceConversationState`. It never exercises `panelsTransport` validation or controller identity,
generation, sequence, reset, or reconnect admission. The grouping evidence is additionally built
from a hand-shaped update array at `web/tests/acp-browser-conformance.test.mjs:67-98`; after filtering
the stable ID it supplies only one boundary-reset ID, making the frozen uniqueness assertion
vacuous for multiple boundaries.

The adapter correctly calls the existing ACP-00 assertions and mutation helper, but it proves only a
direct reducer projection. A broken transport/controller can still report that probes 2, 3, 4, and
10 pass. The reviewed plan and contract require evidence from the actual
transport → controller → reducer subject.

### 5. High — the Svelte acceptance suite is source-string proof, and a required alert behavior is absent

`web/tests/acp-browser-components.test.mjs:21-73` compiles the nine files and then uses regular
expressions over their source. It never mounts a component, supplies snapshot props, clicks a
disclosure/delivery/permission/queue control, checks rendered order, or inspects the accessibility
tree. As a result, matching words such as `aria-expanded`, `supportsSteer`, `onSelect`, and
`latestReceipt` are treated as behavioral proof.

There is already a missed production violation: `ConversationStatus.svelte:48-50` always renders a
polite `role="status"`; neither it nor the inline protocol row provides the contract's alert when a
connection/protocol failure is newly raised. The test asserts only that the source contains
`role="status"` (`web/tests/acp-browser-components.test.mjs:54-56`). The component acceptance cannot
be considered complete until the nine contracts and accessibility interactions are exercised at
runtime.

### 6. Medium — a failed permission send leaves the blocking UI permanently submission-disabled

`respondToPermission` sets `submittingOptionId` before sending at
`web/src/lib/acp/conversationController.ts:306-319`, but a `{ ok: false }` send result is returned
without rolling that local state back or otherwise settling it. A focused controller probe with a
transport returning `{ ok: false, reason: "send failed" }` left the request pending with
`submittingOptionId: "allow"`. Since `PermissionPrompt.svelte:30` disables every option whenever that
field is non-null, the user cannot retry unless a later disconnect/reset/outcome happens. No focused
test covers local permission-send failure.

### 7. Medium — disposal publishes one final snapshot instead of becoming silent

The corrected plan requires the controller to mark itself disposed and clear subscribers before
closing, with no further publication. `dispose` instead constructs a disposed snapshot and invokes
every subscriber at `web/src/lib/acp/conversationController.ts:331-344`, then sets the controller's
`disposed` guard. A focused subscription probe observed the callback count increase from 1 to 2 on
`dispose()` itself. The existing test at `web/tests/acp-browser-state.test.mjs:409-415` checks only
that a late socket feed cannot mutate `snapshot()`; it never asserts subscriber silence.

### 8. Medium — the composer does not select the latest receipt event

`AcpComposer.svelte:39` computes the live receipt as `Object.values(receipts).at(-1)`. Record order is
key insertion order, so updating an older client-message receipt after a newer key was inserted does
not move it to the end. The `aria-live` line can therefore continue announcing another prompt's
older state rather than the latest accepted/queued/started/interrupted/rejected event. The fixture
uses one receipt key only, and the source-string component test merely checks that the identifier
`latestReceipt` exists.

## Focused checks run

The following required focused commands all exited 0:

- `.venv/bin/python tests/support/acp_fixture_writer.py`
- `node web/tests/acp-contracts.test.mjs`
- `node web/tests/acp-browser-state.test.mjs`
- `node web/tests/acp-browser-conformance.test.mjs`
- `node web/tests/acp-browser-components.test.mjs`
- `npm --prefix web run check` (`svelte-check found 0 errors and 0 warnings`)

Additional read-only runtime probes reproduced findings 1, 2, 3, 6, and 7. An exhaustive small-input
reconstruction/minimality check of the production line-diff algorithm passed.

No finding was found in thought isolation, stable metadata handling, plan replacement, tool-call
reconciliation, typed terminal-state projection, recursive snapshot immutability, replacement-epoch
ready recovery, exact `supportsSteer` prop flow, donor provenance, or the real line-diff algorithm.
The current donor SHA-256 and all four original upstream hashes match the pinned commit. The pane is
unmounted, no route/backend/legacy API path was added, and the nine component styles use existing
Panels tokens without acp-ui/React/card-grid styling.

`./verify` was not run.
