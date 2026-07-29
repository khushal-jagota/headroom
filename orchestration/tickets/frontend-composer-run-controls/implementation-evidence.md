# Implementation evidence

## Intent

The composer's run controls now form one semantic subsystem: a framework-free
policy answers what the next message runs under, and a fragment-root Svelte
component renders that answer. `ConversationComposer.svelte` remains the only
owner of the selection, draft transaction, focus, images, command editing, and
send side effect.

## Implementation

- Added `composer/runSelection.ts` with exactly the locked policy types and two
  functions. It resolves backend catalogs and defaults, normalization, carried
  run values, picker presentation, delivery, and submit state without Svelte,
  DOM, network, focus, or other side effects.
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
- Added focused policy tests for backend switching, catalog/default selection,
  normalization, picker presentation, effort rules, carried values, delivery,
  submit projection, and pure transitions.
- Rebuilt `web/dist`.

## Acceptance gates

The completed production and test change passed the ticket's full focused gate
set:

```text
$ npm --prefix web run test:vitest -- tests/conversation-run-selection.test.ts
Test Files  2 passed (2)
Tests       30 passed (30)
Type Errors no errors

$ node web/tests/conversation-pane.test.mjs
conversation-pane.test.mjs: all assertions passed

$ npm --prefix web run check
svelte-check found 0 errors and 0 warnings

$ npm --prefix web run build
vite v6.4.3 building for production...
✓ 556 modules transformed.
dist/assets/index-EWwUA7uo.css  26.08 kB │ gzip: 4.21 kB
dist/assets/index-Y2p_V1HR.js  487.96 kB │ gzip: 153.45 kB
✓ built in 1.12s

$ .venv/bin/pytest -q \
    tests/e2e/test_dev_conversation_pane.py \
    tests/e2e/test_conversation_three_states.py
..............                                                   [100%]

$ git diff --check -- . ':(exclude,glob)web/dist/assets/*.js'
[no output]
```

The program-reserved `./verify` was not run.

## Final production sizes

```text
755 web/src/components/conversation/ConversationComposer.svelte
334 web/src/components/conversation/composer/runSelection.ts
 83 web/src/components/conversation/composer/ComposerRunControls.svelte
```

The public composer dropped from 911 to 752 lines. Both new production files
are below the 400-line hard limit. The planned command-editing extraction
remains the next semantic step toward the two-ticket 600-line checkpoint.
