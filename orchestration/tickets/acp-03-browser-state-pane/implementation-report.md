# ACP-03 implementation report

## Outcome

Implemented the unmounted typed ACP browser subject and Panels-native conversation pane.

- One injected websocket transport validates the closed Panels envelope and shallow SDK-owned values
  required by the reducer.
- One controller owns attach/reconnect, identity/generation/sequence admission, reset, gaps, actions,
  disposal, and replacement-epoch recovery.
- Panels-owned receipt invariants and activity/permission sequence equality fail closed at the
  transport boundary. Generation resets preserve the attached entity.
- One pure state fold delegates message, thought, tool, and plan work to corrected donor helpers.
- Public snapshots are cached recursively frozen projections. Mutable donor maps and arrays are not
  exposed.
- Stable mode, config-option, and session-info updates are typed non-transcript state.
- `supportsSteer` is passed explicitly from connection state into the composer. Queue and Send Now
  remain available independently.
- Gap and invalid-envelope errors clear only after a contiguous ready envelope on a replacement
  socket epoch. Protocol rejection remains persistent.
- Non-optimistic human echoes form a real turn boundary; failed permission sends remain retryable;
  disposal is subscriber-silent; and receipt recency is explicit rather than record-order based.
- The nine contracted Svelte components use the existing chat geometry, MarkdownBlock, FilePreview,
  ChatComposer, and existing tokens only. The pane is not mounted in a route.
- Component acceptance mounts the pane in headless Chromium and exercises the nine component
  contracts, controls, disclosures, rendering, live regions, and newly raised alerts at runtime.

## Changed files

Production browser state:

- `web/src/lib/acp/panelsTransport.ts`
- `web/src/lib/acp/conversationState.ts`
- `web/src/lib/acp/conversationController.ts`
- `web/src/lib/acp/lineDiff.ts`
- `web/src/lib/acp/filePreview.ts`

Svelte pane:

- `web/src/components/acp/AcpConversationPane.svelte`
- `web/src/components/acp/TranscriptView.svelte`
- `web/src/components/acp/ThoughtView.svelte`
- `web/src/components/acp/ToolCallCard.svelte`
- `web/src/components/acp/DiffView.svelte`
- `web/src/components/acp/PlanView.svelte`
- `web/src/components/acp/PermissionPrompt.svelte`
- `web/src/components/acp/AcpComposer.svelte`
- `web/src/components/acp/ConversationStatus.svelte`

Donor correction and provenance:

- `web/src/vendor/acp-components-core/src/store/sessionStore.ts`
- `web/src/vendor/acp-components-core/UPSTREAM.md`

Fixture and conformance support:

- `tests/support/acp_fixture_writer.py`
- `tests/support/acp_browser_conformance.py`
- `tests/support/acp_component_runtime.py`
- `tests/fixtures/acp/browser-live-replay-v1.json`
- `tests/fixtures/acp/browser-envelope-states-v1.json`
- `tests/fixtures/acp/browser-controller-cases-v1.json`

Focused tests:

- `web/tests/acp-contracts.test.mjs`
- `web/tests/acp-browser-state.test.mjs`
- `web/tests/acp-browser-conformance.test.mjs`
- `web/tests/acp-browser-components.test.mjs`

Ticket evidence:

- `orchestration/tickets/acp-03-browser-state-pane/implementation-report.md`

ACP-03 did not edit `web/package.json`, either lockfile, a route, shared CSS/tokens, the legacy chat
pane, Python production code, or the Hermes checkout.

## Design deviations

None. The visual implementation follows the reviewed restrained operational-pane thesis. There are
no acp-ui/React styles, decorative cards, gradients, shadows, new tokens, route mounting, or backend
selection in the pane.

## Implementation review round 1 disposition

All eight findings in `implementation-review-round-1.md` were corrected:

1. Delivery receipt state/reason/queue-position invariants and outer/payload activity and permission
   sequence equality are enforced and negatively tested at the real transport boundary.
2. A higher-generation reset for a different entity fails closed and reconnects.
3. A non-optimistic human echo advances the turn boundary, preserving agent/user/agent order for
   missing-ID chunks.
4. ACP-00 browser conformance evidence now traverses fake socket → production transport → production
   controller → reducer; boundary grouping proves three distinct groups.
5. The component suite now mounts the pane in Chromium, renders all nine components, operates their
   controls, and asserts role/live-region behavior. New connection and protocol failures use a
   screen-reader alert while the pane retains exactly one visible polite status line.
6. A failed permission-response send rolls back the local submitting option so the user can retry.
7. Disposal clears subscribers before closing and emits no final snapshot.
8. State explicitly records the client-message key and sequence of the latest receipt event; the
   composer uses that key, including when an older record key receives the newest event.

The full web test also required ACP preview resolution to pass through the small
`web/src/lib/acp/filePreview.ts` adapter. This preserves reuse of the shared FilePreview component
without violating the existing managed-markdown ownership boundary.

