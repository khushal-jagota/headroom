# Implementation review

## Standards

**Verdict: pass.**

No unresolved material standards finding.

The implementation creates one deep, framework-free transaction module inside the
Conversation component domain and imports the existing contract types rather than
redeclaration. Svelte state, `onSend`, focus/caret work, image intake, and URL release
remain in `ConversationComposer.svelte`. The old ad-hoc transaction was replaced rather
than retained beside the new seam, and production markup and public props have no diff.

`recordDraftChange` is the only direct composition-revision increment. The component is
still 911 lines, but that known size smell is explicitly outside this prerequisite
ticket's completion claim; no cosmetic child extraction was introduced to conceal it.

## Spec

**Verdict: pass.**

No unresolved material contract or scope finding.

The reviewed implementation matches the lock:

- the new module exports exactly the three locked types and two locked functions;
- begin-send snapshots the draft and exact carried run values, orders trimmed text before
  images, preserves the revision, retains picks only for `steer`, and performs no effect;
- refusal restoration compares the full after-send draft plus image-intake occupancy,
  returns denial inputs unchanged, and reconstructs sent text, ordered data-preview
  images, explicit picks, revision, and image ids on success;
- the component waits for `intakeTail`, applies the after-send draft and releases its
  captured image resources before `onSend`, and invokes restoration only for `false`;
- backend selection remains outside the cleared draft;
- the rendered Hermes case proves an accepted steer retains model and effort picks and
  that the following `run_when_free` send carries and clears them.

The generated asset change is complete: the previous JavaScript bundle is deleted,
`web/dist/index.html` points to the new present bundle, and the existing CSS asset is
unchanged.

## Test evidence

Independently run during this review:

```text
npm --prefix web run test:vitest -- tests/conversation-draft-transaction.test.ts
Test Files  2 passed (2)
Tests       17 passed (17)
Type Errors no errors

node web/tests/conversation-pane.test.mjs
conversation-pane.test.mjs: all assertions passed

npm --prefix web run check
svelte-check found 0 errors and 0 warnings

git diff --check -- . ':(exclude,glob)web/dist/assets/*.js'
passed with no output
```

The canonical `./verify` was not run, as reserved by the frontend architecture program.

**Summary:** Standards 0 findings; Spec 0 findings. The implementation is ready for
integration.
