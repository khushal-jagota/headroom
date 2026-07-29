# Baseline evidence

Branch base: `0a9dfef56514822db0306ddfe78bedbb4cbb46c8`

The untouched worktree passed:

```text
$ node web/tests/conversation-pane.test.mjs
conversation pane checks passed

$ npm --prefix web run check
svelte-check found 0 errors and 0 warnings

$ npm --prefix web run build
✓ 554 modules transformed.
✓ built
```

Baseline sizes:

```text
911 web/src/components/conversation/ConversationComposer.svelte
400 web/src/lib/conversation/composer.ts
```

The agreed public test seams are:

- the framework-free `resolveComposerRunControls` and
  `applyComposerRunSelectionIntent` policy interface;
- the existing rendered `ConversationComposer` interface and its stable DOM, focus,
  request, refusal-restoration, and delivery behavior;
- the existing Svelte type-check/build interface;
- the existing Playwright browser journeys named by the ticket.

No test reaches into Svelte local state or asserts a private implementation helper.
