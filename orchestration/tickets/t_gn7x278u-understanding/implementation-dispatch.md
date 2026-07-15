# Implementation dispatch — t_gn7x278u

You are the leaf implementer. Edit this worktree directly; do not delegate. Implement the approved Ticket contract using strict vertical RED → GREEN TDD.

Read `AGENTS.md`, `PRINCIPLES.md`, the current `new_worker` definition and specialist, `docs/worker-types.md`, relevant migration/startup-integrity code, shared paired ownership/chat/session code, and:

- `orchestration/tickets/t_gn7x278u-understanding/plan-review-disposition.md`

## Contract

Insert `needs_understanding` / `understanding` immediately after `needs_kickoff` / `kickoff` in the shipped `new_worker` lifecycle. Its default ownership is `paired`. Approval advances to the existing `needs_stages → needs_thinking → needs_drafting → needs_closeout → done` sequence unchanged.

Use only shared machinery: paired resting state, automatic-dispatch exclusion, ordinary Ticket Chat to the durable Employee session, and approval-required paired proposals. Do not add a special interview loop, question counter, parallel chat path, new status, or custom proposal mechanism.

The specialist must concretely guide a bounded Understanding session:

- begin with a small purposeful core set covering purpose/outcome, difficult/ambiguous/risky areas and human judgment, and important constraints/examples/boundaries;
- follow up only when an answer exposes a material gap;
- stop when those categories are sufficiently understood to design the lifecycle;
- propose a concise durable Understanding result carrying those facts forward.

`needs_understanding` is deliberately `new_worker`’s new `first_worker_stage()`. Update external-work, sprint-status, comments/docs, fixtures, and golden expectations accordingly rather than preserving `needs_stages` as a hidden threshold.

Add an idempotent canonical schema migration before startup integrity audit that adds an empty `understanding` slot to every existing `new_worker` row missing it. Preserve every existing slot byte-for-value, stage, ceiling, at-cap mode, ticket status, ownership overrides, Employee session id, recap, notes, proposals, timestamps unless the migration framework’s established contract requires the migration timestamp to change. Do not rewind later-stage tickets. Leave other worker types and already-migrated rows unchanged. Test fresh schema, legacy/current rows at Kickoff and later stages, idempotence, and integrity-audit compatibility.

Keep the frontend manifest-driven. Change frontend source only if a real hard-coded assumption fails. Update focused browser coverage so the served Understanding Stage/field appears in order, rests as paired work, continues through the ordinary chat surface/session, and advances to Stages only after its proposal is approved. No visual redesign or second HTML planning artifact is needed.

Deterministic test claims must stay honest: tests may assert the exact specialist protocol and separately prove transport/session/proposal mechanics. Do not claim code proves model judgment.

## Required sequence

1. Establish one vertical RED for the new manifest/ownership/first-stage contract, then make it GREEN.
2. Add migration preservation/idempotence tests to RED, then implement the migration and make them GREEN.
3. Add specialist-contract tests to RED, then update the source skill and docs.
4. Add shared paired runtime/chat/proposal tests through existing real adapters/fakes to RED, then make only necessary production changes.
5. Add/update manifest-driven frontend/Playwright expectations to RED, rebuild tracked frontend output if required, and make them GREEN.
6. Run focused Python tests, Ruff on changed Python files, mypy if touched contracts require it, Svelte/frontend checks, focused Playwright, and `git diff --check`.
7. Write `orchestration/tickets/t_gn7x278u-understanding/implementation-report.md` with exact RED/GREEN commands and decisive outputs, files changed, and any unresolved issue.

## Boundaries

- Work only in `/Users/khushaljagota/.hermes/worktrees/planning-v2-t_gn7x278u`.
- Do not modify another repository, the primary checkout, installed Hermes skills, live runtime data, or the live SQLite database.
- Do not merge, push, deploy, restart, run the canonical `./verify`, edit `PROGRESS.md` or `decisions.md`, or propose the Ticket.
- Do not commit; the parent orchestrator owns review, verification, and the verified Implementation commit.
- Preserve unrelated behavior and existing later-stage data exactly. Stop and report if the contract cannot be met without a new shared mechanism.

Use `PYTHONPATH=/Users/khushaljagota/.hermes/worktrees/planning-v2-t_gn7x278u/src` with `/Users/khushaljagota/.hermes/planning-v2/.venv/bin/python` so Python and spawned tests resolve this worktree, not the primary checkout.
