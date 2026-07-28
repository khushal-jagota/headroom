# Frontend composer draft transaction

## Outcome

Make the composer's send/refusal state transition one named, testable transaction before
splitting its other semantic domains.

`ConversationComposer.svelte` remains the public composition root and the only owner of
its textarea, focus, keyboard events, image resource release, and `onSend` side effect.
A private framework-free module owns the immutable draft snapshots that cross the async
send boundary.

This is the prerequisite seam for later run-selection, image-lifecycle, and
command-editing extractions. It is not expected to bring the 899-line component into the
final 300–600-line range by itself.

## Why this is first

Text, pending images, model and effort picks, and the composition revision currently
participate in one atomic operation:

1. wait for pending image intake;
2. snapshot the draft;
3. clear what the chosen delivery consumes;
4. send;
5. restore the snapshot only when a definite refusal returns and no newer composing has
   happened.

That operation is presently spread across mutable Svelte variables. Extracting visual
children first would replace it with bindings, reset callbacks, and duplicated revision
rules.

The audit also found a concrete contract mismatch. `composer.ts` states that a steer
cannot carry a run-value change, so pending model and effort picks remain pending.
`ConversationComposer.svelte` currently clears them before every successful send. This
ticket makes the documented rule executable:

- an accepted `steer` clears sent text and images but retains model and effort picks;
- the next non-steer send carries and clears those retained picks;
- an accepted non-steer send continues to clear its picks immediately;
- a refused send restores the sent text, images, and picks only if the current draft is
  exactly the state left by that attempt and no image intake is in flight.

## Contract files

- Existing public component:
  `web/src/components/conversation/ConversationComposer.svelte`.
- New private transaction contract:
  `web/src/components/conversation/composer/draftTransaction.ts`.
- Existing value contracts:
  - `RunValues` from `web/src/lib/conversation/composer.ts`;
  - `PendingConversationImage` from
    `web/src/lib/conversation/pendingImages.ts`;
  - `PromptDeliveryMode` and `SentMessagePiece` from
    `web/src/lib/conversation/wire.ts`.

The exact private interface and behavior are locked in `contract-lock.md`.

## Required work

1. Add the private framework-free transaction module.
2. Replace scattered direct `composeRevision += 1` calls with one descriptively named
   local draft-change operation. Every existing user composition change must still
   advance the revision exactly once:
   - backend, model, or effort selection;
   - text input;
   - accepted image intake;
   - image removal;
   - command insertion or slash insertion.
3. Make `send()` create one immutable attempt snapshot after `intakeTail` settles.
4. Apply the attempt's after-send draft before calling `onSend`.
5. Keep blob URL release in the component and release exactly the images captured by the
   attempt.
6. On refusal, ask the transaction module whether the attempt may restore. Keep
   `tick()`, textarea focus, and caret placement in the component.
7. Add focused unit tests for the transaction and a real rendered-browser assertion for
   the successful-steer behavior.
8. Rebuild `web/dist`.

## Non-goals

- Do not extract visual pass-through children.
- Do not extract run-selection policy, image intake, or command cursor state in this
  ticket.
- Do not change the composer's public props, callback shapes, selectors, classes,
  accessibility labels, DOM ordering, or mounted identity.
- Do not change delivery option availability or mode persistence.
- Do not move URL creation/revocation, Svelte state, focus, caret, or keyboard handling
  into the framework-free module.
- Do not add Tailwind or another frontend stack.
- Do not run the canonical `./verify`; the frontend architecture program reserves one
  settled final run.

## Allowed production and test files

- `web/src/components/conversation/ConversationComposer.svelte`
- `web/src/components/conversation/composer/draftTransaction.ts`
- `web/tests/conversation-draft-transaction.test.ts`
- `web/tests/conversation-pane.test.mjs`
- freshly rebuilt `web/dist`
- this ticket's evidence, plan, disposition, and review files

No existing public contract or unrelated source file may change.

## Acceptance gates

Run the narrowest proof after the work is settled:

```sh
npm --prefix web run test:vitest -- tests/conversation-draft-transaction.test.ts
node web/tests/conversation-pane.test.mjs
npm --prefix web run check
npm --prefix web run build
.venv/bin/pytest -q \
  tests/e2e/test_dev_conversation_pane.py \
  tests/e2e/test_conversation_three_states.py
git diff --check -- . ':(exclude,glob)web/dist/assets/*.js'
```

The existing generated Svelte runtime JavaScript contains an intentional raw
space/tab/newline template literal that can trip Git's end-of-line whitespace warning
when the content hash creates a new file. Confirm any generated-only warning matches the
same runtime expression in the previous committed bundle; do not alter its semantics.

## Done when

- the named gates pass;
- the implementation review reports no unresolved contract or standards violation;
- the component has one async transaction seam rather than ad-hoc snapshot/restore
  state;
- successful steer visibly retains pending model and effort picks;
- normal send and refusal behavior remain covered;
- the exact green revision is integrated to `staging`.
