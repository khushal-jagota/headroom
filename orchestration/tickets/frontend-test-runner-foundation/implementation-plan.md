# Frontend test-runner foundation implementation plan

## Intent and boundaries

Replace the six contract-named source-transpilation harnesses with ordinary
TypeScript tests run by Vitest. The tests will import the production modules
through Vite's resolver and fake only HTTP, query invalidation, and browser
facilities at the real seams.

Only these files may change:

- `web/package.json`
- `web/package-lock.json`
- `web/vitest.config.ts`
- replacements for the six named `web/tests/*.test.mjs` files
- test support imported only by those six suites, if a local fake becomes clearer
  than keeping it in its owning suite
- this Ticket's orchestration artifacts

No production module, existing Vite configuration, TypeScript configuration,
remaining test suite, or verification harness changes in this Ticket.

## Runner and package shape

1. Install `vitest` as an exact development dependency:

   ```sh
   npm --prefix web install --save-dev --save-exact vitest@3.2.4
   ```

   Vitest 3.2.4 accepts the repository's installed Vite 6 line and Node 22. It
   does not require `jsdom`; do not install `jsdom`, `happy-dom`, a Svelte test
   library, or a browser runner.

2. Add `web/vitest.config.ts`. Merge the existing `web/vite.config.ts` with the
   Vitest-only settings so tests use the same Vite plugins and module resolver
   rather than establishing a parallel resolver. Configure:

   - `test.environment: "node"`
   - an explicit `include` containing only:
     - `tests/query-catalogue.test.ts`
     - `tests/mutate.test.ts`
     - `tests/change-stream.test.ts`
     - `tests/reply-watermark.test.ts`
     - `tests/file-preview.test.ts`
     - `tests/lifecycle.test.ts`
   - no global Vitest APIs; each suite imports `describe`, `it`, `expect`, `vi`,
     and lifecycle hooks as needed
   - automatic mock restoration/clearing, with each suite still explicitly
     cleaning up installed globals and subscriptions

   The explicit include prevents Vitest from discovering any legacy `.mjs`
   suite.

3. Replace the single long `test` script with three scripts:

   ```json
   {
     "test": "npm run test:vitest && npm run test:legacy",
     "test:vitest": "vitest run --config ./vitest.config.ts",
     "test:legacy": "node tests/managed-markdown.test.mjs && node tests/markdown-renderer.test.mjs && node tests/browser-css.test.mjs && node tests/vps-status.test.mjs && node tests/backlog-ideas.test.mjs && node tests/production-surfaces.test.mjs && node tests/worker-configuration-setup.test.mjs && node tests/conversation-wire.test.mjs && node tests/conversation-rest-line.test.mjs && node tests/conversation-pane.test.mjs"
   }
   ```

   `npm test` therefore invokes Vitest once, stops immediately if it fails, and
   otherwise invokes each of the ten remaining legacy suites exactly once.
   Remove the six migrated `.mjs` paths from the legacy chain; do not otherwise
   reorder, edit, glob, or duplicate that chain.

## Suite migrations

For each migration, delete the old `.test.mjs` only after its `.test.ts`
replacement has passed alongside it. None of the replacements may import
`typescript`, read production source, rewrite an import, make a temporary
directory, write generated JavaScript, or assert a production filename.

### `query-catalogue.test.ts`

- Import `queries` normally from `../src/lib/queryCatalogue`.
- Mock only `../src/lib/api` and use a `vi.fn()` implementation of `fetchJson`
  to record the requested path and options. Use the real TanStack
  `queryOptions`; it is part of the production interface, not a test seam.
- Table-test every public query builder for its query key, request path, and
  forwarding of the `AbortSignal`.
- Retain the raw parameter value in query keys and verify URL encoding for
  awkward Ticket and Worker ids.
- Retain same-resource/same-id equality and different-id inequality, including
  the shared Ticket key prefix for its conversation start values.
- Delete the exact `Object.keys(queries)` insertion-order assertion. Coverage of
  every public entry comes from the behavior table, without treating export
  order as a contract.

### `mutate.test.ts`

- Import `mutateJson` normally from `../src/lib/mutate`.
- Mock `../src/lib/api` at the HTTP transport seam and
  `../src/lib/queryClient` at the query invalidation seam.
- Preserve separate cases for:
  - forwarding a write and returning its result;
  - defaulting omitted options to an empty object;
  - invalidating only after a successful write;
  - returning the original write error without invalidation;
  - keeping the returned promise pending until invalidation settles.
- Use a controlled invalidation promise for the final case rather than timers or
  a private implementation hook.

### `change-stream.test.ts`

- Import `startChangeStream`, `stopChangeStream`, and `connectionStatus`
  normally from `../src/lib/changeStream`; use the real Svelte writable store
  and real `ensureDebug`.
- Mock only `../src/lib/queryClient` for invalidation.
- Install a small `EventSource` fake on `globalThis` that records instances,
  URL, callbacks, and closure. Install a Node `window` fake containing the
  Vitest-controlled `setTimeout`, `clearTimeout`, and `__plannerDebug`.
- Use `vi.useFakeTimers()` for the 250 ms trailing window and subscribe to or
  read the public `connectionStatus` store for connection-state assertions.
- Preserve one ordered behavior scenario (or isolated scenarios with
  `vi.resetModules`) proving:
  - the first start opens `/api/changes` and a second start opens nothing;
  - open changes state to connected and performs reconnect catch-up
    invalidation;
  - a burst restarts the trailing timer and invalidates once;
  - a later frame invalidates separately;
  - error changes state to reconnecting without opening another stream;
  - reopen catches up again;
  - stop closes the source, cancels a pending timer, resets status, and rejects
    late callbacks from the stale source.
