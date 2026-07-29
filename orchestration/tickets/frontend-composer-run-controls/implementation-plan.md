# Implementation plan

## Intent and boundary

Extract the answer to “what will this message run under?” without moving draft,
image, command, focus, or send-transaction ownership out of
`ConversationComposer.svelte`.

The framework-free policy is the only place that resolves backend catalogs,
defaults, explicit picks, carried values, delivery, and submit presentation. The
new Svelte component only renders that resolved view and invokes named intents.
The parent remains the sole state and side-effect owner.

## Public test seams

Tests use only these seams:

1. Import the two locked functions and their exported types from
   `composer/runSelection.ts`:
   - `resolveComposerRunControls(input)`
   - `applyComposerRunSelectionIntent(input, intent)`
2. Render the existing public `ConversationComposer` interface. Assert its
   established selectors, direct-child order, focus, and `onSend`/`onStop`
   observations; do not inspect Svelte local state.
3. Compile `composer/ComposerRunControls.svelte` through the existing structural
   harness while leaving the exact top-level conversation-component inventory
   unchanged.
4. Exercise the existing application-level Playwright journeys named in the
   ticket.

No test imports an unexported helper or reaches into a component instance.

## Vertical red/green slices

### 1. Backend, model, and carried-value policy

Add the first focused cases to
`web/tests/conversation-run-selection.test.ts`, then implement the minimum
resolver and intent behavior to make them green:

- an untouched pre-conversation selection shows the caller's backend/catalog and
  concrete start model, carries that model, and invents neither backend nor effort;
- choosing another backend is a no-op when locked/already showing, otherwise it
  changes only the backend and clears model/effort;
- a pre-conversation backend pick uses that snapshot's catalog and defaults and
  carries the explicitly chosen backend;
- an existing conversation shows and locks its actual backend and carries only
  explicit run-value picks;
- an invalid explicit model is cleared only when a non-empty active catalog
  disproves it;
- a current/preselected model absent from the catalog is prepended as a visible
  choice;
- catalog/default reconciliation does not mutate the input.

Use small complete `BackendSnapshot` fixtures, and deep-equal only the locked
public result fields.

### 2. Effort and picker presentation policy

Add red cases, then complete the resolver:

- effort options come from the model actually showing; a model's explicit empty
  list is authoritative;
- an invalid picked effort is cleared against those options;
- a current/preselected effort absent from the offered list remains visible;
- empty shown effort produces a bare picker when choices exist, while no choices
  produces `effort: null`;
- model names, detail/model-id second lines, model title, and effort rows exactly
  preserve the existing presentation.

Derive presentation from the normalized selection, so a value cleared in this
resolution cannot remain on the same returned view.

### 3. Delivery, submit, and pure transitions

Add red cases, then finish both policy functions:

- delivery options are based on the actual `backendKey`, appear only while
  running, and retain the selected mode;
- effective delivery is the selection while running and `run_when_free` while
  idle;
- submit preserves existing send/stop action, `on`/`stop` meaning, sending state,
  disabled rule, tooltip, and accessible label for empty, sendable, in-flight,
  disabled, and running inputs;
- model, effort, and delivery intents change only their named field;
- all intent calls are pure and retain object values not named by the intent.

Keep all helpers private so `runSelection.ts` exports exactly the contract-lock
surface.

### 4. Fragment renderer and parent integration

First extend `conversation-pane.test.mjs` so the new nested component is compiled
without adding it to `expectedInventory`. Move the source-level `chat-seg`
ownership assertion to the nested renderer and add a rendered direct-child-order
assertion for `.chat-foot`.

Then:

1. Add `ComposerRunControls.svelte` with exactly `{ view, intents }`.
2. Cut the existing backend snippet, model picker, effort picker, delivery
   segment, and send/stop button into it without rewriting their attributes,
   copy, keyed blocks, or class expressions.
3. Render those five controls as a Svelte fragment: no outer element.
4. Replace the parent's four run states with one
   `$state<ComposerRunSelection>`.
5. Derive one `ComposerRunControlsView` from current props plus
   `inputDisabled`, sendable content, and `sendsInFlight`.
6. In one normalization effect, compare all four selection fields before
   assigning `view.normalizedSelection`; never record a draft revision there.
   The value comparison prevents a derived-view/assignment loop even if the
   resolver returns a fresh object.
7. Route the four selection changes through one discriminated local intent
   handler. Compare before/after values, assign once, record exactly one revision
   for a genuine backend/model/effort change, record none for delivery, and
   restore textarea focus only for model/effort.
8. Build a stable named-intents object whose `send` and `stop` entries delegate
   to the existing parent-owned operations.
9. Read model/effort from the single selection in `currentComposerDraft`.
   When applying a restored draft, replace only those two fields and preserve
   backend and delivery. Pass `view.carriedRunValues` and
   `view.effectiveDeliveryMode` into `beginComposerSend`.

Extend the browser assertions at the existing public composer seam to prove:

- idle `.chat-foot` direct children remain slash, image, hidden file input,
  model, effort (when present), send;
- running mode inserts delivery immediately before stop;
- model/effort choice still returns focus to the textarea;
- backend switch keeps the model panel open and resets its filter/catalog;
- ordinary send clears picks, steer retains them, and refusal restores them;
- the child introduces no wrapper and no DOM order/selector/accessibility
  change.

## DOM preservation rule

Treat the moved markup as a literal move. The parent keeps the slash, image, and
file-input nodes; the child begins with the model picker and ends with the
send/stop button. Do not rename classes/data attributes, alter button text or
titles, change keyed `each` expressions, insert a wrapper, or move the backend
rail outside the model picker's snippet. Existing SSR and browser assertions
remain the behavioral oracle.

## Expected sizes

- `ConversationComposer.svelte`: approximately 680–720 lines; hard limit 760.
- `composer/runSelection.ts`: approximately 260–340 lines; hard limit 400.
- `composer/ComposerRunControls.svelte`: approximately 100–160 lines; hard
  limit 400.
- `conversation-run-selection.test.ts`: approximately 220–320 lines.

If the parent remains near the hard limit, do not pull command/image behavior
into this ticket merely to reduce it. Record command editing as the next
semantic extraction, whose checkpoint must bring the parent below 600 lines.

## Focused gates

Run once after the complete ticket-shaped change:

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

Also record `wc -l` for the three production files. Do not run `./verify` in
this ticket; the frontend architecture program reserves it for the final
settled tree.
