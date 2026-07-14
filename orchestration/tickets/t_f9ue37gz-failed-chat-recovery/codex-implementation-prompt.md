You are the leaf implementer completing Panels ticket t_f9ue37gz in this isolated recovery worktree. Read AGENTS.md, PRINCIPLES.md, orchestration/tickets/t_f9ue37gz-failed-chat-recovery/implementation-contract.md, chat-outcome-plan.html, and the current partial diff. Work directly; do not delegate, commit, merge, edit PROGRESS.md/decisions.md, or run ./verify.

The partial backend establishes ChatState `{messages,outcomes,active_turn}`, terminal outcome projection, schema v21 `recovery_of_turn_id`, and a direct continuation route. The partial frontend has types/API/transcript merging but no outcome markup/handler/styles. Finish the approved feature with strict TDD and focused verification.

Required backend corrections/tests:
- Update exact pre-existing ChatState assertions for empty `outcomes` without weakening them.
- Recheck Ticket `ticket_status != agent_running_step` inside the same BEGIN IMMEDIATE transaction that inserts continuation; the outer check is not enough.
- Use the exact session key validated/inserted by that transaction for gateway execution, not a stale `_resolve` value.
- Prove duplicate/double-click, later turn, active turn, unbound, current-session mismatch, worker origin, interrupted human, ordinary no-output failure, and no gateway call on rejection.
- Prove ticket/day/Chief backend state and eligibility without state reads materializing empty day/agent rows.
- Add v20→v21 migration tests: preservation, column/index, idempotence, FK integrity, fresh schema.
- Update docs/chat.md and only the stale ChatState sentence in docs/frontend.md if needed.

Required UI/browser:
- Render a compact outcome immediately under the turn: `Failed` versus `Interrupted`, any partial Markdown exactly once, Continue only when `can_continue`.
- Do not show raw internal errors. Add per-turn continuation pending state, reject repeat clicks, refresh on success, existing ErrorLine on failure, and accessible/data selectors.
- Preserve composer, slash/image controls, Pause, polling, scrolling/follow rules, preview subtree identity, and resource invalidation.
- Style in assets/app.css using existing tokens and the tracked artifact; no screen/composer redesign.
- Browser proof for every mounted ChatPanel surface (Ticket and Chief). There is no current Day Chat screen; prove Day via backend only and do not invent a screen. Cover ordinary no-output failure, partial failure, interruption, unsafe no-action, eligible continuation, refresh, and existing startup restart recovery.

Run vertical RED→GREEN tests. Then run:
- focused owned unit tests plus tests/unit/test_chat_seed.py and tests/unit/test_db.py
- Ruff on changed Python and focused mypy
- npm --prefix web run check, build, test
- focused new/affected Playwright cases with `PYTHONPATH="$PWD/src"`
- git diff --check

Remove generated language-server junk and ensure generated web/dist points to current source output. Return exact results and any blocker. Do not run full ./verify or commit.