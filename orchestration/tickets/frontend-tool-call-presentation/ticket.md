# Frontend tool-call presentation

## Outcome

Turn the tool-call rendering policy currently embedded in the 1,287-line
`web/src/lib/conversation/transcript.ts` into one deep in-process module.

The new module must give both production callers one presentation result instead of
making them coordinate backend-kind classification, icon selection, shell unwrapping,
subject extraction, wording, truncation, detail formatting, and disclosure policy.
This is a behaviour-preserving architecture ticket.

## Why this seam

`ToolCallRow.svelte` currently crosses five shallow interfaces and bridges the icon
mapping with `as never`. `restLine.ts` separately reconstructs the title/summary line.
By the deletion test, removing the proposed module would spread all of those judgments
back across both callers. The module therefore earns its seam and gives callers leverage
while concentrating presentation knowledge for locality.

All dependencies are in-process. No port or adapter is justified.

## Contract files

- Existing row contract: `web/src/lib/conversation/transcript.ts`
  (`ToolCallRow` remains the input contract in this ticket).
- New presentation contract:
  `web/src/lib/conversation/toolCallPresentation/index.ts`.
- Shared detail formatting:
  `web/src/lib/conversation/conversationDetail.ts`.

The exact locked interface is in `contract-lock.md`. The implementation may not add
extra exported helpers to make the move easier.

## Required work

1. Add the non-trivial `toolCallPresentation/` module folder and move behind
   `toolCallPresentation/index.ts`'s single function:
   - backend tool-kind normalization;
   - the sole SVG path mapping, including the neutral fallback;
   - Claude argument parsing and subject allow-listing;
   - Codex shell-wrapper removal;
   - Hermes kind-prefix cleanup;
   - title and summary choice;
   - the 80-character summary limit;
   - duplicate suppression;
   - readable detail and whether opening the row would reveal anything new.
2. Delete `stepIcons.ts`; the icon mapping is an implementation detail of the new
   module, not a second interface.
3. Move generic structured-detail formatting to `conversationDetail.ts` as
   `readableConversationDetail`. Update permission-ask callers to use that semantic
   module.
4. Change `ToolCallRow.svelte` to call `presentToolCall(row)` once. It must no longer
   know glyph kinds, icon lookup, line construction, detail formatting, or disclosure
   comparison, and the `as never` bridge must disappear.
5. Change `restLine.ts` to use the same presentation result for its tool-call sentence.
6. Remove the tool-presentation implementation and its old public helpers from
   `transcript.ts`. Keep unrelated transcript projection, thread layout, timing,
   prompt-label, and row-language behavior in place.
7. Replace the old helper-shaped tests with tests through `presentToolCall`. Keep
   structured-detail formatting tested through `readableConversationDetail`. Move the
   unrelated `promptLabelFor` assertion to transcript-owned coverage.
8. Remove the source-shape assertion that requires `ToolCallRow.svelte` to import
   `stepIcons`. Tests must assert observable presentation behavior, not the deleted
   implementation filename.

## Acceptance

- Claude, Codex, Hermes, protocol-native, and unknown tool rows retain their exact
  visible title and summary.
- The subject allow-list, shell unwrapping, one-line/80-character summary rule, and
  duplicate suppression remain exact.
- The presentation result carries the correct icon paths, formatted detail, and
  `canExpand` decision, including neutral fallback behavior.
- `ToolCallRow.svelte` retains running/completed/failed marks and expandable/collapsed
  rendering, with observable SSR assertions for all three status marks.
- `restLineFrom` uses the same title and summary as the full row.
- Generic permission details retain structured JSON formatting and prose behavior.
- `transcript.ts` is below 1,000 lines after the extraction.
- No production file added by this ticket exceeds 600 lines, and the non-trivial
  presentation module is represented by its own semantic folder rather than another
  entry in the flat conversation pile.
- No backend, HTTP, live-tail, CSS, route, or product behavior changes.

## Focused gates

Run after the ticket is complete:

```sh
npm --prefix web run test:vitest -- \
  tests/conversation-tool-presentation.test.ts \
  tests/conversation-transcript.test.ts
node web/tests/conversation-rest-line.test.mjs
node web/tests/conversation-pane.test.mjs
npm --prefix web run check
npm --prefix web run build
npm --prefix web test
git diff --check
```

The frontend architecture program reserves one canonical `./verify` for its final
settled tree; this ticket does not repeat it.

## Outside this ticket

- Thread-layout extraction from `transcript.ts`.
- Transcript contracts/package decomposition.
- Conversation viewport/scroll extraction.
- Composer, route, or CSS restructuring.
- Tailwind or any stack change.
