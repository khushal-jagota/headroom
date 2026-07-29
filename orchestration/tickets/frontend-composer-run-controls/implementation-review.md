# Independent implementation review

## Decision

**REVISE**

The production implementation matches the locked architecture and behavior on
inspection, but the committed test/evidence package does not yet meet the approved plan
and repository verification rules.

## Standards

### Major

1. **The verification artifact contains summaries rather than the required full
   command output.**

   `PRINCIPLES.md` under **Test discipline** and `AGENTS.md` under
   **Verification** require a fresh check with its full output shown. The command blocks
   in `implementation-evidence.md` retain selected result lines, but omit portions of the
   Vitest, npm, Svelte-check, build, and pytest output. This leaves the evidence less
   auditable than the repository standard requires.

   **Required revision:** capture and retain the complete output of each named acceptance
   command against the final revision. Do not rerun checks merely to replace evidence if
   the original complete logs still exist; otherwise run the settled gate once and
   preserve its full output.

### Minor

2. **The recorded composer size contradicts itself.**

   `implementation-evidence.md` first records
   `ConversationComposer.svelte` as 755 lines, then says it dropped to 752. The committed
   file is 755 lines. The actual size is within the 760-line contract, but the narrative
   must say 755 so ticket memory remains exact.

### Passed standards checks

- The generic `PRINCIPLES.md` preference for separate logic/contracts locations is
  overridden here by the ticket's explicit locked owner decision: the policy and its
  exact exported types must live together at
  `components/conversation/composer/runSelection.ts`. This is not a violation for this
  ticket.
- The policy is framework-free and side-effect-free.
- No additional Fowler smell warrants a finding. The resolved view and thin renderer
  form the one semantic subsystem required by the contract rather than speculative
  indirection.

## Spec

### Major

3. **The planned public refusal-restoration proof for run picks is missing.**

   `implementation-plan.md`, **Fragment renderer and parent integration**, explicitly
   requires the existing rendered `ConversationComposer` seam to prove that “refusal
   restores” model and effort picks. The diff to
   `web/tests/conversation-pane.test.mjs` adds the idle/running direct-child-order checks,
   but its refusal journey still reaches refusal with no non-null model or effort pick.
   The pure policy tests cannot prove the changed parent mapping between
   `ComposerRunSelection` and `ComposerDraft`.

   This is the load-bearing integration changed by the ticket:

   - `currentComposerDraft()` now reads model and effort from the single selection;
   - `applyComposerDraft()` must restore those two fields while retaining backend and
     delivery mode;
   - refusal must restore that draft without creating an extra user revision.

   The implementation looks correct on inspection, but the approved public seam does not
   demonstrate it.

   **Required revision:** extend the existing browser host journey to make non-null model
   and effort picks, hold an ordinary send long enough to observe their after-send
   clearing, refuse it, and assert that the exact text and both picker values return.
   Keep the assertion at the public component/DOM and `onSend` seam; do not expose local
   Svelte state.

### Passed spec checks

- `runSelection.ts` exports exactly the six locked types and two locked functions, and
  nothing else.
- Resolution is immutable. It normalizes the explicit model first, derives effort
  availability from the model actually showing, then normalizes the explicit effort.
- Existing/preselected values absent from a catalog remain visible; explicit empty
  model effort lists remain authoritative.
- Carried backend/model/effort values, actual-backend delivery options, effective mode,
  and submit projection match the lock.
- The parent owns exactly one `ComposerRunSelection`. Catalog normalization records no
  draft revision; genuine backend/model/effort changes record once; no-op and delivery
  intents record none. Model and effort choices restore textarea focus.
- Draft restoration replaces only model and effort, retaining selected backend and
  delivery mode. Send and stop remain parent-owned.
- `ComposerRunControls.svelte` takes exactly `{ view, intents }`, owns no selection, emits
  no wrapper, and preserves model → effort → delivery → submit order and existing
  selectors/copy.
- The structural harness keeps the exact top-level component inventory and separately
  compiles the nested renderer. Its browser assertions prove the idle and running direct
  child order.
- File sizes pass: composer 755 lines, policy 334, renderer 83.
- The committed generated bundle is present, and the ticket did not run the
  program-reserved `./verify`.

## Review checks run

Against commit `29db07b83dba28418807843cacd9e6ecdf020393`:

```text
npm --prefix web run test:vitest -- tests/conversation-run-selection.test.ts
  2 files passed; 30 tests passed; no type errors

node web/tests/conversation-pane.test.mjs
  conversation-pane.test.mjs: all assertions passed

npm --prefix web run check
  svelte-check found 0 errors and 0 warnings

git diff --check 0a9dfef5...29db07b8 -- . ':(exclude,glob)web/dist/assets/*.js'
  no output
```

Approval requires resolving findings 1–3 and rerunning only the checks affected by
those revisions.
