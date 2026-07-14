# Frontend implementation dispatch

Implement the frontend slice of `contract.md` in this worktree.

Read `AGENTS.md`, `PRINCIPLES.md`, `contract.md`, `ownership.md`, and current frontend/tests. Use RED→GREEN tests. Freeze against the public wire shape in the contract; backend work may be concurrent.

Own only `web/src/**`, `web/tests/**`, and the dedicated new Playwright file `tests/e2e/test_stage_ownership_frontend.py`. Do not edit backend, skills/docs, PROGRESS/decisions, or other orchestration files. Do not commit and do not run `./verify`.

Preserve the existing Ticket layout/interactions. Replace implementer with execution route, add current-stage owner control (default/worker/user/paired), derive Take over/Release from explicit current-stage user override, render paired_work across shared labels/lifecycle, Workspace filter/StageMark/byline and Review where applicable, and label stored at_cap=propose as Continue while sending propose. Use backend-provided default/effective ownership; do not reconstruct it. Update types and resource event completeness. Add meaningful desktop/mobile Playwright assertions in the dedicated file plus focused Node/Svelte tests where useful.

Run focused frontend unit, Svelte check, and dedicated browser tests if the test server can consume the concurrent backend. Finish with files changed, RED/GREEN commands/results, and assumptions.