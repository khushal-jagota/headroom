# Plan review: Frontend tool-call presentation

## Verdict

**FINDINGS — do not implement until the two items below are resolved.**

The proposed seam is real and deep: `presentToolCall(row)` hides classification,
backend-specific parsing, wording, icon selection, detail formatting, and disclosure
policy behind one result used by two production callers. By the deletion test, removing
the module would spread those judgments back across `ToolCallRow.svelte` and
`restLine.ts`. The five result fields are all observable facts required by at least one
caller; the interface is neither needlessly large nor too small.

## Standards

### [Standards-1] Put the non-trivial presentation module in a layer folder

`PRINCIPLES.md:6-7` requires layers within a domain rather than a flat pile and says that
anything non-trivial gets a folder. The plan instead adds a roughly 340–430-line
`web/src/lib/conversation/toolCallPresentation.ts` beside the existing flat conversation
files. That reduces one file's size while preserving the structural problem this
architecture program is meant to remove.

The ticket and contract lock currently name the flat path, so the implementation agent
cannot repair this without violating its contract. Before implementation, the owner must
either:

- amend the ticket, contract lock, and plan to place the layer under a semantic folder,
  for example `web/src/lib/conversation/presentation/toolCall.ts` and
  `web/src/lib/conversation/presentation/detail.ts`, with no compatibility barrel; or
- record an explicit live-owner override of `PRINCIPLES.md`.

The first option preserves the proposed interface and dependency graph while giving the
presentation layer locality and room to grow.

Apart from placement, the Standards axis passes. The logic remains framework-free and
directly testable. All dependencies are in-process, so the plan correctly avoids a
hypothetical port or adapter. The runtime direction is one-way:
`tool-call presentation -> detail formatting`, with only a type import from the existing
row contract and no import back from `transcript.ts`.

## Spec

### [Spec-1] Add observable coverage for all three status marks

The ticket explicitly accepts preservation of running, completed, and failed marks in
`ToolCallRow.svelte`. The plan says to leave them unchanged and later claims the existing
rendered coverage proves caller behavior, but `conversation-pane.test.mjs:533-625` uses
different statuses without asserting the mark, its accessible label, or its status
attribute. There are no existing assertions for `Completed`, `Failed`, `Running`,
`acp-mark-ok`, `acp-mark-fail`, or `data-conversation-tool-status`.

Add rendered interface assertions for one row of each status. Assert the observable
status attribute and accessible mark (`Completed`, `Failed`, or `Running`), not source
text or import shape. This closes an acceptance gap exactly where
`ToolCallRow.svelte` is being rewired.

The rest of the Spec axis passes:

- `ToolCallPresentation` matches the locked five-field interface exactly.
- `readableConversationDetail` is a justified shared seam with three production
  consumers and remains a dependency-free leaf.
- `progress ?? detail`, exact icon paths, neutral fallback, shell unwrapping, subject
  allow-listing, 80-character truncation, and duplicate suppression are all named in
  interface-level coverage.
- `conversation-rest-line.test.mjs` has both required legacy-harness updates: the closed
  transpilation list and extension-rewrite expression.
- The obsolete `stepIcons` source-shape assertion is removed without replacing it with
  another implementation assertion.
- Scope stays within presentation logic and its direct callers; no transcript-contract,
  thread-layout, CSS, route, backend, HTTP, or live-tail change is proposed.
- The focused gates cover the new pure interfaces, both production callers, type
  checking, build output, and the settled frontend suite.
