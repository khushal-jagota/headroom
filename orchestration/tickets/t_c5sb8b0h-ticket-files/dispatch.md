# Implementation dispatch

Implement the reviewed ticket and plan in the current shared worktree using strict TDD.

## Ownership

Expected new areas: `src/planner/files/` (contracts, pure path logic, API), a focused backend test file, `web/src/lib/filePreview.ts`, `web/src/components/FilePreview.svelte`, `web/src/routes/FilePreviewRoute.svelte`, and focused e2e coverage.

Shared files requiring surgical edits because they already contain unrelated work: `src/planner/core/server.py`, `web/src/App.svelte`, `web/src/components/ChatPanel.svelte`, `web/src/components/MarkdownBlock.svelte`, `web/src/lib/markdownEdit.ts`, `assets/app.css`, `docs/frontend.md`, and built `web/dist` output. Re-read each immediately before editing. Do not revert, reformat, or absorb unrelated hunks.

## Non-negotiable contracts

- Backend path resolution and unsafe-response behavior follow `plan.md` exactly, including residual/double-encoded traversal, `relative_to` containment, `nosniff`, and attachment policy.
- HTML preview uses fetched text assigned through `srcdoc` in an empty sandbox; no scripts, no same-origin, no `innerHTML`.
- The hash route uses URLSearchParams and encoded query values, not slash segments.
- `FilePreviewTarget` and `resolvePreview` own classification; surfaces do not inspect extensions/MIME.
- Read-only Markdown may hydrate preview components. Editable Markdown must keep ordinary anchor DOM so the existing serializer round-trips raw source.
- All persisted chat message roles use the shared Markdown path.

## Required proof

- Watch each focused test fail before production code, then pass.
- Backend cases listed in plan item 1 plus response headers/disposition.
- Browser behavior listed in plan items 4–6, including blocked HTML script and source-preserving inline editing for each link kind.
- Run targeted tests/check/build only. Do not run final `./verify`; the integrator owns the one canonical run.
