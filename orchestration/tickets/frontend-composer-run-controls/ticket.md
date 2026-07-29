# Frontend composer run controls

## Outcome

Extract the composer's backend/model/effort/delivery/submit subsystem into:

- one private semantic package with a contracts file and framework-free logic module
  that resolves the complete visible and send-time answer; and
- one cohesive fragment-root Svelte renderer driven by that resolved view and named
  intents.

`ConversationComposer.svelte` remains the public composition root and single owner of
the controlled selection state, draft transaction, textarea, focus, images, command
editing, permission takeover, and send side effect.

This is a semantic extraction, not a final composer-size claim. The current component is
911 lines. This ticket should reduce it to no more than 760 lines. The command-editing
module is the next planned extraction and the two-ticket checkpoint must bring the
composer to no more than 600 lines.

## Semantic boundary

The run-controls subsystem answers one question: **what would this message run under,
and what controls represent that answer?**

It owns:

- which backend is showing and whether it is locked;
- which catalog and defaults apply after a pre-conversation backend switch;
- the distinction between shown defaults and explicit picks;
- invalid model/effort reconciliation;
- the exact `RunValues` carried by a message;
- model and effort picker presentation;
- delivery options and effective delivery mode;
- send/stop presentation.

It does not own the draft or send transaction. The composer holds one
`ComposerRunSelection` value, reads the policy's carried/effective answer when sending,
and applies run-value picks restored by `draftTransaction.ts`.

## Contract files

- New private contracts:
  `web/src/lib/conversation/runControls/contracts.ts`.
- New private policy:
  `web/src/lib/conversation/runControls/logic/runSelection.ts`.
- New private renderer:
  `web/src/components/conversation/composer/ComposerRunControls.svelte`.
- Existing public component:
  `web/src/components/conversation/ConversationComposer.svelte`.
- Existing contracts:
  - `RunValues` and `DeliveryOption` from
    `web/src/lib/conversation/composer.ts`;
  - backend/model/snapshot/delivery types from
    `web/src/lib/conversation/wire.ts`;
  - `ComposerDraft` from
    `web/src/components/conversation/composer/draftTransaction.ts`.

The exact private types, functions, renderer props, and behavior are locked in
`contract-lock.md`.

## Required work

1. Add focused pure tests for run selection and projection.
2. Add the contract and policy modules with no Svelte, DOM, network, focus, or side
   effect. Both the policy and renderer import their shared shapes from the contract
   module.
3. Update the parent, renderer, and tests to import from the new semantic package, then
   delete the superseded
   `web/src/components/conversation/composer/runSelection.ts`. No import or declaration
   may remain at the legacy path.
4. Replace `pickedBackend`, `pickedModel`, `pickedEffort`, and `mode` with one
   `ComposerRunSelection` state object in the public composer.
5. Derive one `ComposerRunControlsView`.
6. Reconcile only `view.normalizedSelection` in an effect. Reconciliation caused by
   changing catalogs/defaults is not a user draft change and must not advance
   `compositionRevision`.
7. Route user intents through one local handler:
   - backend/model/effort changes each record exactly one draft revision;
   - backend switching clears model and effort;
   - model and effort choices return focus to the same textarea;
   - delivery-mode changes do not change the draft revision;
   - send and stop delegate to the existing component-owned operations.
8. Move the exact backend rail, model picker, effort picker, delivery segment, and
   send/stop button markup into `ComposerRunControls.svelte`.
9. Render no wrapper. The emitted controls remain direct children of `.chat-foot` in the
   exact existing order.
10. Preserve the draft transaction's model/effort snapshot and restoration behavior
   through the controlled selection state.
11. Extend the structural harness to compile and inspect the nested renderer without
    changing its exact top-level conversation-component inventory assertion.
12. Rebuild `web/dist`.

## Non-goals

- Do not change `ConversationComposer` public props or callbacks.
- Do not change any production selector, class, data attribute, accessibility label,
  tooltip, DOM order, direct-child relationship, focus behavior, or visible copy.
- Do not extract slash/image controls, command menu, ask controls, or queue tray here.
- Do not let the child own or mirror a second selection state.
- Do not add bindable props, exported imperative reset/snapshot methods, custom events,
  context entries, a store, or a wrapper element.
- Do not modify `web/src/lib/conversation/composer.ts`.
- Do not add Tailwind or another frontend stack.
- Do not run `./verify`; the frontend architecture program reserves the canonical run
  for its settled tree.

## Allowed production and test files

- `web/src/components/conversation/ConversationComposer.svelte`
- `web/src/components/conversation/composer/runSelection.ts` (removal only)
- `web/src/lib/conversation/runControls/contracts.ts`
- `web/src/lib/conversation/runControls/logic/runSelection.ts`
- `web/src/components/conversation/composer/ComposerRunControls.svelte`
- `web/tests/conversation-run-selection.test.ts`
- `web/tests/conversation-pane.test.mjs`
- freshly rebuilt `web/dist`
- this ticket's planning, evidence, disposition, and review files

## Acceptance gates

```sh
npm --prefix web run test:vitest -- tests/conversation-run-selection.test.ts
node web/tests/conversation-pane.test.mjs
npm --prefix web run check
npm --prefix web run build
.venv/bin/pytest -q \
  tests/e2e/test_dev_conversation_pane.py \
  tests/e2e/test_conversation_three_states.py
git diff --check -- . ':(exclude,glob)web/dist/assets/*.js'
```

## Done when

- the named gates pass;
- independent review reports no unresolved Standards or Spec violation;
- `ConversationComposer.svelte` is at most 760 lines;
- each new production file is at most 400 lines;
- the child takes exactly `{ view, intents }` and emits no wrapper;
- the composer owns exactly one run-selection state;
- the previous picker, backend, delivery, send/stop, steer-retention, refusal, and focus
  browser behavior remains green;
- the exact green revision is integrated to `staging`.