- Always call `stopChangeStream`, unsubscribe, restore real timers, and remove
  the browser globals in cleanup.

### `reply-watermark.test.ts`

- Import the three public watermark functions normally from
  `../src/lib/replyWatermark`; mock no production module.
- Keep a suite-local `MemoryStorage` implementing the browser `Storage` methods
  the production module actually calls, and install it as `localStorage`.
- Preserve:
  - absent and invalid values reading as zero;
  - Conversation-specific persisted keys;
  - forward-only whole-number writes;
  - independent Conversation watermarks;
  - movement notification only after a persisted advance;
  - listener unsubscription;
  - blocked reads, rejected writes, and absent storage failing safely.
- Explicitly unregister listeners and restore the `localStorage` property after
  each case so module-level listeners cannot leak between tests.

### `file-preview.test.ts`

- Import the public file-preview functions normally from
  `../src/lib/filePreview`; mock no production module.
- Preserve the current target and kind matrix for managed Markdown, HTML,
  images including SVG, video/audio extension disambiguation, downloads, and
  external links/media.
- Preserve exact managed-file and preview addresses, same-origin managed-link
  recognition, off-origin fallback, unsafe target/path rejection, URL encoding,
  and Markdown recursion depth/visited bounds.
- Install an explicit `window.location` fake only for same-origin and absolute
  URL behavior.
- Add the contract-required `prepareManagedHtmlPreviewDocument` case. Exercise
  the real function with a minimal suite-local `DOMParser`/Document fake that
  implements only the external browser operations the function uses:
  `parseFromString`, `createElement`, `setAttribute`, `head.firstChild`,
  `head.insertBefore`, `doctype`, and `documentElement.outerHTML`. Assert that
  the generated base uses the managed document's absolute URL and is inserted
  before existing head content. Do not mock the production parser helper or add
  a DOM package.
- Restore `window` and `DOMParser` after the suite.

### `lifecycle.test.ts`

- Import lifecycle functions normally from `../src/lib/lifecycle` and
  `ticketStatusText` normally from `../src/lib/ui`; mock nothing.
- Keep typed coding and research manifest fixtures matching the current public
  `WorkerTypeManifest` values (`default_backend`, `default_model`, and
  `default_reasoning_effort`, rather than the stale fixture-only
  `default_employee_backend` spelling).
- Preserve behavior for:
  - field and stage order;
  - stage-to-field and field-to-stage gating;
  - advance targets;
  - default ownership by stage;
  - scope ceiling options and their visible labels;
  - passed/upcoming/current/running/paired/approval/error/done visual states;
  - field proposal projection;
  - null/pre-load defaults;
  - lookup and memoization;
  - the synthetic second Worker type;
  - unknown Worker types.
- Remove fixture-self assertions and commentary whose only purpose was proving
  the former transpilation/import rewrite or the retired constant filenames.
  Keep value comparisons that prove public lifecycle behavior.

## RED/GREEN sequence

1. **Runner RED:** add the Vitest dependency, config, and scripts with the six
   replacement paths included, before adding the replacements. Run
   `npm --prefix web run test:vitest`; it must fail because the six registered
   suites do not yet exist. This proves the new runner is the active gate.
2. Add each `.test.ts` replacement while its old `.test.mjs` remains:
   - for query catalogue, mutation, and change stream, first run the
     direct-import assertions before installing the relevant seam mock and
     record the expected external-seam failure;
   - then install only the named seam mock/fake and make the focused suite
     GREEN;
   - reply watermark, file preview, and lifecycle are pure imports except for
     explicit browser globals, so their parity check is old suite GREEN plus
     focused Vitest GREEN rather than an artificial failing assertion.
3. After every replacement has focused GREEN parity, delete the six old `.mjs`
   files and remove their six Node invocations from the legacy script.
4. Run all six Vitest suites together. Then run the complete `npm test` command
   and confirm the output shows one Vitest invocation followed by the ten
   unchanged legacy suites once each.

Focused per-suite commands:

```sh
npm --prefix web run test:vitest -- tests/query-catalogue.test.ts
npm --prefix web run test:vitest -- tests/mutate.test.ts
npm --prefix web run test:vitest -- tests/change-stream.test.ts
npm --prefix web run test:vitest -- tests/reply-watermark.test.ts
npm --prefix web run test:vitest -- tests/file-preview.test.ts
npm --prefix web run test:vitest -- tests/lifecycle.test.ts
```

## Final focused gates

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

Do not run the repository-wide `./verify`; the program reserves that canonical
run for the final assembled tree.

## Contract ambiguities and decisions

1. The legacy file-preview suite does not currently call
   `prepareManagedHtmlPreviewDocument`, while Required behavior explicitly
   names HTML base preparation. Treat the explicit Required behavior as
   authoritative and add that focused case with a browser-parser fake; no
   production change is implied.
2. The legacy lifecycle fixture carries `default_employee_backend`, but the
   current production manifest interface carries `default_backend`,
   `default_model`, and `default_reasoning_effort`. Use the current production
   interface and remove assertions that only tested the stale fixture's own
   spelling. The lifecycle behavior named by the contract remains unchanged.
3. The contract permits test support owned by these suites but does not require
   a shared support module. Keep one-off browser fakes in their owning suite;
   extract support only if both use the same behavior, without exposing it to
   remaining legacy suites.
