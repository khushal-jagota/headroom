# Independent revised-plan review

## Decision

**REVISE**

## Findings by severity

### Major

1. **The revised scope does not authorize or plan removal of the superseded policy
   file.**

   The committed first implementation still contains
   `web/src/components/conversation/composer/runSelection.ts`. The revised ticket moves
   its contracts to `web/src/lib/conversation/runControls/contracts.ts` and its policy
   to `web/src/lib/conversation/runControls/logic/runSelection.ts`, but the old path was
   removed from **Allowed production and test files** and the plan only says to add the
   two new files. It never says to delete the old module.

   An implementer following the file ceiling cannot remove the old file. Leaving it in
   the final tree would retain a second declaration of all six contract shapes and a
   second policy location, contradicting both the ticket's “one private semantic
   package” boundary and `PRINCIPLES.md`:

   - contracts/types have one domain contract source and are not redeclared locally;
   - pure rules live in the domain's `logic/` layer;
   - obsolete structure does not remain beside the replacement.

   **Required revision:** add
   `web/src/components/conversation/composer/runSelection.ts` to the allowed file list
   explicitly for removal, and add a migration step that updates every parent, renderer,
   and test import before deleting it. The final evidence should include a repository
   search proving that no import or declaration remains at the legacy path.

### Minor

2. **The line-count evidence instruction still refers to three production files.**

   The revised plan now has four production files with enforced limits:
   `ConversationComposer.svelte`, `contracts.ts`, logic `runSelection.ts`, and
   `ComposerRunControls.svelte`. Its closing instruction still says, “record `wc -l`
   for the three production files.”

   **Required revision:** change this to four files and name them, so the parent limit
   and all three new-file limits are evidenced once and consistently.

## Passed review checks

- The new `conversation/runControls/contracts.ts` plus `logic/runSelection.ts` layout
  aligns with the domain/layer, contracts-first, and pure-logic rules in
  `PRINCIPLES.md`.
- The locked surface is coherent: `contracts.ts` exports exactly the six shared types;
  logic exports exactly the two pure functions; the logic and renderer import shared
  shapes from the contract rather than redeclaring them.
- The plan gives tests the correct split import seam: types from `contracts.ts`,
  functions from logic.
- The disposition precisely closes the missing integration proof: it requires non-null
  model and effort picks, observes ordinary-send clearing, refuses the held send, and
  verifies exact restoration through public DOM and `onSend` behavior without exposing
  Svelte state.
- The disposition also requires fresh, complete output for the affected gates and
  corrects the prior contradictory composer-size evidence. Those requirements need not
  be duplicated in the plan to be binding.
- Production and test changes otherwise remain within the stated ticket boundary, and
  the acceptance command set remains unchanged.

## Recheck disposition

- **Major finding resolved.** The ticket now authorizes the legacy
  `components/conversation/composer/runSelection.ts` path for removal only. Both the
  ticket and plan require updating all parent, renderer, and test imports, deleting that
  file, and proving no legacy import or local contract declaration remains.
- **Minor finding resolved.** The plan now requires `wc -l` evidence for all four named
  production files: `ConversationComposer.svelte`, `contracts.ts`, logic
  `runSelection.ts`, and `ComposerRunControls.svelte`.

**Final verdict: APPROVE.**
