# Remove Atlas

Status: implemented, focused gate passed, independent review approved.

## Contract and acceptance

Atlas has no unique record. It reads board/review and embeds existing Ticket,
Sprint Item, Review, and conversation surfaces. Remove the alternative world and
its navigation entry; preserve the canonical screens and all their actions.
No backend contract changes. The existing hash router's unknown-route behavior
handles old Atlas links; do not add a compatibility subsystem.

Owned files: `web/src/routes/AtlasRoute.svelte`, `web/src/components/atlas/**`,
`web/src/lib/atlas/**`, `web/src/App.svelte`, `assets/app.css` (Atlas section only),
`web/package.json`, `web/package-lock.json`, `web/tests/atlas-*`, and Atlas prose in
`docs/frontend.md`. Comments naming Atlas in shared non-conversation files may be
corrected, but shared implementation and conversation CSS are protected.
No other agent may edit the package files until this ticket returns.

Delete three and @types/three, not shared dependencies. Preserve linkClick.ts,
workspaceAddress.ts, shared Ticket/Sprint/Review components, all conversation and
backend code, and all shared behavior tests. Do not change generated web/dist yet;
the final canonical build will replace it.

Focused gate: `npm --prefix web run check`, after dependency install. The final
program gate supplies build and remaining frontend/Workspace/Review coverage.
No new E2E: removal introduces no new browser-server interaction.

## Plan and independent review

Tooling/UX auditor traced all producers and consumers at b7ca8e0 and proposed the
scope above. Root independently checked App registration, route inputs, embedding
sites, and CSS ownership. No unique product data or authority is lost. Keep the
shared click helper and protect conversation styles even where their comments
mention Atlas. Plan accepted without unresolved findings.

Independent implementation review: 00-review-first-chunks.md reports no actionable
findings or protected-code changes. Ready for serial integration.

## Implementation

Removed the Atlas route, navigation entry, component tree, world model and renderer,
dedicated tests, and Atlas-only CSS. Removed `three`, `@types/three`, and their
now-unused lockfile entries. Kept the shared Ticket, Sprint Item, Review, file-link,
conversation, and backend implementations intact; only stale Atlas wording in shared
non-conversation comments and the frontend documentation was corrected. Old
`#/atlas` links now reach the router's existing unknown-screen behavior.

Focused gate after reconciling `web/node_modules` with the reduced dependency tree:

```text
$ npm --prefix web run check

> check
> svelte-check --tsconfig ./tsconfig.json

Loading svelte-check in workspace: /Users/khushaljagota/Coding/planning-v2-worktrees/panels-simplification/web
Getting Svelte diagnostics...

svelte-check found 0 errors and 0 warnings
```

Additional audit: `npm ls --prefix web three @types/three --depth=0` reports an
empty tree; Atlas references are absent from `web/src`, `web/tests`, and
`docs/frontend.md`; `git diff --check` is clean. The protected shared conversation
CSS comments that mention Atlas were deliberately left unchanged. The final program
run owns `./verify` and the generated `web/dist` update.
