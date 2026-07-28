# Frontend test-runner foundation implementation report

## Intent

The six pure TypeScript frontend suites now import their production modules
normally and run through Vitest. The source-reading, import-rewriting,
`typescript.transpileModule`, and temporary `.mjs` harnesses are gone. Production
source and the ten remaining legacy suites are unchanged.

## Implementation

- Added exact dev dependency `vitest@4.1.10`; `web/package-lock.json` was changed
  only by the reviewed `npm install --prefix web --save-dev --save-exact
  vitest@4.1.10` command.
- Added standalone `web/vitest.config.ts` with Node environment, TypeScript-test
  discovery, mock/global cleanup, and Vitest type checking.
- Added strict `web/tests/tsconfig.json`, including the production ambient
  `vite-env.d.ts` declarations required by the real `debug.ts` import.
- Replaced exactly:
  - `query-catalogue.test.mjs` with `query-catalogue.test.ts`
  - `mutate.test.mjs` with `mutate.test.ts`
  - `change-stream.test.mjs` with `change-stream.test.ts`
  - `reply-watermark.test.mjs` with `reply-watermark.test.ts`
  - `file-preview.test.mjs` with `file-preview.test.ts`
  - `lifecycle.test.mjs` with `lifecycle.test.ts`
- Split the package runner into:
  - one `vitest run --typecheck`;
  - one unchanged-order chain containing each of the ten remaining legacy
    suites exactly once;
  - `npm test`, which composes those two commands with `&&`.

The suite seams are:

- query catalogue: real TanStack `queryOptions`, mocked HTTP transport only;
- mutation: mocked HTTP transport and query invalidation only;
- change stream: mocked query invalidation plus suite-owned EventSource,
  window, and timer fakes; the real Svelte store and debug code run;
- reply watermark: suite-owned browser Storage fake;
- file preview: pure public functions plus suite-owned window and DOMParser
  fakes for same-origin and HTML-base behavior;
- lifecycle: pure public functions with strictly typed coding and second-Worker
  manifests.

## Reviewed-plan deviation

The standalone runner initially externalized `@tanstack/svelte-query`. Node then
loaded its root export directly and failed on the package's transitive
`HydrationBoundary.svelte` export before the query-catalogue suite could collect:

```text
TypeError: Unknown file extension ".svelte"
.../@tanstack/svelte-query/dist/HydrationBoundary.svelte
```

Inlining only `@tanstack/svelte-query` moved that package through Vite but then
proved a Svelte transform was required. With owner approval, the standalone
Vitest config now uses the repository's already-installed `svelte()` Vite plugin
and inlines only that one dependency through `test.server.deps.inline`.
It does not merge or modify the production Vite config, add an alias, install a
testing library, or mock TanStack. This is the narrow configuration needed to
exercise the real `queryOptions` interface.

## RED/GREEN evidence

Pre-edit baseline:

- `npm --prefix web test` — GREEN, all sixteen former legacy commands passed.
- `npm --prefix web run check` — GREEN, 0 errors and 0 warnings.
- `npm --prefix web run build` — GREEN, 547 modules transformed.

Runner RED:

- After adding Vitest, config, and the TypeScript include but before adding the
  replacements, `npm --prefix web run test:vitest` exited 1 with `No test files
  found`.

Seam RED:

- The first ordinary mutation import failed at its real query-client/TanStack
  dependency before the reviewed HTTP/query-invalidation mocks were installed.
- The first ordinary change-stream assertion failed with `ReferenceError:
  EventSource is not defined` before its explicit browser fake was installed.
- Query catalogue exposed the transitive `.svelte` resolution issue described
  above; it became GREEN through runner configuration, not a production edit or
  TanStack mock.

Focused GREEN:

```text
npm --prefix web run test:vitest -- \
  tests/query-catalogue.test.ts \
  tests/mutate.test.ts \
  tests/change-stream.test.ts \
  tests/reply-watermark.test.ts \
  tests/file-preview.test.ts \
  tests/lifecycle.test.ts

Test Files  12 passed (12)
Tests       140 passed (140)
Type Errors no errors
```

Vitest reports twelve test tasks because the six runtime suites and the same six
typecheck suites are both enabled.

Settled-tree gates:

- `npm --prefix web run check` — GREEN, 0 errors and 0 warnings.
- `npm --prefix web run build` — GREEN, 547 modules transformed.
- `npm --prefix web test` — GREEN:
  - one Vitest invocation;
  - 140 assertions and no type errors;
  - the ten remaining legacy commands invoked once each in their prior relative
    order.

The six replacements contain no `readFile`, `transpileModule`, `mkdtemp`,
`writeFile`, or `typescript` reference. No repository-wide `./verify` was run;
the program reserves it for the final assembled tree.

An independent implementation review reported PASS on both standards and
contract conformance, with no unresolved findings. It confirmed coverage,
approved seam mocks, browser-fake cleanup, exact runner composition, and the
absence of production or unrelated-test changes.

No commit was created.
