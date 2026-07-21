# ACP-05 permission diff implementation plan

## Boundary

Change only the mounted permission prompt and its existing browser-component proof. The ACP request
already carries typed `toolCall.content`, so this ticket needs no transport, state, or backend work.

## UI intent

- **Visual thesis:** keep the existing restrained Panels permission inset and place the supplied file
  changes between its compact title and its unchanged approval controls.
- **Content plan:** title, zero or more source-ordered diff views, existing options, existing status.
- **Interaction thesis:** no new interaction; the diff is immediately readable because the decision
  depends on it, while approval submission behaves exactly as it does now.

## TDD slices

1. Extend the real mounted-component browser seam with a no-diff control and a mixed-content
   permission containing two ordered diff blocks. Prove no arbitrary non-diff content appears; prove
   file order, old/new lines, markers, line numbers, and accessible table labels; re-prove the exact
   option kinds, disabled/submitting behavior, and callback IDs.
2. Import the existing `DiffView.svelte` in `PermissionPrompt.svelte` and render only content entries
   whose discriminant is `diff`, in their supplied order, between title and options. Add no wrapper or
   style for requests without a diff.
3. Run the focused mounted component test, Svelte diagnostics, and the production frontend build.
   Record exact outputs in `implementation-report.md`; do not run `./verify`.
