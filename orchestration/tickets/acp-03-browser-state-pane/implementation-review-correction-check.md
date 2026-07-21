# ACP-03 implementation review correction check

## Verdict

**READY.** All eight findings from `implementation-review-round-1.md` are corrected in the current
ACP-03 production paths and have targeted proof that exercises the behavior which was previously
missing. This check did not reopen areas that round 1 confirmed sound.

## Finding-by-finding result

### 1. RESOLVED — strict Panels receipt and sequence invariants

`web/src/lib/acp/panelsTransport.ts:149-160` now enforces the frozen receipt cross-fields:

- `queuePosition` is required only for `queued`;
- `reason` is required for `rejected`;
- `reason` is optional only for `interrupted` and rejected for the remaining states.

`validatePayload` at `web/src/lib/acp/panelsTransport.ts:250-270` now also requires
`activity.sequence`, permission `openedSequence`, and permission `settledSequence` to equal the
outer envelope sequence. `web/tests/acp-browser-state.test.mjs:477-522` calls the bundled production
validator with the formerly admitted receipt and sequence mutations and proves they fail closed,
while the valid receipt variants still pass.

### 2. RESOLVED — higher-generation resets preserve the attached entity

`sameAttachedEntity` at `web/src/lib/acp/conversationController.ts:72-74` compares the established
entity kind and ID. The higher-generation branch applies it before reset admission at
`web/src/lib/acp/conversationController.ts:187-198`, while still allowing the ACP session to change
through a valid reset. The focused controller fixture now includes
`higherGenerationWrongEntityReset`, and `web/tests/acp-browser-state.test.mjs:389-397` proves that it
sets the recoverable invalid-data error and closes the socket.

### 3. RESOLVED — non-optimistic human echo is a real missing-ID turn boundary

`replacePromptMessage` at `web/src/lib/acp/conversationState.ts:389-423` advances the turn and sets
the fallback role to user while clearing the active missing-ID group/current agent message when no
matching optimistic human exists. A matching optimistic echo retains the boundary already created
by the local prompt and does not advance it twice.

`web/tests/acp-browser-state.test.mjs:426-441` drives agent text → remote `human_echo` → agent
text through fake socket, production transport, controller, and reducer, and asserts three ordered
messages with texts `before`, `human echo`, and `after`.

### 4. RESOLVED — ACP-00 browser conformance uses the complete production subject

`web/tests/acp-browser-conformance.test.mjs:31-85` bundles `panelsTransport`,
`conversationController`, and `conversationState`, opens a fake socket through the real transport,
and feeds every live/replay envelope through controller admission before deriving evidence from the
public snapshot.

The grouping proof at `web/tests/acp-browser-conformance.test.mjs:126-173` creates missing-ID groups
separated by both tool and plan boundaries. It records three IDs and asserts that the evidence is
non-vacuous; `web/tests/acp-browser-conformance.test.mjs:220-227` additionally requires all three IDs
to be distinct before calling the existing ACP-00 probe and mutation machinery. The targeted
conformance command passes.

### 5. RESOLVED — all nine Svelte contracts have mounted runtime and alert proof

`web/tests/acp-browser-components.test.mjs:50-340` now builds a real Svelte entry, mounts
`AcpConversationPane`, serves it locally, and invokes the Chromium probe in
`tests/support/acp_component_runtime.py`. The probe observes the pane, transcript, thought, tool,
line diff, plan, permission prompt, composer, and persistent status; it operates disclosures,
permission options, delivery choices, queue/lifecycle controls, raw/terminal content, compaction,
and the recency-selected receipt.

`ConversationStatus.svelte:29-73` tracks newly raised recoverable/protocol failures and exposes a
screen-reader-only `role="alert"` while retaining one visible polite `role="status"` line. The
mounted test proves no alert initially, one alert with the exact newly raised message, and exactly
one status line for both connection and protocol failures
(`tests/support/acp_component_runtime.py:28-38,132-147`).

### 6. RESOLVED — permission send failure returns to a retryable state

`respondToPermission` at `web/src/lib/acp/conversationController.ts:314-331` dispatches
`permission_response_failed` when transport send returns `{ ok: false }`. The reducer at
`web/src/lib/acp/conversationState.ts:652-661` clears only the matching submitting option. The
focused fake-transport case at `web/tests/acp-browser-state.test.mjs:414-424` proves the failed send
returns `submittingOptionId` to `null`, so the request is not left permanently disabled.

### 7. RESOLVED — disposal is subscriber-silent

`dispose` now sets the permanent controller guard and clears subscribers before timer/socket cleanup
at `web/src/lib/acp/conversationController.ts:342-354`. It reduces the final internal disposed state
without publishing it. `web/tests/acp-browser-state.test.mjs:465-475` records the subscription count,
calls `dispose`, proves the count does not change, and still verifies timer cleanup and late-socket
silence.

### 8. RESOLVED — latest receipt follows event recency, not record insertion order

The reducer records `latestReceiptClientMessageId` and the admitted envelope sequence on every
server receipt at `web/src/lib/acp/conversationState.ts:445-465`; the immutable public projection
includes both values. `AcpConversationPane.svelte:143-149` passes the explicit key to the exact
`AcpComposer` prop, and `AcpComposer.svelte:41-43` resolves that receipt rather than relying on
`Object.values` insertion order.

`web/tests/acp-browser-state.test.mjs:443-463` inserts two receipt keys and then updates the older key
at the newest sequence, proving that the older key/sequence 4 is selected. The mounted component
probe separately confirms that this explicit older key is rendered instead of the later-inserted
queued record (`tests/support/acp_component_runtime.py:121-123`).

## Targeted verification run

All correction-focused commands exited 0:

- `.venv/bin/python tests/support/acp_fixture_writer.py`
- `node web/tests/acp-browser-state.test.mjs`
- `node web/tests/acp-browser-conformance.test.mjs`
- `node web/tests/acp-browser-components.test.mjs`
- `.venv/bin/ruff check tests/support/acp_component_runtime.py`
- `npm --prefix web run check` (`svelte-check found 0 errors and 0 warnings`)

`./verify` was not run.