## Focused verification

Command:

```text
.venv/bin/python tests/support/acp_fixture_writer.py
```

Full outcome:

```text
exit code 0
(no stdout or stderr)
```

Command:

```text
node web/tests/acp-contracts.test.mjs
```

Full outcome:

```text
acp-contracts.test.mjs: all assertions passed
exit code 0
```

Command:

```text
node web/tests/acp-browser-state.test.mjs
```

Full outcome:

```text
acp-browser-state.test.mjs: all assertions passed
exit code 0
```

Command:

```text
node web/tests/acp-browser-conformance.test.mjs
```

Full outcome:

```text
acp-browser-conformance.test.mjs: all assertions passed
exit code 0
```

Command:

```text
node web/tests/acp-browser-components.test.mjs
```

Full outcome:

```text
acp-browser-components.test.mjs: all assertions passed
exit code 0
```

Command:

```text
.venv/bin/ruff check tests/support/acp_component_runtime.py
```

Full outcome:

```text
All checks passed!
exit code 0
```

Command:

```text
npm --prefix web run check
```

Full outcome:

```text
> check
> svelte-check --tsconfig ./tsconfig.json

Loading svelte-check in workspace: /Users/khushaljagota/.hermes/planning-v2/web
Getting Svelte diagnostics...

svelte-check found 0 errors and 0 warnings
exit code 0
```

Command:

```text
npm --prefix web test
```

Full outcome:

```text
> test
> node tests/resource-catalogue.test.mjs && node tests/resource-cache.test.mjs && node tests/ws-connection.test.mjs && node tests/neutral-pane.test.mjs && node tests/ticket-neutral-pane.test.mjs && node tests/file-preview.test.mjs && node tests/lifecycle.test.mjs && node tests/managed-markdown.test.mjs && node tests/markdown-renderer.test.mjs && node tests/chat-images.test.mjs && node tests/acp-contracts.test.mjs

resource-catalogue.test.mjs: all assertions passed
resource-cache.test.mjs: all assertions passed
[planner] ws open since=0
[planner] flush 1
[planner] ws open since=1
ws-connection.test.mjs: all assertions passed
neutral-pane.test.mjs: all assertions passed
ticket-neutral-pane.test.mjs: all assertions passed
lifecycle.test.mjs: all assertions passed
acp-contracts.test.mjs: all assertions passed
exit code 0
```

Command:

```text
npm --prefix web run build
```

Full outcome:

```text
> build
> vite build

vite v6.4.3 building for production...
<script src="/assets/markdown.js"> in "/index.html" can't be bundled without type="module" attribute

/assets/tokens.css doesn't exist at build time, it will remain unchanged to be resolved at runtime

/assets/app.css doesn't exist at build time, it will remain unchanged to be resolved at runtime
transforming...
✓ 158 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                                                0.75 kB │ gzip:  0.38 kB
dist/assets/newsreader-vietnamese-400-normal-DdKr49mV.woff2    5.43 kB
dist/assets/newsreader-vietnamese-500-normal-CL6a8tp2.woff2    5.44 kB
dist/assets/newsreader-vietnamese-600-normal-CaH84vfx.woff2    5.50 kB
dist/assets/newsreader-vietnamese-400-normal-BekUZro8.woff     6.97 kB
dist/assets/newsreader-vietnamese-500-normal-BEAbKU8A.woff     7.04 kB
dist/assets/newsreader-vietnamese-600-normal-CVAR0otO.woff     7.06 kB
dist/assets/newsreader-latin-ext-400-normal-svq1FPys.woff2    14.21 kB
dist/assets/newsreader-latin-ext-500-normal-BNHmvKvI.woff2    15.07 kB
dist/assets/newsreader-latin-ext-600-normal-BXv5iMHi.woff2    15.17 kB
dist/assets/newsreader-latin-ext-400-normal-DYA1XoQK.woff     18.38 kB
dist/assets/newsreader-latin-ext-500-normal-CZruMFou.woff     19.30 kB
dist/assets/newsreader-latin-ext-600-normal-BrbfzHZ5.woff     19.39 kB
dist/assets/newsreader-latin-400-normal-BFBkh4jY.woff2        22.48 kB
dist/assets/newsreader-latin-500-normal-B66TYsaK.woff2        23.62 kB
dist/assets/newsreader-latin-600-normal-30OJ_TG_.woff2        23.88 kB
dist/assets/newsreader-latin-400-normal-gRTjlS2D.woff         28.29 kB
dist/assets/newsreader-latin-500-normal-DFwuUcdu.woff         29.64 kB
dist/assets/newsreader-latin-600-normal-DUnT2r2g.woff         29.78 kB
dist/assets/index-C68rJfrI.css                                 6.64 kB │ gzip:  1.35 kB
dist/assets/index-B-0RHEqE.js                                189.61 kB │ gzip: 63.92 kB
✓ built in 707ms
exit code 0
```

`./verify` was not run, as required. No commit was created.
