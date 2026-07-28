# Reviewed implementation plan: frontend test-runner foundation

This is the canonical plan after independent review. It supersedes
`implementation-plan.md`.

## Boundary

This Ticket changes only the test runner and the six contract-named suites. It
does not change production TypeScript, Svelte components, Vite's production
build, CSS, backend code, or any other test.

The implementation must touch exactly these repository paths:

- modify `web/package.json`
- modify `web/package-lock.json`
- add `web/vitest.config.ts`
- add `web/tests/tsconfig.json`
- replace `web/tests/query-catalogue.test.mjs` with
  `web/tests/query-catalogue.test.ts`
- replace `web/tests/mutate.test.mjs` with `web/tests/mutate.test.ts`
- replace `web/tests/change-stream.test.mjs` with
  `web/tests/change-stream.test.ts`
- replace `web/tests/reply-watermark.test.mjs` with
  `web/tests/reply-watermark.test.ts`
- replace `web/tests/file-preview.test.mjs` with
  `web/tests/file-preview.test.ts`
- replace `web/tests/lifecycle.test.mjs` with
  `web/tests/lifecycle.test.ts`
- add or update this Ticket's orchestration artifacts

Do not add shared test support pre-emptively. The storage, EventSource, timer,
window, and DOM parser fakes are small and specific to one suite each; keep them
in the suite that owns them. If implementation proves that a helper is genuinely
shared by at least two of these six suites, it may live under
`web/tests/support/`, but it must not be imported by a legacy suite.

## Runner and package setup

1. Add exact dev dependency `"vitest": "4.1.10"` using:

   ```sh
   npm install --prefix web --save-dev --save-exact vitest@4.1.10
   ```

   This version supports the repository's Vite 6 dependency and the worktree's
   Node 22 runtime. The install command must be the only way the lockfile is
   changed.

2. Add `web/vitest.config.ts`, importing `defineConfig` from
   `vitest/config`. Configure:

   - `test.environment: "node"`
   - `test.include: ["tests/**/*.test.ts"]`
   - `test.clearMocks: true`
   - `test.restoreMocks: true`
   - `test.unstubGlobals: true`
   - `test.typecheck.enabled: true`
   - `test.typecheck.include: ["tests/**/*.test.ts"]`
   - `test.typecheck.tsconfig: "./tests/tsconfig.json"`

   Do not add jsdom, happy-dom, browser mode, a Svelte test plugin, aliases, or
   an alternative resolver. Normal Vitest/Vite resolution must import
   `web/src/**/*.ts` directly. Keep `web/vite.config.ts` unchanged.

3. Add `web/tests/tsconfig.json`, extending `../tsconfig.json` and replacing
   its `include` with the six `./**/*.test.ts` suites. Keep strict checking and
   `noEmit`; do not introduce global Vitest APIs because every suite imports the
   APIs it uses. This config exists because the production `web/tsconfig.json`
   intentionally includes only `src/`, while a normal Vite transform does not
   type-check tests.

4. Give `web/package.json` two explicit runner scripts:

   ```json
   "test:vitest": "vitest run --typecheck",
   "test:legacy": "node tests/managed-markdown.test.mjs && node tests/markdown-renderer.test.mjs && node tests/browser-css.test.mjs && node tests/vps-status.test.mjs && node tests/backlog-ideas.test.mjs && node tests/production-surfaces.test.mjs && node tests/worker-configuration-setup.test.mjs && node tests/conversation-wire.test.mjs && node tests/conversation-rest-line.test.mjs && node tests/conversation-pane.test.mjs",
   "test": "npm run test:vitest && npm run test:legacy"
   ```

   The legacy list is the current `test` list with only the six migrated
   commands removed. Vitest runs once; each remaining legacy suite runs once.
   Keep their existing relative order.

## Suite migrations

Every new suite imports `describe`, `it`, `expect`, `beforeEach`, `afterEach`,
and `vi` from `vitest` only as needed. No suite reads production source, rewrites
an import string, calls `typescript.transpileModule`, writes a temporary module,
or dynamically imports a generated file.

### `query-catalogue.test.ts`

Ordinary production imports:

- `queries` from `../src/lib/queryCatalogue`
- `fetchJson` from `../src/lib/api`

Mock the real HTTP seam with `vi.mock("../src/lib/api", ...)`; use the real
TanStack `queryOptions` implementation. The mock records path and options and
returns a harmless typed value. Reset it before each test.

Preserve:

