# ACP-05 permission diff implementation review

## Finding

### P1 — The no-diff proof does not establish the required byte-for-byte markup preservation

The contract requires requests with no content or only non-diff content to remain byte-for-byte
equivalent to the pre-change compact permission UI (`contract.md:15-16`). The test records
`permission.inner_html()` only after the changed component is mounted and compares that value with a
second state rendered by the same changed component (`web/tests/acp-browser-components.test.mjs:406-418`).
It therefore proves only that no content and non-diff content match each other after this change.

That distinction is observable here: the new unkeyed `{#each diffs as diff}` block at
`web/src/components/acp/PermissionPrompt.svelte:29-31` compiles an empty block anchor between the title
and options even when `diffs` is empty. A read-only Svelte compile emitted the section template with an
`<!>` anchor at that position. The current assertion accepts that changed markup because it captures the
post-change value as its own expected result.

Add a pre-change exact markup fixture/snapshot (or an equivalent serialization assertion that excludes
the new empty anchor), and adjust the component if that assertion exposes a markup change. Keep the
existing no-content versus non-diff comparison as the strict arbitrary-content filtering proof.

## Confirmed compliant behavior

- Filtering is strict: only entries whose discriminant is exactly `type === "diff"` render.
- Array filtering preserves supplied source order, and every retained block uses the existing
  `DiffView.svelte` with its exact path/old/new values.
- Non-diff content is not displayed.
- Option IDs, kinds, labels, disabled/submitting state, accessibility state, and
  `onSelect(requestId, optionId)` behavior are unchanged and covered by the mounted test.
- No new component style or approval interaction was introduced; the Panels treatment remains restrained.
- `node web/tests/acp-browser-components.test.mjs` passed during this review.

## Verdict

**NOT READY** — one required no-diff byte/markup regression proof is incomplete, and the compiled empty
block anchor indicates the literal byte-for-byte contract may currently be violated.

## Correction disposition

The sole finding is resolved. `PermissionPrompt.svelte` no longer places a diff loop in the section
template; a read-only Svelte compile shows the title followed directly by the options element with no
empty `<!>`/`<!---->` anchor. The focused browser test now compares no-content markup with an exact
serialized pre-change fixture (normalizing only the generated Svelte scope token), then proves
only-non-diff content remains literally identical. Ordered supplied diffs still mount the existing
`DiffView`, cleanup unmounts only those mounted diff instances, and the unchanged option IDs, kinds,
submitting/disabled state, and callback arguments continue to pass.

Focused evidence: `node web/tests/acp-browser-components.test.mjs` passed.

## Final verdict

**READY** — the correction satisfies the previously open no-diff byte/markup requirement without
altering the accepted diff ordering or permission-option behavior.
