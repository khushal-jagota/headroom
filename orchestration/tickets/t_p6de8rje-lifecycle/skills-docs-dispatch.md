# Skills/docs implementation dispatch

Implement the role-guidance and live-doc portion of `contract.md` test-first. Read AGENTS.md, docs/CLAUDE.md, D55, plan-review.md, and all named skill sources first.

## Ownership

You may edit `skills/**`, `docs/**`, and `tests/unit/test_minds.py` for loaded/provisioned skill assertions. Do not edit backend/frontend production, other tests, PROGRESS.md, decisions.md, or unrelated pointers. Do not commit.

## Required guidance

- `panels-worker`: list the new states and five gated outputs. Implementation follows the approved plan, performs the work, and proposes a concise reviewable package with concrete evidence. Closeout begins only after Implementation approval, performs only applicable merge/deploy/follow-up/bookkeeping, and proposes a concise verified report. The worker still handles one current step only and never approves its own proposal.
- Keep proposal-shape, recap, field-note, artifact, and authority guidance current without changing the established artifact-planning expectation or inventing implementer assignment.
- `panels`: describe five canonical ticket outputs and the six-stage sequence; artifact wording uses Implementation/Closeout, not result.
- `panels-chief-of-staff`: align approval/orchestration and strict external-work settled-prefix guidance while keeping Chief outside worker execution.
- Rewrite or retire stale `planning-worker.md`, `planning-executor.md`, and `planner-main.md` so no active role prompt instructs Ticket workers to use `in_progress`, `needs_review`, or the `result` field. Preserve valid general-language uses only where they cannot be mistaken for lifecycle contract.
- Update live `docs/tickets-and-gates.md`, `docs/systems.md`, `docs/frontend.md`, and any other actually stale system doc. Keep docs plain, current, non-duplicative, with required ASCII shape and verified date.

## TDD and evidence

First add focused assertions in `tests/unit/test_minds.py` that read the provisioned skill symlinks and pin the new stage names/distinct responsibilities while rejecting obsolete lifecycle instructions; run to expected RED. Then update skills/docs and rerun GREEN. Run the focused test and `git diff --check`; do not run `./verify`.

Write RED/GREEN commands, decisive RED lines, changed files, and any wording judgment to `orchestration/tickets/t_p6de8rje-lifecycle/skills-docs-report.md` before stopping.
