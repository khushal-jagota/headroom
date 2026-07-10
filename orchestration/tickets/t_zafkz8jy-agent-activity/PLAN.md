# t_zafkz8jy — Expandable live agent activity

## Accepted outcome

While an agent is working, Panels shows enough live activity to make progress understandable during long runs. Ticket and chat surfaces stay quiet by default; an inline disclosure reveals useful activity details on demand without exposing raw internals.

## Accepted interaction

Keep the existing bubble-less live row in the shared `ChatPanel`. It continues to show the latest plain-language activity. A chevron expands a compact chronological timeline for only the active turn, in place beneath that row. Ticket and Chief of Staff chat use the same behavior. The transcript, composer, pause behavior, follow eligibility, Latest affordance, and established responsive behavior do not change.

Reference sketch: `/Users/khushaljagota/.hermes/planning-v2/data/files/tickets/t_zafkz8jy/artifacts/agent-activity-approach.png`.

## Contract boundary

- Add a typed, ordered, bounded active-turn activity collection to `src/planner/chat/contracts.py` and the TypeScript chat contract.
- Persist activity separately from visible messages and from raw Hermes events. A schema migration must preserve existing databases.
- One normalizer maps accepted Hermes phase/tool/command observations to display-safe entries. Both human chat and ticket-worker event paths must use the same meaning.
- Entries may carry only display category, safe label, lifecycle state, stable identity/order, and timing needed by the UI. Do not persist chain-of-thought, model reasoning text, tool arguments, tool results, raw command output, or arbitrary payloads.
- Repeated deltas must not create timeline spam. Start/end observations for the same action update one entry when identity is available. Keep at most the newest 100 entries per running turn through one named backend constant; delete the oldest entries in stable order when the cap is exceeded.
- `ChatState.active_turn` returns the ordered collection. Existing `phase` and `activity_label` remain the collapsed summary and backward-compatible state.
- The disclosure is a native accessible button with correct expanded/control state, collapsed by default. Live refresh must not steal scroll control from a reader who moved upward.
- Activity belongs only to the running turn. `finish_turn` and `fail_turn` delete its entries, including interrupted/pause completion; foreign-key cascade removes them when a ticket's chat turns are deleted. Completed transcript messages remain unchanged, and no permanent transcript audit surface is added by this ticket.

## Strict TDD slices

1. **Contract and persistence:** add failing backend tests for ordered activity attached to an active turn, the exact 100-entry cap, finish/fail/interruption cleanup, ticket-delete cascade, and migration from the current schema; then add the smallest schema, contract, writer, and reader changes.
2. **Normalization and both producers:** add failing tests for tool/command start/end, deduplication/bounds, safe-field exclusion, human chat, and worker turns; then implement one shared normalization path.
3. **UI disclosure:** add failing Playwright coverage for the collapsed row, keyboard expansion, ordered live details, and collapse; then implement the shared `ChatPanel` markup/types/styles.
4. **Non-regressions:** prove ticket and Chief chat, streaming output, pause/composer behavior, reduced motion, follow eligibility, and Latest visibility remain intact.

Capture the decisive RED and GREEN commands/output in the implementation report.

## Files likely in scope

- `src/planner/chat/contracts.py`, `src/planner/chat/data.py`, `src/planner/chat/service.py`
- `src/planner/minds/shared_gateway.py` only if the normalized stream contract requires it
- `src/planner/core/db.py`
- `web/src/lib/types.ts`, `web/src/components/ChatPanel.svelte`, `assets/app.css`
- focused chat/database/gateway unit tests and `tests/e2e/test_live_chat_state.py`
- `docs/chat.md`, `PROGRESS.md`, `decisions.md`

Do not modify unrelated Ticket assignment work, the composer interaction, or the visible message model.

## Review and verification

- Codex reviews this contract before implementation and reviews the complete diff afterward, read-only against this plan and the approved sketch.
- Address or explicitly refute every concrete finding.
- Run focused backend and browser checks, then one canonical `./verify` in this worktree.
- Commit the verified implementation on `ticket/t_zafkz8jy-agent-activity`.
- Do not merge during Implementation. Merge, integration repair, and post-merge `./verify` belong to Closeout.
