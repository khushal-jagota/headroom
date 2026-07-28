# Frontend test-runner foundation plan review

## Findings

1. **High — the primary plan pins an obsolete runner without a repository reason.**
   `implementation-plan.md:25-33` chooses Vitest 3.2.4. The independently
   confirmed current release, Vitest 4.1.10, supports this worktree's Node 22
   and Vite 6 versions. Carrying an old major into a new foundation creates an
   immediate upgrade task and makes the plan's mock-lifecycle assumptions
   version-dependent.

   **Required amendment:** use the alternate plan's exact
   `vitest@4.1.10` development dependency and generate the lockfile only through
   `npm install --prefix web --save-dev --save-exact vitest@4.1.10`.

2. **Medium — merging the production Vite configuration is unnecessary coupling.**
   `implementation-plan.md:35-50` would import and merge `vite.config.ts`, which
   contributes the Svelte plugin, production base path, build output, server
   ports, and backend proxies. None of the six suites imports a Svelte component
   or uses a project alias; ordinary Vitest resolution already exercises the
   production TypeScript imports required by `contract.md:14-20,44-46`.

   **Required amendment:** use a standalone `web/vitest.config.ts` built with
   `defineConfig` from `vitest/config`. Configure Node environment,
   `clearMocks`, `restoreMocks`, and `unstubGlobals`; do not merge the production
   Vite config or add aliases/plugins.

3. **Medium — the exact six-file include recreates a physical inventory that the
   foundation would have to edit on every later migration.**
   `implementation-plan.md:40-46` freezes the six filenames even though the
   intended distinction is semantic: TypeScript suites use Vitest and remaining
   `.mjs` suites stay on the legacy harness. The exact list is not needed to
   exclude `.mjs`.

   **Required amendment:** use `test.include: ["tests/**/*.test.ts"]`. On this
   Ticket's settled tree that discovers exactly the six replacements, while
   legacy `.mjs` suites remain exclusively in `test:legacy`.

4. **Medium — the primary lifecycle plan preserves private cache identity as a
   test contract.**
   `implementation-plan.md:188` retains “lookup and memoization,” which carries
   forward `lifecycle.test.mjs:399-400`'s referential-identity assertion.
   `contract.md:60-64` requires lifecycle behavior and interface values, not the
   private `WeakMap` strategy in `lifecycle.ts:185-200`.

   **Required amendment:** follow the alternate plan: remove the `===` cache
   identity assertion. Test both Worker-type lookups, repeated lookup values,
   and unknown/unloaded results without constraining object identity.

5. **Medium — the primary file-preview migration can silently lose an existing
   public-shape assertion.**
   `file-preview.test.mjs:101-103` proves that HTML, SVG, and external media
   results do not resurrect the retired `actionLabel`. The primary plan's
   `implementation-plan.md:151-169` does not retain it, despite
   `contract.md:52-64` requiring existing behavioral/interface-value coverage.
   Its new HTML fake also needs `window.location.href`, not merely `origin`,
   because `filePreview.ts:194-196` resolves the base against `href`.

   **Required amendment:** preserve the `actionLabel` absence assertion. For HTML
   base preparation, give the window fake both `origin` and an absolute `href`;
   make the document fake record `createElement("base")`, `href` assignment, and
   insertion before the existing first head child, then assert the serialized
   doctype and resulting order.

6. **Medium — the change-stream isolation choice is internally ambiguous.**
   `implementation-plan.md:109-130` first says to import the module normally,
   then offers isolated cases using `vi.resetModules`. A static top-level import
   survives `resetModules`, so that variant would not isolate the module-level
   `stream` and `flushTimer` in `changeStream.ts:14-15`.

   **Required amendment:** choose the alternate plan's one coherent method:
   before each case reset modules, install the timer/window/EventSource fakes,
   then dynamically import the ordinary production path. Always stop the
   imported instance, unsubscribe, restore timers, and unstub globals.

7. **Medium — “typed” lifecycle fixtures are not enforced by any proposed gate.**
   `implementation-plan.md:175-178` relies on current
   `WorkerTypeManifest` typing, but `web/tsconfig.json:17-18` excludes
   `web/tests`, and a normal Vitest run transforms TypeScript without type
   checking it. The stale manifest spelling this Ticket is intended to remove
   could therefore recur without failing `check` or Vitest.

   **Required amendment:** enable Vitest type checking for the same
   `tests/**/*.test.ts` set (or add an equivalent explicit test-TypeScript
   check within the Ticket's runner configuration) and include that in
   `test:vitest`. Declare both manifests with `satisfies WorkerTypeManifest` and
   the response with `satisfies WorkerTypesResponse`. Give the synthetic second
   Worker type distinct ownership values and assert its
   `stageDefaultOwnershipMode`, so manifest-driven ownership is proved beyond
   the coding fixture.

The package-script split in both plans is otherwise correct: Vitest runs once,
the ten remaining legacy suites retain their order and run once, and `&&`
propagates either runner's failure.

## Verdict

**FAIL**

Unresolved findings: 7. Amend the implementation plan as stated above before
implementation.

## Re-review

The canonical `implementation-plan-alternate.md` and
`plan-review-disposition.md` resolve all seven findings:

1. Vitest is pinned exactly at 4.1.10.
2. The Vitest configuration is standalone and does not merge production Vite
   settings.
3. Runtime and type-check discovery both use `tests/**/*.test.ts`.
4. Lifecycle assertions explicitly exclude referential memo identity.
5. File-preview coverage retains `actionLabel` absence and specifies
   `window.location.href`, base insertion order, and doctype serialization.
6. Change-stream cases reset modules, install globals before dynamic import,
   and clean up the imported instance.
7. A test-owned strict TypeScript configuration is wired into `test:vitest`;
   manifest fixtures use `satisfies`, and the second Worker type proves default
   ownership.

**PASS**

Unresolved findings: 0.