- each named query's public key and HTTP path
- propagation of the query function's `AbortSignal`
- whole, unencoded ids in keys and URL-encoded ids in paths for Ticket and Worker
- equal keys for the same resource id and unequal keys for different ids
- the shared-resource key values used by more than one caller

Use a typed table for the fixed queries and focused tests for parameterized
queries. Delete the `Object.keys(queries)` insertion-order assertion and the
catalogue-length assertion: they test object spelling/order, not behavior.

### `mutate.test.ts`

Ordinary production import:

- `mutateJson` from `../src/lib/mutate`

Use `vi.hoisted` fakes and mock only:

- `fetchJson` from `../src/lib/api` as the HTTP transport seam
- `queryClient.invalidateQueries` from `../src/lib/queryClient` as the cache
  invalidation seam

Preserve separate tests for:

- successful write returns the transport result and invalidates once afterwards
- omitted options are forwarded as the production default
- rejected writes rethrow the same error and never invalidate
- the returned promise does not settle until invalidation settles

Use a local deferred promise for the last case. Reset call history and configured
results before each test.

### `change-stream.test.ts`

Ordinary production imports, loaded after browser fakes are installed:

- `startChangeStream`, `stopChangeStream`, and `connectionStatus` from
  `../src/lib/changeStream`

Mock only `queryClient.invalidateQueries`. Use the real Svelte `writable` store
and the real `ensureDebug` implementation.

The suite owns:

- a minimal fake `EventSource` recording its URL, handlers, and whether it closed
- Vitest fake timers (`vi.useFakeTimers`)
- a Node `window` fake exposing the timer functions and `__plannerDebug`
- a subscription to the real `connectionStatus` store

Because the production module holds one stream and timer at module scope, use
`vi.resetModules()` before each test, install the globals first, then dynamically
import the real source module through its ordinary path. Dynamic import of the
production module is acceptable; generation/import of a temporary module is
not. Always call `stopChangeStream`, unsubscribe, restore real timers, and unstub
globals in cleanup.

Preserve:

- initial reconnecting state and `/api/changes`
- a second start reuses the one stream
- open means connected, increments debug counters, and reconciles once
- repeated frames use a trailing 250 ms debounce
- a later frame earns a later invalidation
- error means reconnecting without constructing a second stream
- reopening reconciles again
- stop closes the stream, cancels a pending flush, and resets status
- callbacks from a stopped stream do nothing

### `reply-watermark.test.ts`

Ordinary production imports:

- `readReplyWatermark`, `writeReplyWatermark`, and
  `onReplyWatermarkMoved` from `../src/lib/replyWatermark`

Keep the in-memory `Storage` fake local to the suite and install it with
`vi.stubGlobal`. Clean up every movement listener and restore globals after each
test so the production module's listener set cannot leak between cases.

Preserve:

- absent conversation reads as zero
- storage key is per Conversation
- writes persist and notify movement
- equal/backward/fractional/NaN writes do not move or notify
- different Conversations remain independent
- malformed, negative, fractional, blank, and non-numeric stored values read as
  zero
- refused writes do not throw or notify
- unsubscribed listeners hear no later movement
- storage getter/read failures and missing storage fail safe to zero/no-op

Use an explicit configurable property descriptor only for the case where reading
`globalThis.localStorage` itself throws; restore the prior descriptor in
`finally`.

### `file-preview.test.ts`

Ordinary production imports:

- `markdownExpansionFor`
- `prepareManagedHtmlPreviewDocument`
- `previewHashHref`
- `resolvePreview`
- `targetFromHref`
- `ticketFileTarget`

No module mock is needed. Stub `window.location` only for same-origin absolute
URL behavior. Keep the extension/kind cases table-driven.

Preserve:

- Ticket Markdown resolution, encoded managed-file URL, label, and preview hash
- HTML, image (including SVG), video, audio, download, and external kind
  selection
- external image/video/audio rendering and external Markdown/HTML/link behavior
- absence of the retired `actionLabel`
- Markdown recursion depth and visited-URL bounds
- local relative and same-origin absolute managed-file target resolution
- cross-origin fallback to external links
- rejection/fallback for traversal, dot segments, and encoded traversal
- explicit preview hash generation

Also cover the contract-named HTML base preparation without adding a DOM
package. Install a suite-local minimal `DOMParser` fake whose returned document
records `createElement("base")`, the assigned absolute `href`, and insertion
before `head.firstChild`; expose a deterministic `documentElement.outerHTML`.
Assert that `prepareManagedHtmlPreviewDocument` resolves the managed HTML URL
against the fake `window.location.href`, inserts the base first, and preserves
doctype serialization. This mocks the browser DOM parser seam, not a private
production helper.

