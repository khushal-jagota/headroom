# Specialist and public-flow slice dispatch — t_gn7x278u

You are the leaf implementer. Edit directly; do not delegate. Read `implementation-dispatch.md`, `plan-review-disposition.md`, and the current backend diff, then complete only the shipped specialist, live docs, and public-flow coverage.

Own:

- `skills/panels-worker-new-worker/SKILL.md`
- `docs/worker-types.md`
- focused tests that validate the provisioned specialist contract
- focused backend/e2e/Playwright tests for ordinary Ticket Chat session continuity, paired resting/proposal behavior, and manifest-driven UI progression
- frontend source or tracked build output only if a focused public-flow test proves a real change is necessary

Requirements:

1. Update the visible lifecycle to `Understanding → Stages → Thinking → Drafting → Closeout → Done` after universal Kickoff.
2. Add a concrete `needs_understanding` section. It must tell the same Employee to begin with a small purposeful core set covering purpose/outcome, hard/ambiguous/risky work and human judgment, and constraints/examples/boundaries; follow up only for material gaps; stop when these are sufficiently understood to design the lifecycle; then propose a concise durable Understanding result carrying the material facts forward.
3. Preserve shared paired mechanics from `panels-worker`; do not restate or recreate chat/session/approval machinery beyond the specialist-specific guidance needed to use the paired conversation.
4. Add deterministic source/provisioning assertions for the exact protocol. Separately prove through the existing real server/gateway fake/public APIs that ordinary Ticket Chat on a `new_worker` ticket at `needs_understanding` binds/reuses its Employee session and that an Understanding proposal parks for approval. Do not claim tests prove model judgment.
5. Add/update Playwright coverage that the served manifest renders Understanding in field/stage order and paired state, and that approval progresses to Stages. Keep layout/interactions unchanged. No visual redesign or HTML artifact.
6. Update `docs/worker-types.md`, including the former claim that every shipped non-terminal Stage is worker-owned.

Use strict focused RED→GREEN where production/source behavior is missing. For downstream manifest-driven UI coverage added after the backend tracer bullet, if it passes immediately because generic frontend behavior already works, record that honestly rather than inventing a frontend change; prove red-capability by temporarily reverse-applying only the backend lifecycle diff if practical and safely restored.

Run focused pytest, Ruff on changed Python tests, Svelte/frontend checks only if frontend source changes, focused Playwright, and `git diff --check`. Append exact commands/results and file list to `implementation-report.md` (create it if absent). Do not edit backend production already implemented unless a focused failing test proves a contract violation. Do not edit `PROGRESS.md` or `decisions.md`; do not run `./verify`, commit, merge, touch live data, or propose the Ticket.

Use `PYTHONPATH=/Users/khushaljagota/.hermes/worktrees/planning-v2-t_gn7x278u/src` with `/Users/khushaljagota/.hermes/planning-v2/.venv/bin/python`.
