# Implementation review — ACP browser replay presentation transaction

## P2 — The malformed-envelope recovery test never has a replay candidate to discard

The implementation plan requires invalid data to be exercised while a replay candidate is in
progress, and the implementation report claims that coverage. The only raw malformed-envelope case,
however, feeds `"not json"` at `web/tests/acp-browser-state.test.mjs:846` after the reconnect has
already admitted `ready` at sequence 2 and committed its replay. That proves ordinary recovery from
invalid transport data, but it cannot fail if `onInvalidEnvelope` forgets to discard an incomplete
candidate. The semantic-invalid cases at lines 950–958 start a candidate, but they do not put any
transcript state into it or assert that its cursor/transcript remain hidden after failure.

Exact fix: add a focused case that feeds reset, then a transcript-bearing replay envelope, then
`"not json"` before ready. Assert that `snapshot()` still has the prior committed transcript/cursor
(or the empty/null first-attach state), that the exact recoverable error and reconnect timer are
present, and that the reconnect attach uses only the committed cursor. This closes the explicit
plan/acceptance-evidence gap for candidate discard on malformed input.

## Verified

- The load-bearing controller logic correctly reduces replacement resets from
  `replayCandidate ?? committedState`, commits before publishing and releasing a deferred prompt,
  reads reconnect attach cursors only from committed state, and discards the candidate in each
  production recovery/lifecycle path.
- Existing controller action tests remain post-ready and meaningful.
- The mounted browser regression is pre-fix-sensitive: it uses the real controller and pane,
  production styles, one envelope per settled animation frame, asserted overflow, and bottom-distance
  checks.
- Fresh focused gates passed: `conversation-controller.test.mjs`, `acp-browser-state.test.mjs`,
  `acp-browser-components.test.mjs`, and `npm run check` (zero diagnostics).

## Resolution review

The correction resolves the P2. The new malformed-replay case begins from a complete committed
conversation, admits a replacement reset and a transcript-bearing candidate message, then delivers
raw malformed WebSocket data before ready. It proves the committed transcript and sequence-15 cursor
remain visible, the candidate content stays absent, the exact recoverable error is published, the
socket closes with one reconnect scheduled, and the next attach uses only the committed cursor.

A fresh `node tests/acp-browser-state.test.mjs` run passed, and `git diff --check` remains clean.

**NO VIOLATIONS.**

## Post-VERIFY conformance migration review

The conformance repair preserves the probes rather than weakening them:

- `drivePostReadyLive` commits an empty reset/ready transaction, then feeds the original live
  updates one at a time in the same order with contiguous shifted sequences. The two plan updates
  therefore still create two distinct subscriber publications, while the unmodified replay stream
  commits atomically and reaches the same final typed-thought, message-grouping, plan, and tool state.
- The stable explicit-ID and missing-ID boundary grouping checks now run after a committed ready.
  Their original per-update observations and uniqueness/equality assertions remain intact.
- Protocol rejections are still admitted in order and inspected only after ready commits them; both
  discriminators, both visible statuses, and the absence of assistant-text fallback remain required.
- The frozen Python conformance assertions and mutation-failure checks are unchanged.

A fresh `node tests/acp-browser-conformance.test.mjs` run passed, and `git diff --check` remains
clean. The implementation report and `PROGRESS.md` accurately describe the canonical-run failure,
the fixture-boundary correction, and the remaining full verification rerun.

**NO VIOLATIONS.**
