# Implementation report — ACP browser replay presentation transaction

## RED evidence

Before changing production code, the focused controller regression failed with the existing
controller because `snapshot()` exposed the first replay message before `ready`:

```text
$ cd web && node tests/acp-browser-state.test.mjs
AssertionError [ERR_ASSERTION]: an incomplete replay candidate must stay outside snapshot()

1 !== 0
    at runControllerAssertions (.../web/tests/acp-browser-state.test.mjs:601:12)
exit 1
```

The mounted production component with the real controller also failed before the fix. It observed 28
separate pre-ready transcript-height increases while every sampled frame remained bottom-anchored:

```text
$ cd web && node tests/acp-browser-components.test.mjs
AssertionError: RED: replay painted before ready:
[(147, 196, 0), (196, 260, 0), ... (1796, 1860, 0), (1860, 1924, 0)]
exit 1
```

This is direct evidence that the visible travel is repeated replay presentation, not a failure to
reach the final bottom.

## Implementation

- `conversationController.ts` now keeps `committedState` as the only subscriber-visible and
  `snapshot()` state. `replayCandidate` is the private protocol head from reset through ready.
- Admission and ordered reduction continue against `replayCandidate ?? committedState`, including
  exact-next replacement resets and preserved protocol rejections.
- Ready reduces and clears recovery state inside the candidate, commits it, publishes once, and
  only then releases a deferred first prompt. Ordinary live envelopes continue to publish one by one.
- Invalid data, gaps, close, error, replay-unavailable close, and disposal discard the candidate.
  Reconnect attaches from the committed cursor.
- Existing action regressions now exercise queue cancellation and permission response after an
  explicitly committed ready boundary instead of reaching into an incomplete replay.
- The mounted regression uses the real controller and pane, production styles, one envelope per
  animation frame, a genuine overflowing transcript, and post-tick bottom measurements. No pane
  markup or scroll code changed.

## Focused GREEN evidence

```text
$ cd web && node tests/conversation-controller.test.mjs \
    && node tests/acp-browser-state.test.mjs \
    && node tests/acp-browser-components.test.mjs \
    && npm run check \
    && npm run build
conversation-controller.test.mjs: all assertions passed
acp-browser-state.test.mjs: all assertions passed
acp-browser-components.test.mjs: all assertions passed
svelte-check found 0 errors and 0 warnings
vite v6.4.3 building for production...
✓ 469 modules transformed.
✓ built in 1.09s
exit 0

$ git diff --check
exit 0
```

The controller tests cover hidden first replay, one ready publication, old-transcript retention and
one replacement swap, exact-next reset rejection preservation, gap/invalid/close/error/replay-
unavailable discard, committed-cursor reconnect, no publication on disposal, commit-before-deferred-
prompt send, and incremental post-ready updates. The mounted test proves zero pre-ready height growth,
one overflowing ready paint at the bottom, and one later live height/bottom update.

## Independent review resolution

The first review found that the raw malformed transport-data case occurred after `ready`, so it did
not prove candidate discard. The focused regression now starts from a committed sequence-15
conversation, admits reset sequence 16 and transcript-bearing human echo sequence 17, then feeds raw
`not json` before ready. It proves that the sequence-17 message and cursor never become visible, the
committed transcript/cursor remain intact, the exact `Conversation data could not be read.
Reconnecting…` error is published, the socket closes, exactly one reconnect timer is scheduled, and
the next attach carries only committed generation 1 / sequence 15.

```text
$ cd web && node tests/acp-browser-state.test.mjs && cd .. && git diff --check
acp-browser-state.test.mjs: all assertions passed
exit 0
```

`./verify` was deliberately not run; the parent program reserves that single canonical gate for the
settled tree after independent review.

The successful production build regenerated the FastAPI-served bundle and it is intentionally left
for root integration: `web/dist/index.html` now references
`web/dist/assets/index-DCKqHcAm.js`; the superseded tracked
`web/dist/assets/index-kTLmyVHa.js` is removed. No bundle file was hand-edited.

## Canonical-run integration repair

The parent program's first canonical `./verify` passed its other gates, including 1,358 unit tests
and all 117 Playwright tests, but failed at `web/tests/acp-browser-conformance.test.mjs:134`. That
fixture inspected the `stable-id` message after reset and before ready, which contradicted the new
hidden-replay contract.

The conformance fixture now commits reset with ready before feeding and inspecting its grouping
updates. Auditing the rest of the file found the plan-reconciliation evidence also relied on
intermediate replay publications. Its live side now explicitly commits ready first and then feeds
the typed updates incrementally, preserving the required multiple plan snapshots; the replay side
remains the atomic reset-to-ready stream. Protocol-rejection evidence is inspected only after its
candidate is committed with ready.

```text
$ cd web && node tests/acp-browser-conformance.test.mjs
acp-browser-conformance.test.mjs: all assertions passed
exit 0

$ cd web && npm test
resource-catalogue.test.mjs: all assertions passed
resource-cache.test.mjs: all assertions passed
ws-connection.test.mjs: all assertions passed
lifecycle.test.mjs: all assertions passed
acp-images.test.mjs: all assertions passed
acp-contracts.test.mjs: all assertions passed
acp-browser-state.test.mjs: all assertions passed
acp-browser-conformance.test.mjs: all assertions passed
acp-browser-components.test.mjs: all assertions passed
acp-components.test.mjs: all assertions passed
employee-configuration-setup.test.mjs: all assertions passed
acp-production-mount.test.mjs: all assertions passed
exit 0
```

## Final canonical verification

After the conformance migration and independent re-review, the settled tree's canonical gate passed
in full:

```text
$ ./verify
All checks passed!
Success: no issues found in 154 source files
1358 passed, 9 warnings
svelte-check found 0 errors and 0 warnings
✓ 469 modules transformed.
acp-browser-state.test.mjs: all assertions passed
acp-browser-conformance.test.mjs: all assertions passed
acp-browser-components.test.mjs: all assertions passed
117 passed, 1 warning
[verify] gate ruff: ok
[verify] gate mypy: ok
[verify] gate unit suite: ok
[verify] gate build check: ok
[verify] gate frontend: ok
[verify] gate e2e suite: ok
VERIFY: PASS
```
