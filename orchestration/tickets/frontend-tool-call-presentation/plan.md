# Frontend tool-call presentation implementation plan

## Current state and locked scope

Implement the locked in-process seam without changing product behavior:

- `web/src/lib/conversation/transcript.ts` is 1,287 lines. Its tool-presentation
  implementation occupies lines 903–1,254: tool-kind classification, readable
  detail, line construction, backend-specific parsing, truncation, and duplicate
  suppression.
- `web/src/lib/conversation/stepIcons.ts` is a separate 30-line icon lookup used
  only by `ToolCallRow.svelte`.
- `ToolCallRow.svelte` is 128 lines and currently coordinates five helpers plus
  the `as never` type bridge.
- `restLine.ts` is 229 lines and separately calls `toolCallLine`.
- `PermissionAskCard.svelte` and `ConversationTranscript.svelte` also depend on
  the generic structured-detail formatter.

The external interface is exactly the one in `contract-lock.md`:
`presentToolCall(row): ToolCallPresentation`, plus the separate
`readableConversationDetail(detail)` interface. Do not export any implementation
helper, constant, glyph type, or icon lookup. Keep `ToolCallRow` in
`transcript.ts`; this ticket does not move transcript contracts or thread layout.

## Dependency direction

Keep the new modules pure and the runtime graph one-way:

```text
wire/feed
   ↓
transcript ────────────────┐
   ↑ type-only             │ row consumers
toolCallPresentation       │
   ↓ runtime               │
conversationDetail         │
                           ↓
ToolCallRow / restLine / PermissionAskCard / ConversationTranscript
```

- `conversationDetail.ts` is a dependency-free leaf.
- `toolCallPresentation/index.ts` imports `ToolCallRow` with `import type` from
  `../transcript.ts` and imports `readableConversationDetail` at runtime. The
  non-trivial module gets its own semantic folder as required by `PRINCIPLES.md`;
  its callers import from the folder interface.
- `transcript.ts` must not import either presentation module. This prevents a
  runtime cycle and keeps record-to-row projection independent of rendering.
- `ToolCallRow.svelte` and `restLine.ts` import only `presentToolCall` for tool
  presentation. Permission-ask renderers import only
  `readableConversationDetail`.
- There is no port or adapter. Every dependency is in-process, and the sole icon
  mapping remains private implementation rather than a hypothetical adapter
  seam.

## Implementation sequence

1. **Replace helper-shaped tests with interface-shaped RED tests.**
   - Rewrite `conversation-tool-presentation.test.ts` to import only
     `presentToolCall` from the new module (and the existing `ToolCallRow` type
     for fixture construction).
   - Add `conversation-detail.test.ts` for
     `readableConversationDetail`: structured object/array JSON, prose,
     malformed JSON, empty text, `null`, and `undefined`.
   - Move the unrelated `promptLabelFor` examples into
     `conversation-transcript.test.ts`; do not leave transcript behavior in the
     tool-presentation suite.
   - Run the three named Vitest suites and record the expected missing-module
     failure before implementation.

2. **Create the dependency-free detail module.**
   - Add `conversationDetail.ts` with the exact locked function and move the
     current behavior from `transcript.ts:954–970` unchanged.
   - Update `PermissionAskCard.svelte` and `ConversationTranscript.svelte` to
     import `readableConversationDetail` from this module.
   - Preserve the exact rule: trim only to decide emptiness/JSON eligibility;
     valid JSON is pretty-printed with two spaces, while prose and malformed JSON
     are returned exactly as received.

3. **Create the deep tool-call presentation module.**
   - Add `toolCallPresentation/index.ts` with only the locked type and function
     exported. Keep the module in its own folder rather than adding another
     non-trivial file to the flat conversation directory.
   - Move the existing tool glyph vocabulary/classification from
     `transcript.ts:903–952`, line-building and backend-specific parsing from
     `transcript.ts:972–1,254`, and the SVG path table from `stepIcons.ts`.
   - Keep glyph classification, icon lookup, shell unquoting, Claude subject
     allow-listing, Hermes prefix removal, word comparison, summary shortening,
     and line/detail comparison private.
   - Implement `presentToolCall` as the one composition point:
     - derive title and summary with the existing rules;
     - select icon paths from the same classified tool kind;
     - derive detail from exactly `row.progress ?? row.detail` through
       `readableConversationDetail`;
     - set `canExpand` only when that formatted detail is non-null and is not
       already wholly represented by title and summary.
   - Preserve the existing path arrays, subject-name order, shell regular
     expressions, strings, and 80-character ellipsis behavior byte-for-byte.

4. **Move both production callers onto the single interface.**
   - In `ToolCallRow.svelte`, replace the five helper calls with one derived
     `presentToolCall(row)` result. Render its `iconPaths`, `title`, `summary`,
     `detail`, and `canExpand`; leave status marks, open state, DOM attributes,
     output cap, CSS, and click behavior unchanged. Remove `as never`.
   - In `restLine.ts`, replace `toolCallLine(row)` with
     `presentToolCall(row)` and join the returned title/summary exactly as today.
     Do not add a second lightweight interface for the rest line.

