# Plan re-review: Frontend tool-call presentation

## Verdict

**ONE REMAINING FINDING.**

## Standards

**PASS.** The non-trivial module now lives at
`web/src/lib/conversation/toolCallPresentation/index.ts`, with the folder itself as the
semantic module interface. This satisfies the folder requirement without adding a
compatibility barrel. The small shared detail formatter remains a justified
dependency-free leaf.

## Spec

The status-mark finding is **PASS**. The ticket now requires observable SSR coverage,
and the plan explicitly asserts each row's `data-conversation-tool-status` value and
accessible `Running`, `Completed`, or `Failed` mark. Those assertions cover behavior
rather than source shape.

### [Spec-1] Update the legacy transpiler for the nested module source path

The folder correction makes the current legacy-wiring instruction incomplete.
`conversation-rest-line.test.mjs:27-38` reads every entry from
`../src/lib/conversation/${name}.ts`, while the locked source is now
`toolCallPresentation/index.ts`. Merely adding `toolCallPresentation` to the existing
name list will try to read the deleted flat path
`web/src/lib/conversation/toolCallPresentation.ts`.

Amend the plan to require an explicit source-to-output mapping for the nested module,
for example:

- source `toolCallPresentation/index.ts`;
- temporary output `toolCallPresentation.mjs`;
- rewrite `./toolCallPresentation` to `./toolCallPresentation.mjs`.

Keep the existing flat mapping for `conversationDetail.ts`. With that mechanical
instruction added, both original findings and their test wiring are resolved.
