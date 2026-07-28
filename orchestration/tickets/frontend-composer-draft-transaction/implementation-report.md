# Implementation report

## Result

Implemented the reviewed composer draft transaction at fixed point
`8c13d66699504c59f939bb2bea9f15efd64e8b43`.

The composer now crosses its asynchronous send through one immutable attempt:

1. wait for image intake;
2. snapshot the current draft and carried run values;
3. apply the mode-specific after-send draft;
4. release the captured image resources;
5. send;
6. restore only after a definite refusal when the whole after-send draft is still
   current.

The locked owner decision is implemented: a successful steer retains model and effort
picks, and the next accepted non-steer send carries and clears them.

## Files changed

### `web/src/components/conversation/composer/draftTransaction.ts`

- Added the exact three locked exported types.
- Added the exact two locked exported functions.
- `beginComposerSend`:
  - snapshots the draft and carried run values without input mutation;
  - trims and orders sent content;
  - always clears sent text and images;
  - retains picks only for `steer`;
  - preserves the composition revision.
- `restoreRefusedComposerSend`:
  - checks in-flight intake, revision, text, ordered images, model, and effort;
  - returns the current draft and image id unchanged on mismatch;
  - restores the sent trimmed text, existing restored-image representation, explicit
    picks, and image ids on a match.
- The module has no Svelte, DOM, focus, URL, network, or resource-release side effect.
- The file is 124 lines.

### `web/src/components/conversation/ConversationComposer.svelte`

- Replaced the ad-hoc send/refusal snapshot with the private transaction module.
- Renamed the revision to `compositionRevision`.
- Added `recordDraftChange`, the only direct revision increment.
- Routed the eight existing composition-change sites through that operation.
- Added local draft read/apply helpers.
- Kept backend selection outside the transactional draft.
- Kept `intakeTail`, URL release, `onSend`, `tick`, textarea focus, caret, and cursor
  ownership in the composer.
- Applies and releases the after-send state before awaiting `onSend`.
- Calls restoration only for a false `onSend` result.
- Public props and production markup were not changed.
- The file is now 911 lines; this prerequisite was not a line-count ticket.

### `web/tests/conversation-draft-transaction.test.ts`

- Added focused tests at the locked pure interface.
- Covers exact carried run values including `backendKey`.
- Covers trimmed content, image ordering, empty text, snapshot isolation, all delivery
  modes, successful restoration, image ids, and every refusal-denial condition.
- The file is 200 lines.

### `web/tests/conversation-pane.test.mjs`

- Made only the generated test host's backend/running facts reactive.
- Added a rendered Hermes steer scenario after fixed-index send assertions.
- Proves callback modes, visible retained picks, their next non-steer delivery, and their
  subsequent clearing.
- Existing assertions were not weakened or renumbered.

## TDD evidence

### Pure interface red

Command:

```sh
npm --prefix web run test:vitest -- tests/conversation-draft-transaction.test.ts
```

Result: failed because
`../src/components/conversation/composer/draftTransaction` did not exist. Runtime import
and typecheck both reported the missing module.

### Pure interface green

Same command after adding the private module.

Result:

```text
Test Files  2 passed (2)
Tests       17 passed (17)
Type Errors no errors
```

### Rendered interface red

The first harness run exposed that the preceding command menu covered the picker. The
test setup was corrected by clearing that existing draft/menu; production was untouched.

The next harness run reached the intended contract failure:

```text
assert face(model) == "Sonnet"
AssertionError: Opus
```

This proved the old unconditional pick clearing after an accepted steer.

### Rendered interface green

Command:

```sh
node web/tests/conversation-pane.test.mjs
```

Result:

```text
conversation-pane.test.mjs: all assertions passed
```

### Settled focused rerun

Command:

```sh
npm --prefix web run test:vitest -- tests/conversation-draft-transaction.test.ts \
  && node web/tests/conversation-pane.test.mjs
```

Result: 17 focused assertions passed, typecheck reported no errors, and the rendered
composer harness passed.

### Svelte diagnostics

Command:

```sh
npm --prefix web run check
```

Result:

```text
svelte-check found 0 errors and 0 warnings
```

### Scope and whitespace

Command:

```sh
git diff --check -- . ':(exclude,glob)web/dist/assets/*.js'
```

Result: passed with no output.

Source audit:

- `draftTransaction.ts` exports exactly the locked three types and two functions.
- `compositionRevision += 1` appears exactly once, inside `recordDraftChange`.
- The composer prop block and production DOM have no diff.
- All implementation paths are ticket-allowed.

## Review

### Standards

No unresolved violation found. The pure rule sits in the conversation domain, imports
existing contracts, performs no framework or browser side effect, and replaces rather
than layers over the old transaction.

### Spec

No unresolved violation found. Begin-send, refusal restoration, revision ownership,
resource ownership, sticky backend behavior, textarea identity, and successful-steer
retention match the locked contract.

## Gates intentionally left for root

Per dispatch, the final broad gates were not run here:

```sh
npm --prefix web run build
.venv/bin/pytest -q \
  tests/e2e/test_dev_conversation_pane.py \
  tests/e2e/test_conversation_three_states.py
```

`web/dist` was therefore not rebuilt in this implementation turn. `./verify` was not
run, as required.

## Unresolved issues

None in the implemented source or focused test scope. Final build, generated bundle, and
focused e2e confirmation remain for root integration.