5. **Remove the old shallow interfaces.**
   - Delete the moved implementation and exports from `transcript.ts`, retaining
     `ToolCallRow`, transcript projection, thread layout, turn timing, plan
     wording, prompt labels, row sentences, and live-ask behavior.
   - Delete `stepIcons.ts`.
   - Search production and tests for `stepIcons`, `stepIconPaths`,
     `toolGlyphKind`, `toolCallLine`, `commandWithoutShellInvocation`,
     `lineShowsWholeDetail`, and `readableDetail`. No old import/export or
     `as never` bridge may remain.

6. **Update legacy test wiring without replacing behavior coverage.**
   - In `conversation-rest-line.test.mjs`, replace the `${name}.ts` convention
     with an explicit source/output table so the folder module is real rather
     than flattened by accident:
     - `wire.ts` → `wire.mjs`
     - `feed.ts` → `feed.mjs`
     - `transcript.ts` → `transcript.mjs`
     - `conversationDetail.ts` → `conversationDetail.mjs`
     - `toolCallPresentation/index.ts` → `toolCallPresentation.mjs`
     - `restLine.ts` → `restLine.mjs`
   - Make its import rewrite explicit for both ordinary sibling specifiers and
     the folder module's `../transcript` / `../conversationDetail` specifiers,
     all targeting those temporary `.mjs` filenames. Keep its existing
     assertion that the newest Bash call reads
     `Ran command · ls -la /tmp`.
   - In `conversation-pane.test.mjs`, remove the source-shape assertion requiring
     `ToolCallRow.svelte` to import `stepIcons` and rewrite its adjacent comment
     as behavior rather than file placement. Do not replace it with another
     import assertion.
   - Retain the existing rendered assertions for collapsed output, one-line
     summary, Claude title/subject presentation, and expandable output.
   - Add observable SSR assertions for running, completed, and failed tool rows:
     retain the `data-conversation-tool-status` value and the exact accessible
     mark (`Running`, `Completed`, or `Failed`) for each. These protect behavior
     that stays in `ToolCallRow.svelte` while its presentation dependency changes.

## Acceptance-test coverage

Test `presentToolCall` only through its locked result:

- protocol-native kinds retain their exact icon paths;
- unknown kinds return the neutral three-dot paths;
- Claude Bash/Read/Write arguments select only allowed subject fields;
- unknown argument fields do not invent a subject;
- Codex bash/zsh/sh, cmd, and PowerShell wrappers unwrap exactly as before;
- Hermes descriptive prefixes do not repeat the tool kind;
- summaries stay on one line, truncate to 80 characters with the existing
  ellipsis, and are not suppressed by mere substring overlap;
- `progress` wins over `detail` by nullish precedence;
- formatted one-line detail already represented by the line gives
  `canExpand: false`; multiline/new detail gives `canExpand: true`;
- title, summary, detail, and icon paths are asserted together for representative
  rows so classification and wording cannot drift apart.

The generic detail suite owns JSON/prose formatting. Transcript coverage owns
`promptLabelFor`. Existing rest-line and rendered-pane coverage proves both
production callers still expose the same visible behavior.

## Size targets and audits

- Target `toolCallPresentation/index.ts` at roughly 340–430 lines including its
  documentation and private icon table; hard ceiling 600 lines.
- Target `conversationDetail.ts` at 20–40 lines.
- Reduce `transcript.ts` from 1,287 lines to below the ticket's 1,000-line
  acceptance ceiling (expected roughly 930–950 lines).
- Add no production file above 600 lines.
- Do not pad or compress files to hit a count; semantic locality and readable
  formatting govern the final size.

After implementation, record `wc -l` for the three production modules and use
`rg` to prove the deleted interfaces, filename, and type bridge are absent.

## Focused gates

Use narrow RED/GREEN runs while implementing, then run this settled sequence
once:

```sh
npm --prefix web run test:vitest -- \
  tests/conversation-detail.test.ts \
  tests/conversation-tool-presentation.test.ts \
  tests/conversation-transcript.test.ts
node web/tests/conversation-rest-line.test.mjs
node web/tests/conversation-pane.test.mjs
npm --prefix web run check
npm --prefix web run build
npm --prefix web test
git diff --check
```

Do not run repository-wide `./verify`; the architecture program reserves one
canonical run for its final settled tree.

## Risks and controls

- **Runtime cycle:** importing the row type as a value would make the new module
  and transcript depend on each other. Use `import type` and prohibit any
  presentation import from `transcript.ts`.
- **Subtle presentation drift:** shell regexes, subject priority, title fallback,
  word-based duplicate detection, SVG paths, and ellipsis logic are exact
  behavior. Move them before simplifying names or formatting, and test through
  the locked result.
- **Detail-precedence drift:** `row.progress ?? row.detail` is nullish, not truthy.
  Preserve empty-string behavior and calculate `canExpand` from the formatted
  detail, as the current renderer does.
- **Legacy harness resolution:** `conversation-rest-line.test.mjs` manually
  transpiles a closed module list. Its old `${name}.ts` convention cannot read a
  folder interface. Use the explicit source/output table above and rewrite both
  sibling imports and the folder module's parent imports, or it will fail
  independently of production correctness.
- **Coverage loss during replacement:** move `promptLabelFor` coverage before
  deleting the old mixed suite assertions, and retain the pane/rest-line
  observable checks rather than replacing them with source inspection.
- **Scope creep:** do not move `ToolCallRow`, split thread layout, restructure
  renderers, alter CSS, or change backend/wire contracts in this ticket.
