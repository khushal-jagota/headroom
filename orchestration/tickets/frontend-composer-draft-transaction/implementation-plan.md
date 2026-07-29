# Implementation plan

## 1. Prove the framework-free transaction first

Create `web/tests/conversation-draft-transaction.test.ts` before the production module.

Add failing tests that require:

- `beginComposerSend` to trim text, omit an empty text piece, then append pending images
  in visible order;
- no mutation or array aliasing of the supplied draft;
- every delivery mode to clear text and images without changing the composition revision;
- `steer` to retain model and effort picks;
- `run_when_free` and `send_now` to clear model and effort picks;
- `carriedRunValues` to preserve the exact supplied value, including an optional
  `backendKey`, without catalog/default resolution;
- `restoreRefusedComposerSend` to restore text, images, picks, image ids, and the same
  revision when the current draft still equals the attempt's after-send draft;
- restored text to come from the trimmed text piece that was actually sent, not from
  leading or trailing whitespace in `draftBeforeSend.text`;
- restoration to be denied independently by a changed revision, text, images, model,
  effort, or nonzero in-flight image intake;
- denied restoration to return the current draft and `nextImageId` unchanged.

Run the focused Vitest file and record the expected missing-module failure.

## 2. Implement the private transaction module

Create `web/src/components/conversation/composer/draftTransaction.ts`.

Export exactly the types and functions locked in `contract-lock.md`. Import the existing
types and reuse `pendingImagesAsPieces` and `restoredPendingImages`.

`beginComposerSend` will:

1. copy the supplied draft and image collection;
2. snapshot the carried run values;
3. build the ordered content run;
4. create an after-send draft with empty text and images;
5. retain model and effort only for `steer`;
6. preserve the revision;
7. perform no mutation, URL work, or other side effect.

`restoreRefusedComposerSend` will:

1. require zero in-flight image intakes;
2. compare revision, text, ordered image references, model, and effort with the
   attempt's after-send draft;
3. return the current state and id unchanged on any mismatch;
4. otherwise restore the text, existing data-preview image representation, before-send
   picks, and unchanged revision, advancing the image id exactly once per restored image.

Make the focused Vitest file green before editing the Svelte integration.

## 3. Prove the successful-steer correction in a browser

Extend only the existing rendered host state inside
`web/tests/conversation-pane.test.mjs` so the same composer can be placed into a running
Hermes state.

Add a failing rendered-browser scenario:

1. pick a model and reasoning effort;
2. select `steer`;
3. send an accepted message;
4. assert the callback mode is `steer` and both picker faces retain their values;
5. return to a non-running state and send the next accepted message;
6. assert that callback mode is `run_when_free`, the message carries the retained picks,
   and the controls then clear.

Append this scenario after assertions that depend on fixed send indexes, or capture its
starting send count. Do not renumber or weaken existing assertions or production DOM
expectations.

## 4. Integrate the transaction in `ConversationComposer.svelte`

Import the transaction functions and type.

Rename `composeRevision` to `compositionRevision`. Add one descriptively named local
draft-change operation; it is the only place that directly increments the revision.
Route these existing user changes through it exactly once:

- backend, model, and effort choices;
- textarea input;
- accepted image intake;
- image removal;
- command insertion;
- slash insertion.

Add small local read/apply helpers for the locked `ComposerDraft` shape. Backend selection
remains outside the draft because it is sticky across sends.

Rewrite `send()` in this order:

1. await `intakeTail`;
2. preserve the existing empty/disabled guard;
3. capture `effectiveMode`;
4. begin one immutable attempt;
5. apply its after-send draft;
6. release exactly its before-send image resources;
7. clear the native file input and intake error;
8. call the unchanged `onSend` callback with a mutable copy of the attempt content;
9. invoke refusal restoration only when `onSend` returns `false`;
10. preserve counted `sendsInFlight` accounting in `finally`.

When restoration succeeds, apply the returned draft and image id before the existing
`tick()`, same-textarea focus, end-caret placement, and cursor update.

Delete the superseded ad-hoc snapshot and restoration implementation. Do not alter
markup, selectors, styles, focus ownership, image URL release, delivery availability, or
the component's public props.

## 5. Risk checks

- **Older refusal overwrites newer work:** compare the complete after-send draft and
  revision, not only empty text.
- **Steer loses picks:** cover the mode table in pure tests and the actual picker faces in
  the rendered harness.
- **The next normal send does not consume retained picks:** assert its callback arguments
  and the picker reset.
- **Array aliasing changes an attempt after capture:** copy at the transaction seam.
- **Blob leak or double revocation:** keep resource release solely in the Svelte
  component.
- **Image intake races send or restore:** preserve `await intakeTail` and the in-flight
  intake guard.
- **Textarea identity/focus regresses:** leave the DOM unchanged and retain the rendered
  caret assertions.
- **Backend choice is cleared accidentally:** keep it out of `ComposerDraft`.

## 6. Size forecast

`draftTransaction.ts` should remain a small deep internal module, expected below 180
lines including contract comments. The component should lose the ad-hoc send/restore
implementation but may remain around 800–850 lines. That remaining size is an explicit
smell and the next composer ticket will address another semantic domain; this
prerequisite is not a line-count victory by itself.

## 7. Settled gates

Run once the work is complete, in this order:

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

Also audit:

- only ticket-allowed files changed;
- the transaction module exports exactly the locked interface;
- no direct revision increment remains outside the named operation;
- `ConversationComposer.svelte` keeps the same public props and production markup;
- the rebuilt `web/dist/index.html` names the emitted assets.

The frontend architecture program reserves the final canonical `./verify`; do not run it
for this prerequisite ticket.
