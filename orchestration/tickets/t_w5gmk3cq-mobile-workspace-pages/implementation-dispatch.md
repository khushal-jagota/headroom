# Implementation dispatch

Implement Panels ticket `t_w5gmk3cq` in this isolated worktree.

Read `AGENTS.md`, `PRINCIPLES.md`, `contract.md`, `implementation-plan.md`,
`plan-review.md`, `web/src/routes/BoardRoute.svelte`, the Workspace breakpoint in
`assets/app.css`, the relevant routing in `web/src/App.svelte`,
`tests/e2e/test_chief_of_staff.py`, and `docs/frontend.md`.

## Allowed edits

- `web/src/routes/BoardRoute.svelte`
- `tests/e2e/test_chief_of_staff.py`
- `docs/frontend.md`
- generated `web/dist/**`
- `implementation-report.md` in this ticket directory

Do not edit contracts, backend code, other tests, skills, `PROGRESS.md`, `decisions.md`,
other orchestration files, live data, or ticket state. Do not commit, merge, push,
restart the live service, or run canonical `./verify`.

## Required implementation

1. Add focused mobile coverage for Ticket and Chief of Staff selection from Workspace.
   Run the new test against the unchanged tracked frontend and record meaningful RED.
2. Make the two Workspace selection handlers evaluate
   `window.matchMedia("(max-width: 960px)").matches` at interaction time.
3. At narrow width, use the existing encoded `#/ticket/<ticket-id>` and `#/chief`
   destinations. Otherwise preserve current Workspace hashes and inspector behavior.
4. Update the live frontend documentation and build tracked frontend output.
5. Run focused Playwright for `tests/e2e/test_chief_of_staff.py`, `npm run check
   --prefix web`, `npm run build --prefix web`, and `git diff --check`.
6. Write `implementation-report.md` with exact RED/GREEN commands and results, changed
   files, and any concern for review.

