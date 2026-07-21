# ACP-06 implementation report

Status: **COMPLETE** — integrated review READY and live schema-v25 cutover passed.

## Delivered

- Replaced Chat transcript state as automatic-work correctness with the exact eight-field
  `employee_step_runs` repository and routed the runner, eligibility, restart recovery, permission
  guard, paired-stage lookup, stale cleanup, and Ticket guards through that owner.
- Added the atomic schema-v25 cutover. It converts only legacy worker-step correctness, interrupts
  migrated running rows, releases running Tickets, clears old session mirrors and bindings, deletes
  legacy conversation state, removes the Day chat column, and advances the version in the same
  transaction.
- Kept one ACP production conversation path and preserved real pending worker-context delivery in
  the model prompt. Removed Chat, Minds, relay, raw adapter, neutral-pane, managed-chat-image, and
  duplicate session/transcript owners.
- Replaced the legacy composer with the ACP composer. Commands come from ACP and selected images are
  sent as ordered inline ACP image blocks. Generic and Ticket-file preview remain.
- Rewrote the live architecture and product docs for the sole ACP end state.

## Integrated-review corrections

The one review found four P1 defects. Each was accepted and corrected in the same bounded round:

1. Controlled shutdown had terminally interrupted the Employee-step while leaving its Ticket
   running. Shutdown now interrupts and drains the child within the existing deadline but preserves
   the exact durable running row for startup recovery. Recovery replaces that row and continues the
   same ACP session.
2. The v25 proof matrix omitted destructive input shapes. It now covers complete, errored, and
   running worker rows; human messages, activity, and clarification; matching and nonmatching Chat
   events; Day and Chief sessions; and a running Ticket without a usable worker row. Forced rollback
   covers both direct pre-column and ACP-05-amended v24 shapes and proves the new table and index are
   absent afterward.
3. v25 initially accepted any JSON array as compaction provenance. One canonical parser now enforces
   exact objects, valid triggers, non-empty IDs, and unique IDs for both the binding repository and
   migration. Invalid provenance aborts without mutation.
4. The ACP composer cleared text and images when admission returned `ok: false`. Rejected not-ready
   and unsupported-Steer sends now show the exact error while retaining the full draft, image order,
   and object URLs. A successful retry clears and revokes each image once.

The orchestrator also made one wording-only cleanup in the restart prompt: the worker is told to
reply in the conversation, not in the deleted Chat system.

## Evidence

The implementation lanes reported the retained Python unit suite passing, current Playwright e2e
suite 99/99 passing, Ruff clean across product/unit/e2e, strict Mypy clean across 122 source files,
all 13 web test groups passing, zero Svelte diagnostics, a clean production build, and clean caller
closure except intentional historical recognition inside v25.

The review correction gates are recorded verbatim in `focused-checks.txt`. No canonical `./verify`
was run for this ticket. ACP-10 owns the single final frozen-tree run.

## Live cutover and actual Panels proof

The old process on `127.0.0.1:8767` was stopped before starting the reviewed code. Opening the real
`data/planning.db` migrated it from schema 24 to 25. Direct inspection then proved:

- 451 legacy worker-step rows preserved as correctness history: 410 complete, 14 errored, and 27
  interrupted;
- zero running Tickets and zero running Employee-step rows;
- no `chat_turns`, `chat_messages`, `chat_turn_activity_entries`, or `agent_chat_sessions` table;
- no `days.chat_session_key` column; and
- an empty `PRAGMA foreign_key_check` result.

The already-open browser immediately created a fresh generation-1 Chief Hermes/ACP binding. Actual
Computer Use against `http://127.0.0.1:8767/#/workspace` showed the normal dark Panels layout and sent
`Reply exactly ACP06 LIVE CUTOVER READY 20260720.` The worker returned exactly
`ACP06 LIVE CUTOVER READY 20260720.` and both messages replayed after a hard browser reload. Opening a
real Ticket then created its own fresh generation-1 binding; the Ticket's stored
`employee_session_id` matched the ACP binding's session ID exactly. The live server remains running.
