# Independent implementation re-review

## Decision

**APPROVE**

No unresolved Standards or Spec violation remains in commit
`0a5c983bb15dde5ad525140faec3db04b286da35`.

## Findings by severity

- **Critical:** none.
- **Major:** none.
- **Minor:** none.

## Standards

**PASS**

- The accepted structural correction is complete. The six shared contract types live in
  `web/src/lib/conversation/runControls/contracts.ts`; framework-free rules live in
  `web/src/lib/conversation/runControls/logic/runSelection.ts`; the Svelte renderer
  remains in the component layer.
- Production consumers import the shared shapes from the contract instead of
  redeclaring them. The logic remains directly unit-testable without Svelte, DOM,
  network, focus, callbacks, or mutable external state.
- The superseded component-local `composer/runSelection.ts` is deleted. Repository
  search finds neither a legacy-path import nor a component-local run-control contract
  declaration.
- The renderer is a cohesive view/intents boundary, not a second state owner or a
  speculative middleman. No actionable baseline code smell remains.
- `implementation-evidence.md` now retains the complete emitted output for every named
  ticket gate, explicitly records the no-output `git diff --check` result, and correctly
  leaves the program-reserved `./verify` to the final combined tree.
- Recorded and actual sizes agree:
  - `ConversationComposer.svelte`: 757 lines, limit 760;
  - `contracts.ts`: 84 lines, limit 400;
  - logic `runSelection.ts`: 260 lines, limit 400;
  - `ComposerRunControls.svelte`: 83 lines, limit 400.

## Spec

**PASS**

- `contracts.ts` exports exactly the six locked types and nothing else.
- logic `runSelection.ts` exports exactly
  `resolveComposerRunControls` and `applyComposerRunSelectionIntent`, and nothing else.
  Every shared shape is imported from `contracts.ts`.
- The move did not alter policy semantics: model normalization still precedes
  shown-model effort resolution; reconciliation remains immutable; carried values,
  actual-backend delivery, submit projection, and pure intent transitions retain the
  locked behavior.
- `ConversationComposer` still owns one `ComposerRunSelection`, applies reconciliation
  without a draft revision, records genuine backend/model/effort changes once, records
  no-op and delivery changes zero times, restores focus for model/effort choices, and
  retains backend/delivery while draft restoration replaces model/effort.
- The renderer still takes exactly `{ view, intents }`, owns no canonical state, emits no
  wrapper, and preserves model → effort → delivery → submit order, selectors,
  attributes, copy, and accessibility behavior.
- The structural harness keeps the exact top-level component inventory and separately
  compiles the nested renderer. Its idle and running assertions confirm that emitted
  controls remain direct children of `.chat-foot`.
- The previously missing public refusal proof is now present. The browser journey:
  1. chooses non-null model and effort values;
  2. sends an ordinary message through a held callback;
  3. observes both picker faces return to their cleared/default values while held;
  4. refuses the send;
  5. observes the exact text, model, effort, and image return through public DOM and
     `onSend` seams.
- The generated production bundle is retained, and every changed file remains inside
  the revised ticket allow-list.

## Re-review checks

Run against the corrected commit:

```text
npm --prefix web run test:vitest -- tests/conversation-run-selection.test.ts
  2 files passed; 30 tests passed; no type errors

node web/tests/conversation-pane.test.mjs
  conversation-pane.test.mjs: all assertions passed

npm --prefix web run check
  svelte-check found 0 errors and 0 warnings

git diff --check 0a9dfef5...0a5c983b -- . ':(exclude,glob)web/dist/assets/*.js'
  no output
```

The committed full gate evidence additionally records a clean build and 14 passing
named Playwright tests.

## Final summary

Standards: 0 findings. Spec: 0 findings. **Final verdict: APPROVE.**
