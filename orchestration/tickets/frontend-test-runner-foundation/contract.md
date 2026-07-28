# Frontend test-runner foundation

## Program context

This is the first implementation slice of the frontend simplification program.
The program uses one repeated loop: simplify at a real seam, split through that
seam, simplify the isolated implementation, then simplify the assembled system.

The program reserves one canonical `./verify` run for its final settled tree.
This Ticket must pass every focused gate named below before integration.

## Accepted outcome

Pure TypeScript frontend tests import the production modules they exercise through
the normal Vite/TypeScript resolver. They no longer read production source, rewrite
imports, invoke `typescript.transpileModule`, or write temporary JavaScript modules.

Vitest is the single runner for the six suites in this Ticket. `npm test` still runs
those suites and every frontend suite that remains on the existing Node/browser
harnesses. Production behavior and production source do not change.

## Contract-scoped files

- `web/package.json`
- `web/package-lock.json`
- one Vitest configuration file under `web/` if normal Vite configuration is
  insufficient
- test support owned only by the six migrated suites
- replacements for:
  - `web/tests/query-catalogue.test.mjs`
  - `web/tests/mutate.test.mjs`
  - `web/tests/change-stream.test.mjs`
  - `web/tests/reply-watermark.test.mjs`
  - `web/tests/file-preview.test.mjs`
  - `web/tests/lifecycle.test.mjs`
- this Ticket's orchestration artifacts

No file under `web/src/`, `assets/`, `src/`, or another test suite belongs to this
Ticket. If ordinary imports expose a production defect, record it as a follow-up
instead of widening this Ticket.

## Required behavior

1. Vitest runs TypeScript directly through the repository's frontend module
   resolution. The migrated suites contain no source-text import replacement,
   temporary transpilation directory, or generated `.mjs` module.
2. Tests mock only real external seams:
   - HTTP transport and query invalidation where those are dependencies;
   - `EventSource`, timers, browser storage/events, and the clock where browser
     behavior requires them.
   Tests do not mock private implementation functions.
3. Preserve the existing behavioral coverage:
   - query identity, paths, URL encoding, cancellation, and shared-resource keys;
   - successful mutation invalidation and no invalidation after a failed write;
   - one change stream, burst debouncing, reconnect catch-up, connection state,
     stale-callback rejection, and cleanup;
   - per-Conversation reply watermark persistence and movement notification;
   - managed-file target resolution, kind selection, preview addresses, recursion
     bounds, and HTML base preparation;
   - manifest-driven lifecycle order, gating, ownership, scope, visual state, a
     second Worker type, and unknown Worker types.
4. Remove assertions whose only contract is exact export insertion order, exact
   source spelling, or module filename. Keep assertions on public behavior and
   interface values.
5. The normal `npm test` command runs Vitest once and then every remaining legacy
   suite exactly once. A failure from either runner fails the command.
6. Do not introduce jsdom, a Svelte testing library, or a second browser harness.
   These six suites need only Vitest's Node environment plus explicit browser
   fakes.

## Named focused gates

- the six migrated Vitest suites
- `npm --prefix web run check`
- `npm --prefix web run build`
- the complete `npm --prefix web test`
- `git diff --check`

## Completion boundary

The Ticket is complete when its reviewed implementation passes every named gate
and is committed on `codex/frontend-test-runner-foundation`. It may then be
integrated serially into `staging`. It does not migrate Conversation tests,
component/browser harnesses, remove dependencies, or change production topology.
