# Follow-up implementation dispatch

Implement `follow-up-plan.md` in the current worktree using strict vertical TDD.

## Owned files

- `web/src/lib/filePreview.ts`
- `web/src/components/FilePreview.svelte`
- `web/src/components/MarkdownBlock.svelte`
- `web/src/components/InlineEdit.svelte`
- `web/src/components/ApprovalBlock.svelte`
- `assets/app.css`
- `web/tests/file-preview.test.mjs`
- `tests/e2e/test_ticket_file_previews.py`
- Existing e2e files only where their editor selectors/expectations intentionally change because Markdown editing becomes explicit source mode.
- `docs/frontend.md` and the implementation report only after focused green.

Do not touch backend file-serving policy, database contracts, unrelated UI, or product data.

## Required sequence

1. Add one failing browser/unit assertion for the real normal-state at-rest regression and run it RED.
2. Implement the smallest at-rest `MarkdownBlock` / active raw-source editor seam and run GREEN. Preserve non-Markdown `InlineEdit` behavior.
3. Add failing assertions for inline Markdown plus bounded nested/self links; implement and run GREEN.
4. Add failing assertions for the HTML sandbox card/new-tab Panels action, unsupported download card, and external link-preview card; implement and run GREEN.
5. Add and pass approval-draft edit/cancel/approve coverage.
6. Run focused frontend tests, Svelte check/build, and the preview e2e module. Do not run `./verify`; the integrator owns the one final run.

Return the exact RED/GREEN commands and outputs, files changed, design decisions, and any unresolved issue. Do not commit.
