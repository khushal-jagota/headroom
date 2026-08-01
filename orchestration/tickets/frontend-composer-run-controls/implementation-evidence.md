# Implementation evidence

## Intent

The composer's run controls now form one semantic subsystem: a framework-free
policy answers what the next message runs under, and a fragment-root Svelte
component renders that answer. `ConversationComposer.svelte` remains the only
owner of the selection, draft transaction, focus, images, command editing, and
send side effect.

## Implementation

- Added `lib/conversation/runControls/contracts.ts` with exactly the six locked
  shared types.
- Added `lib/conversation/runControls/logic/runSelection.ts` with exactly the
  two locked functions. It resolves backend catalogs and defaults,
  normalization, carried run values, picker presentation, delivery, and submit
  state without Svelte, DOM, network, focus, or other side effects.
- Deleted the superseded component-local `composer/runSelection.ts`; the
  parent, renderer, and tests now import from the semantic package.
- Added `composer/ComposerRunControls.svelte` with exactly `{ view, intents }`.
  It emits the model, effort, delivery, and submit nodes as a fragment, so they
  remain direct children of `.chat-foot`.
- Replaced the parent's four run-value states with one
  `ComposerRunSelection`. One normalization effect applies catalog
  reconciliation without changing `compositionRevision`; one intent handler
  records genuine backend/model/effort changes and returns focus for model and
  effort choices.
- Kept draft restoration and send ownership in the parent. Restored drafts
  replace only model and effort, while backend and delivery remain selected.
  Sends use the resolved carried values and effective delivery mode.
- Extended the structural/browser harness to compile the nested renderer,
  preserve the exact top-level component inventory, and assert the idle and
  running `.chat-foot` direct-child order.
- Extended the held-send refusal journey to choose non-null model and effort
  values, observe both visible values clear during an ordinary send, and
  observe the exact text, model, and effort return after refusal through the
  public component DOM and `onSend` seam.
- Added focused policy tests for backend switching, catalog/default selection,
  normalization, picker presentation, effort rules, carried values, delivery,
  submit projection, and pure transitions.
- Rebuilt `web/dist`.

## Acceptance gates

The completed production and test change passed the ticket's full focused gate
set:

```text
$ npm --prefix web run test:vitest -- tests/conversation-run-selection.test.ts

> test:vitest
> vitest run --typecheck tests/conversation-run-selection.test.ts

Testing types with tsc and vue-tsc is an experimental feature.
Breaking changes might not follow SemVer, please pin Vitest's version when using it.

 RUN  v4.1.10 /Users/khushaljagota/Coding/planning-v2-worktrees/frontend-composer-run-controls/web


 Test Files  2 passed (2)
      Tests  30 passed (30)
Type Errors  no errors
   Start at  01:44:04
   Duration  447ms (transform 51ms, setup 0ms, import 59ms, tests 4ms, environment 0ms, typecheck 319ms)

$ node web/tests/conversation-pane.test.mjs
conversation-pane.test.mjs: all assertions passed

$ npm --prefix web run check

> check
> svelte-check --tsconfig ./tsconfig.json

Loading svelte-check in workspace: /Users/khushaljagota/Coding/planning-v2-worktrees/frontend-composer-run-controls/web
Getting Svelte diagnostics...

svelte-check found 0 errors and 0 warnings

$ npm --prefix web run build

> build
> vite build

vite v6.4.3 building for production...

/assets/tokens.css doesn't exist at build time, it will remain unchanged to be resolved at runtime

/assets/app.css doesn't exist at build time, it will remain unchanged to be resolved at runtime
transforming...
✓ 556 modules transformed.
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
dist/assets/index-EWwUA7uo.css                                26.08 kB │ gzip:   4.21 kB
dist/assets/index-Y2p_V1HR.js                                487.96 kB │ gzip: 153.45 kB
✓ built in 1.15s

$ .venv/bin/pytest -q \
    tests/e2e/test_dev_conversation_pane.py \
    tests/e2e/test_conversation_three_states.py
..............                                                   [100%]

$ git diff --check -- . ':(exclude,glob)web/dist/assets/*.js'
```

`git diff --check` exited 0 with no output.

The program-reserved `./verify` was not run.

## Structure and final production sizes

```text
$ wc -l \
    web/src/components/conversation/ConversationComposer.svelte \
    web/src/lib/conversation/runControls/contracts.ts \
    web/src/lib/conversation/runControls/logic/runSelection.ts \
    web/src/components/conversation/composer/ComposerRunControls.svelte
     757 web/src/components/conversation/ConversationComposer.svelte
      84 web/src/lib/conversation/runControls/contracts.ts
     260 web/src/lib/conversation/runControls/logic/runSelection.ts
      83 web/src/components/conversation/composer/ComposerRunControls.svelte
    1184 total

$ rg -n '^export (type|function)' \
    web/src/lib/conversation/runControls/contracts.ts \
    web/src/lib/conversation/runControls/logic/runSelection.ts
web/src/lib/conversation/runControls/logic/runSelection.ts:156:export function resolveComposerRunControls(
web/src/lib/conversation/runControls/logic/runSelection.ts:232:export function applyComposerRunSelectionIntent(
web/src/lib/conversation/runControls/contracts.ts:9:export type ComposerRunSelection = Readonly<{
web/src/lib/conversation/runControls/contracts.ts:16:export type ComposerRunControlsInput = Readonly<{
web/src/lib/conversation/runControls/contracts.ts:32:export type ComposerRunSelectionIntent =
web/src/lib/conversation/runControls/contracts.ts:38:export type ComposerRunControlChoice = Readonly<{
web/src/lib/conversation/runControls/contracts.ts:44:export type ComposerRunControlsView = Readonly<{
web/src/lib/conversation/runControls/contracts.ts:77:export type ComposerRunControlIntents = Readonly<{

$ if rg -n \
    'components/conversation/composer/runSelection|composer/runSelection|from "\./runSelection"' \
    web/src web/tests; then exit 1; else printf 'legacy runSelection path references: none\n'; fi
legacy runSelection path references: none

$ if rg -n '^export type ComposerRun(Control|Selection)' \
    web/src/components/conversation; then exit 1; \
    else printf 'component-local run-control contract declarations: none\n'; fi
component-local run-control contract declarations: none
```

The public composer dropped from 911 to 757 lines. All three new production
files are below the 400-line hard limit. The planned command-editing extraction
remains the next semantic step toward the two-ticket 600-line checkpoint.
