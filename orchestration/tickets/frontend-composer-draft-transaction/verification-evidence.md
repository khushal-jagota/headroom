# Verification evidence

## Focused pure transaction and rendered composer

The implementation report records the settled focused run:

```sh
npm --prefix web run test:vitest -- tests/conversation-draft-transaction.test.ts
node web/tests/conversation-pane.test.mjs
```

Results:

```text
Test Files  2 passed (2)
Tests       17 passed (17)
Type Errors no errors
conversation-pane.test.mjs: all assertions passed
```

## Svelte diagnostics

Command:

```sh
npm --prefix web run check
```

Exit code: `0`

```text
svelte-check found 0 errors and 0 warnings
```

## Production build

Command:

```sh
npm --prefix web run build
```

Exit code: `0`

```text
✓ 554 modules transformed.
dist/assets/index-C8ABQvyo.css  26.08 kB │ gzip:   4.20 kB
dist/assets/index-DoAxE1yU.js  485.09 kB │ gzip: 152.76 kB
✓ built in 1.29s
```

`web/dist/index.html` names `index-DoAxE1yU.js` and the unchanged
`index-C8ABQvyo.css`.

## Server-backed browser gates

Command:

```sh
.venv/bin/pytest -q \
  tests/e2e/test_dev_conversation_pane.py \
  tests/e2e/test_conversation_three_states.py
```

Exit code: `0`

```text
.......
```

## Scope and source audits

The changed implementation paths are exactly:

```text
web/dist/assets/index-CAMyhuZ7.js
web/dist/assets/index-DoAxE1yU.js
web/dist/index.html
web/src/components/conversation/ConversationComposer.svelte
web/src/components/conversation/composer/draftTransaction.ts
web/tests/conversation-draft-transaction.test.ts
web/tests/conversation-pane.test.mjs
```

plus this ticket's planning, evidence, and review files.

`draftTransaction.ts` exports exactly:

```text
ComposerDraft
ComposerSendAttempt
RefusedComposerSendRestoration
beginComposerSend
restoreRefusedComposerSend
```

The only direct `compositionRevision += 1` is inside `recordDraftChange`.
`composeRevision` no longer exists.

## Diff whitespace

Command:

```sh
git diff --check -- . ':(exclude,glob)web/dist/assets/*.js'
```

Exit code: `0`

Full output: none.

The generated JavaScript has the same intentional Svelte runtime
space/tab/newline template literal as the preceding committed bundle:

```text
new index-DoAxE1yU.js:  1:44338
old index-CAMyhuZ7.js:  1:44338
```

The generated runtime expression is left semantically intact.

## Size

```text
911 web/src/components/conversation/ConversationComposer.svelte
124 web/src/components/conversation/composer/draftTransaction.ts
200 web/tests/conversation-draft-transaction.test.ts
```

The component remains a severe size smell and has grown by twelve lines because the
transaction is now explicit at the Svelte boundary. This ticket intentionally establishes
the safe async seam; it does not satisfy the composer's final size objective. The next
composer extraction remains required.

The canonical `./verify` was not run. The frontend architecture program reserves one run
for its settled final tree.

## Closeout merge

Before publication, `origin/staging` advanced from `8c13d666` to `d1a79b17` with the
Ticket-page redesign. Its production source did not overlap the composer, but both
branches had rebuilt `web/dist`. Current `origin/staging` was merged, the generated
bundle conflict was resolved by one build from the combined source, and the combined
tree emitted:

```text
✓ 554 modules transformed.
dist/assets/index-C8ABQvyo.css   26.08 kB │ gzip:   4.20 kB
dist/assets/index--u397DQq.js   485.01 kB │ gzip: 152.74 kB
✓ built in 1.25s
```

The combined tree then passed:

```text
npm --prefix web run check
svelte-check found 0 errors and 0 warnings

.venv/bin/pytest -q \
  tests/e2e/test_dev_conversation_pane.py \
  tests/e2e/test_conversation_three_states.py
100% passed
```
