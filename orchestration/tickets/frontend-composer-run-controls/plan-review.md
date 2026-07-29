# Independent plan review

## Decision

**APPROVE**

## Findings by severity

- **Critical:** none.
- **Major:** none.
- **Minor:** none.

## Review basis

The implementation plan covers the complete locked behavior:

- the resolver owns backend/catalog/default selection, normalization, carried run
  values, picker presentation, delivery, and submit projection;
- the pure intent function implements every locked transition without side effects;
- the renderer takes exactly `{ view, intents }`, preserves the literal control markup
  and order, and emits no wrapper;
- the parent retains one controlled selection, the draft/send transaction, focus,
  revision accounting, restoration, and send/stop effects;
- normalization is field-compared before assignment and explicitly excluded from draft
  revision accounting;
- restored drafts replace only model and effort while preserving backend and delivery;
- focused policy tests, structural compilation, direct-child DOM assertions, public
  browser behavior, and the ticket's existing Playwright gates cover the appropriate
  seams without reaching into component internals.

The planned files stay within the ticket's allow-list. The stated production-file size
targets satisfy the hard limits, the exact acceptance commands are retained, `web/dist`
is rebuilt, and the plan correctly excludes the program-reserved `./verify` run.

## Implementation-review checks

These are not plan revisions, but they are the load-bearing points to verify in the
resulting diff:

1. `runSelection.ts` exports exactly the contract-lock surface and stays framework-free.
2. Catalog reconciliation uses the normalized model when resolving effort in the same
   view, and never increments `compositionRevision`.
3. Genuine backend/model/effort intents increment the draft revision once; no-op and
   delivery intents do not.
4. The nested renderer is compiled separately without changing the exact top-level
   component inventory assertion, and its emitted nodes remain direct children of
   `.chat-foot`.
5. The final line counts and every named gate are recorded against the completed tree.
