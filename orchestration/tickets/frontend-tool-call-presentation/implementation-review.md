# Implementation review: Frontend tool-call presentation

## Verdict

**FINDINGS.**

## Standards

### [Standards-1] Keep the rebuilt production bundle

`docs/frontend.md:344-346` says that build artifacts are checked in for the local server,
that FastAPI serves `web/dist`, and that it must be rebuilt after Svelte changes. This
implementation changes three Svelte files, and the recorded successful build generated
`dist/assets/index-DSasre04.js`. The current checked-in
`web/dist/index.html` still names the old `index-BGysQfHU.js`, and
`implementation-report.md` explicitly says the generated files were restored because
they were considered outside the ticket.

That leaves the production bundle served by FastAPI out of step with the reviewed source.
Run the required production build again and retain its `web/dist` changes in the ticket,
including the new hashed asset, removal of the superseded asset, and updated
`web/dist/index.html`. Record the settled build and `git diff --check` evidence after
keeping those files.

All other Standards checks pass:

- the 323-line tool-call presentation module has its own semantic folder;
- its implementation is pure and its runtime dependency points only to the
  dependency-free detail formatter;
- the import from `transcript.ts` is type-only, and `transcript.ts` imports no
  presentation module, so there is no runtime cycle;
- the module provides depth through one interface and leaves no duplicated presentation
  policy in its callers;
- no unsupported port, adapter, compatibility barrel, or speculative interface was
  added.

## Spec

**PASS.**

- `toolCallPresentation/index.ts` exports exactly `ToolCallPresentation` and
  `presentToolCall`; `conversationDetail.ts` exports exactly
  `readableConversationDetail`.
- Classification, the sole SVG-path map, shell unwrapping, allowed-subject parsing,
  Hermes prefix cleanup, wording, truncation, detail selection, and disclosure policy
  are private implementation.
- `ToolCallRow.svelte` makes one `presentToolCall(row)` call and has no presentation
  helper coordination or `as never` bridge. `restLine.ts` uses the same result.
- Both permission-detail renderers use `readableConversationDetail`.
- `stepIcons.ts` is deleted, the old transcript exports are gone, and searches find no
  stale production/test imports.
- Tests cover every protocol icon path, neutral fallback, backend normalization,
  title/summary rules, shell wrappers, subject allow-listing, 80-character truncation,
  nullish progress precedence, detail formatting, `canExpand`, both production callers,
  and observable running/completed/failed marks.
- The nested legacy-transpiler mapping is implemented and passed.
- `transcript.ts` is 934 lines; the new production files are 323 and 18 lines.
- The recorded focused suites, legacy harnesses, type check, build, 366-test frontend
  suite, and whitespace audit all passed. Repository-wide `./verify` is correctly
  reserved by the reviewed program plan.

Summary: **Standards — 1 finding; Spec — 0 findings.**
