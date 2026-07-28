# Implementation report

## Outcome

The tool-call rendering policy now lives behind the locked
`presentToolCall(row): ToolCallPresentation` interface in the semantic
`toolCallPresentation/` module. Both production callers consume that one result.
Generic conversation-detail formatting is separately owned by
`readableConversationDetail`.

There are no compatibility exports. `stepIcons.ts` and the old transcript exports
were removed, and `transcript.ts` has no runtime dependency on either presentation
module.

## Production changes

- Added `web/src/lib/conversation/toolCallPresentation/index.ts`.
  - Its only exports are `ToolCallPresentation` and `presentToolCall`.
  - Tool classification, exact SVG paths, shell-wrapper removal, Claude subject
    selection, Hermes prefix removal, wording, truncation, duplicate detection, and
    disclosure policy are private.
- Added `web/src/lib/conversation/conversationDetail.ts`.
  - Its only export is `readableConversationDetail`.
- Changed `ToolCallRow.svelte` to derive one presentation result and render its five
  fields. Status marks, disclosure state, DOM attributes, and CSS are unchanged.
- Changed `restLine.ts` to use the same presentation result.
- Changed the two permission-detail renderers to use
  `readableConversationDetail`.
- Removed the presentation implementation from `transcript.ts` and deleted
  `stepIcons.ts`.

## Test changes

- Replaced helper-shaped tool tests with interface-shaped
  `presentToolCall` tests.
- Added exact path coverage for every protocol-native glyph, the neutral fallback,
  and backend-name normalization.
- Added `conversation-detail.test.ts`.
- Moved `promptLabelFor` coverage to the transcript suite.
- Updated the rest-line transpilation harness for the semantic folder interface.
- Replaced the pane's deleted source-shape check with rendered running, completed,
  and failed status assertions.

## TDD record

The RED command was:

```sh
npm --prefix web run test:vitest -- \
  tests/conversation-detail.test.ts \
  tests/conversation-tool-presentation.test.ts \
  tests/conversation-transcript.test.ts
```

It exited 1 before either production module existed. Vitest reported four failed
suites and two passed suites: runtime resolution and type-check resolution both
failed for `../src/lib/conversation/conversationDetail` and
`../src/lib/conversation/toolCallPresentation`. The 32 pre-existing transcript
tests passed and Vitest reported no unrelated type errors.

The first GREEN attempt exposed a test-fixture mistake: `TaskUpdate` is classified
as an edit by the existing `"update"` hint, so it was not a valid neutral-tool
fixture. The fixture was corrected to `AskUserQuestion`; production behavior was
not changed to satisfy it.

The settled focused run passed 65 tests across the runtime and type-check projects.
Full final gate output is in `verification-evidence.md`.

## Independent review

The implementation review found two issues:

1. exact path coverage initially covered only read, execute, and unknown glyphs;
2. four PID-specific `.c2-*` files remained after an earlier browser-harness
   environment failure.

Every known glyph path is now asserted through `presentToolCall`, backend aliases
`WebFetch` and `NotebookEdit` are covered, and all four generated files were
removed. The reviewer's focused re-review reported no unresolved findings.

## Structural audit

```text
934  web/src/lib/conversation/transcript.ts
323  web/src/lib/conversation/toolCallPresentation/index.ts
 18  web/src/lib/conversation/conversationDetail.ts
```

- `transcript.ts` is below the 1,000-line ceiling.
- No production module added by this ticket exceeds 600 lines.
- The presentation module exports exactly the locked type and function.
- The detail module exports exactly the locked function.
- No `stepIcons`/`stepIconPaths`, old helper import or export, conversation
  `as never` bridge, runtime presentation import in `transcript.ts`, or `.c2-*`
  artifact remains.

## Environment and closeout

The legacy pane harness initially could not start because this new worktree did not
have `.venv/bin/python`. The worktree's pinned Python dependencies and editable
package were installed in its ignored `.venv`; the harness then passed.

`npm run build` regenerated the shipped `web/dist` bundle after the source changes.
The updated tracked `index.html`, replacement hashed JavaScript asset, and removal of
the superseded hashed JavaScript asset are retained in the ticket.

The reviewed focused gate sequence passed. Repository-wide `./verify` was
intentionally reserved for the architecture program. No commit was created.
