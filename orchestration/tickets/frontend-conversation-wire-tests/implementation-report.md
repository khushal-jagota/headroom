# Conversation wire test decomposition implementation report

## Intent

Replace the 2,101-line source-reading `conversation-wire.test.mjs` harness with
small Vitest suites whose file boundaries describe the Conversation behavior
they protect. Keep production behavior and source unchanged.

## Implementation

- Added the twelve contract-named TypeScript suites:
  - pending image intake and ownership;
  - feed record, liveness, and stream connection;
  - transcript projection;
  - composer asks and delivery;
  - outgoing reconciliation and tab storage;
  - thread items and plan ownership;
  - turn timing;
  - tool presentation;
  - wire values.
- Added `web/tests/support/conversationEvents.ts`, a 144-line shared fixture
  containing only seven typed event builders used across feed, transcript,
  thread, plan, and timing suites.
- Removed `web/tests/conversation-wire.test.mjs`, including its source reads,
  temporary TypeScript compilation, rewritten imports, generated modules,
  cleanup, and manual success log.
- Removed only the old harness command from `test:legacy`. The other nine
  legacy commands retain their original order; `test` and `test:vitest` are
  unchanged.

All suites import real Conversation production modules. Fakes stop at the
existing stream-port, object-URL, session-storage, clock, and randomness seams.
No production module is mocked.

## Important implementation choices

- Pending-image policy is proved with concrete byte sizes and table-driven PNG,
  JPEG, GIF, and WebP admission. No exported size constant supplies the
  expected boundary.
- Reconnect tests use a deferred event read and a second deferred resolved when
  the replacement tail opens. This makes close/read/open ordering awaitable
  without a scheduling delay.
- Outgoing reload tests retain one `MemoryStorage`, reset modules, rebind the
  mutable module variable to the fresh import, and use only the fresh module
  thereafter. They do not call the production test reset hook.
- Timer and tool-summary policies are asserted through concrete visible values.
  Running-work coverage imports the public renderer policy, asserts it remains
  one visible entry, and applies that value to the visible slice. This is the
  reviewed exception because no in-scope pure renderer seam exists.
- TypeScript tests narrow transcript and thread discriminants before reading
  variant fields. The only deliberate cast is at the unknown external live
  frame boundary.

## RED/GREEN evidence

Before the replacements existed, focused Vitest paths returned the expected
`No test files found` RED. Each owner then added only its assigned semantic
suite and ran focused Vitest with typechecking. The old Node harness remained
green until all twelve replacements passed together.

Before deletion, the twelve replacement suites passed together:

```text
Test Files  24 passed (24)
Tests       214 passed (214)
Type Errors no errors
```

Only then were the old file and its package command removed. The same
twelve-suite gate passed again on the settled tree with the same result.

## Final focused gates

- Twelve named suites together: **PASS** — 24 runtime/typecheck tasks,
  214 tests, no type errors.
- `npm --prefix web run check`: **PASS** — 0 errors, 0 warnings.
- `npm --prefix web run build`: **PASS** — 547 modules transformed and
  production bundle emitted.
- `npm --prefix web test`: **PASS** — Vitest ran once with 36 tasks and
  354 tests, no type errors; the nine remaining legacy commands then ran once
  in their original order.
- `git diff --check`: **PASS**.
- Scope/harness audit: **PASS** — old harness absent; no source-reading,
  transpilation, generated-module, manual-log, production-mock, reload-hook, or
  disallowed policy-constant usage remains in the replacements. The one
  renderer-policy import is the reviewed public-contract exception.
- Size audit: **PASS** — the largest suite is 301 lines; every suite is below
  350 lines. The shared fixture is 144 lines.

The repository-wide `./verify` was not run because the program reserves one
canonical run for its final assembled tree.

## Plan deviations

- The feed worker completed pending images, feed, and the shared fixture but
  was stopped when the transcript file had not appeared promptly. The
  ticket implementation sub-agent acting as orchestrator for this delegated
  implementation work—not the root agent—implemented the already-planned
  transcript suite directly.
  File scope, public seams, behavior ownership, and gates did not change.
- `conversation-wire-values.test.ts` is 52 lines, below the plan's rough
  90-line target. Its complete behavior domain is small; padding it would add
  no contract coverage. The contract sets a maximum, not a minimum.

No production defect or scope expansion was encountered. No commit was
created.
