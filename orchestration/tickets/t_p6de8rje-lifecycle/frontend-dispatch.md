# Frontend implementation dispatch

Implement the frontend portion of `contract.md` using strict TDD. Read AGENTS.md, PRINCIPLES.md, ownership.md, and the existing Ticket/Review/Board components first.

## Ownership

You may edit `web/src/**`, `web/tests/**`, `web/dist/**`, and lifecycle-specific browser tests in `tests/e2e/test_flows_a.py`, `tests/e2e/test_flows_b.py`, `tests/e2e/test_board_stage_indicators.py`, and `tests/e2e/test_ticket_file_previews.py`. Do not edit backend Python, skills, docs, PROGRESS.md, decisions.md, or unrelated pointers. Do not commit.

## Frozen backend contract

Fields are `success`, `approach`, `plan`, `implementation`, `closeout`; states are `needs_success`, `needs_approach`, `needs_plan`, `needs_implementation`, `needs_closeout`, `done` plus terminal `dropped`. Every non-terminal linear state gates its same-named field. Approval is always a pending proposal represented through the existing `ticket_status`; there is no `review` queue kind or special approve endpoint.

## Required UI behavior

- Present the visible sequence Success → Approach → Plan → Implementation → Closeout → Done. Done is a terminal stage marker, not a field editor.
- Reuse existing stage sections, status visuals, proposal cards, scope picker, accept/edit/revision actions, and interaction behavior. Do not redesign Ticket, Workspace, or Review.
- Implementation and Closeout each show running/waiting/awaiting-approval/completed states through the same generic logic.
- Review queue renders pending implementation and closeout proposals through the ordinary field path; remove special final-review rendering and endpoint use.
- Board/Sprint labels and current-stage summaries use the new states and fields. Preserve unrelated resource invalidation and interaction behavior.

## TDD and evidence

Add or update the narrowest static/browser assertion first and show expected RED before source edits. Then implement and rerun GREEN. Run Svelte check, frontend unit tests, build, and only owned lifecycle browser tests; do not run `./verify`.

Write RED/GREEN commands, decisive RED lines, changed files, and assumptions to `orchestration/tickets/t_p6de8rje-lifecycle/frontend-report.md` before stopping.