### `lifecycle.test.ts`

Ordinary production imports:

- lifecycle functions and manifest types from `../src/lib/lifecycle`
- `ticketStatusText` from `../src/lib/ui`

No mocks.

Make the coding and research fixtures satisfy the current
`WorkerTypeManifest`/`WorkerTypesResponse` types: use current backend/model/effort
field names, supply every stage's ownership field, declare each manifest with
`satisfies WorkerTypeManifest`, and declare the response with
`satisfies WorkerTypesResponse`. This is test-fixture repair, not a production
change.

Preserve:

- coding field and Stage order
- gating-field, reverse gated-Stage, advance, and default ownership projections
- Worker type label
- scope options from the beginning and middle of the range
- gating, advance, and passed-field cases
- running, errored, approval, proposal, waiting, paired, completed, done, and
  upcoming visual states
- field-slot proposal projection
- all null/pre-load fallbacks
- status text
- a distinct second Worker type driving order, gating, advance, scope, passed
  fields, default ownership, and visual state
- lookup of both Worker types and null for unknown/unloaded types

Remove comments and assertions whose only purpose is byte identity with retired
hardcoded maps. Keep expected arrays/maps when they express current public
behavior. Remove the referential-identity assertion for memoization; callers are
entitled to equal interface values, not a particular cache implementation.

## RED/GREEN sequence

1. Baseline before edits:

   ```sh
   npm --prefix web test
   npm --prefix web run check
   npm --prefix web run build
   ```

   Record any pre-existing failure before continuing.

2. Install Vitest and add the config plus `test:vitest`. Point its include at
   TypeScript tests before creating them and run `npm --prefix web run
   test:vitest`; the expected RED is “no test files found”. This proves the new
   runner, config, and include are the path under test.

3. Migrate the ordinary-import suites first:

   - `file-preview.test.ts`
   - `lifecycle.test.ts`
   - `reply-watermark.test.ts`

   For each, add the TypeScript replacement, run only that Vitest file, and make
   it GREEN using only the browser fake named above. Do not remove the legacy
   `.mjs` yet.

4. Migrate the seam-mocked suites:

   - `query-catalogue.test.ts`
   - `mutate.test.ts`
   - `change-stream.test.ts`

   First write the ordinary import and behavioral assertion, run it to observe
   the expected RED at the real HTTP/cache/EventSource seam, then add only the
   named mock/fake and make that file GREEN. A failure caused by production
   behavior rather than harness setup is recorded as a follow-up; no `web/src`
   edit is allowed.

5. Run all six together. Once GREEN, delete their six `.mjs` predecessors,
   add `test:legacy`, and make `test` compose Vitest once followed by the ten
   remaining legacy commands once.

6. Run the focused suite again, then the complete frontend test command. Inspect
   the final diff for forbidden production changes and for any remaining
   source-reading/transpilation harness in the six replacements.

## Gates

Run once on the settled Ticket tree:

```sh
npm --prefix web run test:vitest -- \
  tests/query-catalogue.test.ts \
  tests/mutate.test.ts \
  tests/change-stream.test.ts \
  tests/reply-watermark.test.ts \
  tests/file-preview.test.ts \
  tests/lifecycle.test.ts
npm --prefix web run check
npm --prefix web run build
npm --prefix web test
git diff --check
```

Then inspect:

```sh
git diff --name-only
rg -n 'readFile|transpileModule|mkdtemp|writeFile|typescript' \
  web/tests/query-catalogue.test.ts \
  web/tests/mutate.test.ts \
  web/tests/change-stream.test.ts \
  web/tests/reply-watermark.test.ts \
  web/tests/file-preview.test.ts \
  web/tests/lifecycle.test.ts
```

The first inspection must show only the contract-scoped files. The `rg` command
must return no matches. Do not run the repository-wide `./verify`; the program
reserves that canonical run for the final settled tree.

## Review focus

The independent implementation review should check:

- no production source changed
- every migrated suite imports the real production module through Vite
- mocks stop at the contract-named external seams
- the six `.mjs` harnesses are gone
- insertion order, source spelling, filenames, and memo object identity are no
  longer asserted
- Vitest and every remaining legacy suite execute exactly once from `npm test`
- all named gates pass with their full output recorded
