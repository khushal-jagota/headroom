# Plan re-review 3: Frontend tool-call presentation

## Standards

**PASS.** The semantic folder remains the real module interface, and the legacy harness
does not require a flat compatibility file.

## Spec

**PASS.** The plan now replaces the harness's `${name}.ts` convention with an explicit
source/output table, including:

- `toolCallPresentation/index.ts` → `toolCallPresentation.mjs`;
- `conversationDetail.ts` → `conversationDetail.mjs`;
- the existing wire, feed, transcript, and rest-line modules.

It also explicitly requires rewriting the folder module's `../transcript` and
`../conversationDetail` imports, as well as ordinary sibling imports, to the corresponding
temporary `.mjs` files. This completely resolves the remaining legacy-transpiler finding.

There are no unresolved findings in the reviewed scope.
