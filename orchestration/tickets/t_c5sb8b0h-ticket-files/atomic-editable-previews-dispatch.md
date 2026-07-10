# Atomic editable previews implementation dispatch

Implement `atomic-editable-previews-plan.md` in the current worktree with strict vertical RED → GREEN cycles.

## Owned production files

- `assets/markdown.js`
- `assets/app.css`
- `web/src/lib/markdownEdit.ts`
- one focused shared Svelte mounting adapter under `web/src/lib/`
- `web/src/components/MarkdownBlock.svelte`
- `web/src/components/InlineEdit.svelte`
- `web/src/components/ApprovalBlock.svelte`
- `web/src/components/ProposalCard.svelte` only if integration needs it
- `web/src/routes/DayRoute.svelte`

## Owned tests

- `tests/e2e/test_ticket_file_previews.py`
- `tests/e2e/test_flows_a.py`
- `tests/e2e/test_flows_b.py`
- focused browser/Node tests needed for renderer tokens, serializer, or adapter behavior

## Required sequence

1. Change the passed-field browser test first and run it RED. Record the exact expected failure.
2. Implement only the smallest continuous-editable slice and run it GREEN.
3. Add and run each edge test before its production fix: exact renderer token, async no-op blur, adjacent edits, atomic deletion, focused preview actions, gating approval payloads, and global control removal.
4. Preserve the old contenteditable interaction. There must be no edit/source buttons or textarea mode.
5. Keep preview blocks mounted while the parent is focused; atomic slots serialize only their renderer-authored source token.
6. Run focused checks and the affected e2e modules. Do not run `./verify`; the integrator owns the one final run.
7. Do not edit docs, decisions, PROGRESS, skills, or orchestration plans. Write an implementation report to `atomic-editable-previews-implementation-report.md` with every RED/GREEN command and result.
8. Do not commit.

If an acceptance detail is awkward, solve it inside the shared adapter/editor contract rather than adding per-surface behavior or controls.