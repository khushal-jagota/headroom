# Verification evidence

## Resolved implementation-gate finding

The first post-extraction structural harness invocation failed before the final settled
gate sequence.

Command:

```sh
node web/tests/conversation-pane.test.mjs
```

Exit code: `1`

Full output:

```text
node:internal/modules/run_main:123
    triggerUncaughtException(
    ^

AssertionError [ERR_ASSERTION]: the pane must reuse .chat-jump rather than forking the stylesheet
    at file:///Users/khushaljagota/Coding/planning-v2-worktrees/frontend-conversation-viewport/web/tests/conversation-pane.test.mjs:96:10 {
  generatedMessage: false,
  code: 'ERR_ASSERTION',
  actual: false,
  expected: true,
  operator: '==',
  diff: 'simple'
}

Node.js v22.22.3
```

Cause: the harness's exact top-level component inventory did not include the new nested
viewport source, so its shared-class scan could not see the moved `.chat-jump` markup.
The owner recorded a ticket/plan correction. The harness now retains the exact top-level
inventory assertion and additionally compiles and inspects
`viewport/ConversationViewport.svelte` for warnings, token usage, and shared classes.
No behavior assertion was removed or weakened.

The final settled gate sequence below was run after that correction and after the final
production dependency-tracking audit.

## Structural conversation-pane harness

Command:

```sh
node web/tests/conversation-pane.test.mjs
```

Exit code: `0`

Full output:

```text
conversation-pane.test.mjs: all assertions passed
```

## Svelte and TypeScript diagnostics

Command:

```sh
npm --prefix web run check
```

Exit code: `0`

Full output:

```text

> check
> svelte-check --tsconfig ./tsconfig.json

Loading svelte-check in workspace: /Users/khushaljagota/Coding/planning-v2-worktrees/frontend-conversation-viewport/web
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
✓ 553 modules transformed.
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
dist/assets/index-C8ABQvyo.css                                26.08 kB │ gzip:   4.20 kB
dist/assets/index-CAMyhuZ7.js                                483.73 kB │ gzip: 152.30 kB
✓ built in 1.13s
```

## Full frontend tests

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

 RUN  v4.1.10 /Users/khushaljagota/Coding/planning-v2-worktrees/frontend-conversation-viewport/web


 Test Files  38 passed (38)
      Tests  366 passed (366)
Type Errors  no errors
   Start at  23:46:20
   Duration  736ms (transform 1.09s, setup 0ms, import 1.18s, tests 490ms, environment 1ms, typecheck 285ms)


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

## Targeted browser characterization

Command:

```sh
.venv/bin/pytest -q \
  tests/e2e/test_dev_conversation_pane.py \
  tests/e2e/test_conversation_three_states.py
```

Exit code: `0`

Full output:

```text
..............                                                           [100%]
```

## Diff whitespace checks

Command:

```sh
git diff --cached --check -- \
  . \
  ':(exclude,glob)web/dist/assets/*.js'
```

Exit code: `0`

Full output: none.

The generated JavaScript asset has one intentional whitespace-at-end-of-line
sequence inside the Svelte runtime's template literal that enumerates space, tab,
and newline. The previous committed Vite bundle contains the same sequence at the
same generated runtime expression:

```sh
awk '/[[:blank:]\r]$/ { print NR ":" length($0) }' \
  web/dist/assets/index-CAMyhuZ7.js
git show HEAD:web/dist/assets/index-BLYhu5uk.js |
  awk '/[[:blank:]\r]$/ { print NR ":" length($0) }'
```

Both commands print only `1:44338`. The generated asset is therefore left
byte-semantically intact instead of deleting whitespace characters from the
runtime's whitespace set.
