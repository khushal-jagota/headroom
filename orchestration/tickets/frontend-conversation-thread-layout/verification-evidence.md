# Verification evidence

All commands ran from:

```text
/Users/khushaljagota/Coding/planning-v2-worktrees/frontend-conversation-thread-layout
```

The required interface-first RED is recorded in
`red-evidence.txt`. It failed because
`../src/lib/conversation/threadLayout` did not exist.

## Focused thread-layout suites

Command:

```sh
npm --prefix web run test:vitest -- tests/conversation-thread-items.test.ts tests/conversation-thread-plan.test.ts tests/conversation-turn-time.test.ts
```

Exit code: `0`

Full output:

```text
> test:vitest
> vitest run --typecheck tests/conversation-thread-items.test.ts tests/conversation-thread-plan.test.ts tests/conversation-turn-time.test.ts

Testing types with tsc and vue-tsc is an experimental feature.
Breaking changes might not follow SemVer, please pin Vitest's version when using it.

 RUN  v4.1.10 /Users/khushaljagota/Coding/planning-v2-worktrees/frontend-conversation-thread-layout/web


 Test Files  6 passed (6)
      Tests  48 passed (48)
Type Errors  no errors
   Start at  23:05:26
   Duration  460ms (transform 143ms, setup 0ms, import 174ms, tests 12ms, environment 0ms, typecheck 317ms)
```

## Rest-line harness

Command:

```sh
node web/tests/conversation-rest-line.test.mjs
```

Exit code: `0`

Full output:

```text
conversation-rest-line.test.mjs: all assertions passed
```

## Rendered-pane harness

Command:

```sh
node web/tests/conversation-pane.test.mjs
```

Exit code: `0`

Full output:

```text
conversation-pane.test.mjs: all assertions passed
```

## Svelte and TypeScript checks

Command:

```sh
npm --prefix web run check
```

Exit code: `0`

Full output:

```text
> check
> svelte-check --tsconfig ./tsconfig.json

Loading svelte-check in workspace: /Users/khushaljagota/Coding/planning-v2-worktrees/frontend-conversation-thread-layout/web
Getting Svelte diagnostics...

svelte-check found 0 errors and 0 warnings
```

## Production build

Command:

```sh
npm --prefix web run build
```

Exit code: `0`

Full output:

```text
> build
> vite build

vite v6.4.3 building for production...

/assets/tokens.css doesn't exist at build time, it will remain unchanged to be resolved at runtime

/assets/app.css doesn't exist at build time, it will remain unchanged to be resolved at runtime
transforming...
✓ 549 modules transformed.
rendering chunks...
computing gzip size...
dist/index.html                                                0.78 kB │ gzip:   0.40 kB
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
dist/assets/index-D_OL2IiV.css                                26.08 kB │ gzip:   4.20 kB
dist/assets/index-BLYhu5uk.js                                482.62 kB │ gzip: 152.18 kB
✓ built in 1.18s
```

The rebuilt checked-in bundle is retained: `web/dist/index.html` now points to
`index-BLYhu5uk.js`, and the superseded `index-DSasre04.js` is deleted.

## Aggregate frontend suite

Command:

```sh
npm --prefix web test
```

Exit code: `0`

Full output:

```text
> test
> npm run test:vitest && npm run test:legacy


> test:vitest
> vitest run --typecheck

Testing types with tsc and vue-tsc is an experimental feature.
Breaking changes might not follow SemVer, please pin Vitest's version when using it.

 RUN  v4.1.10 /Users/khushaljagota/Coding/planning-v2-worktrees/frontend-conversation-thread-layout/web


 Test Files  38 passed (38)
      Tests  366 passed (366)
Type Errors  no errors
   Start at  23:05:55
   Duration  803ms (transform 1.15s, setup 0ms, import 1.36s, tests 412ms, environment 1ms, typecheck 302ms)


> test:legacy
> node tests/managed-markdown.test.mjs && node tests/markdown-renderer.test.mjs && node tests/browser-css.test.mjs && node tests/vps-status.test.mjs && node tests/backlog-ideas.test.mjs && node tests/production-surfaces.test.mjs && node tests/worker-configuration-setup.test.mjs && node tests/conversation-rest-line.test.mjs && node tests/conversation-pane.test.mjs

browser-css.test.mjs: all assertions passed
vps-status.test.mjs: all assertions passed
backlog-ideas.test.mjs: all assertions passed
production-surfaces.test.mjs: all assertions passed
worker-configuration-setup.test.mjs: all assertions passed
conversation-rest-line.test.mjs: all assertions passed
conversation-pane.test.mjs: all assertions passed
```

## Diff whitespace check

Command:

```sh
git diff --check
```

Exit code: `0`

Full output: empty.

The frontend architecture program reserves the repository-wide `./verify`; it was
not run for this ticket.
