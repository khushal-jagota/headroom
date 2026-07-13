# t_yctn6m7m — Restart and crash recovery

## Outcome

A restart must not leave ticket-worker or chat work permanently marked running. Panels resumes stranded work in the same durable Hermes session, does not replay the original prompt, preserves partial visible output, and bounds shutdown so deployment cannot wait forever.

## Owner-resolved recovery rule

For every ticket still at `agent_running_step` after restart, send the same stored Hermes session a fresh recovery message. The message tells the worker to inspect the canonical ticket and existing conversation, continue unfinished work, avoid repeating completed actions, and file the currently requested proposal. If it already filed that proposal, it should only say so in chat.

Normal proposal filing remains the sole authoritative handoff to `awaiting_approval`. Do not infer completion from an old proposal or parse an agent's prose into a database transition. Do not resend the original step prompt.

Use the same continuation principle for an interrupted ordinary chat response. A missing or unusable stored session, transport failure, or impossible state mismatch is the recovery-error fallback—not the normal path.

## Boundaries

- Existing single-process runtime only; no replicas, HA, generic VPS, or deployment packaging work.
- Reuse canonical ticket/chat writers, events, readiness polling, and doorbell behavior. Do not create a parallel retry scheduler.
- Stop new admission and discovery before bounded drain/interrupt. Unsettled durable work must remain recoverable on the next startup.
- Preserve existing ticket and chat UI interactions; add no unrelated redesign.
- Implementation occurs only on branch `ticket/t_yctn6m7m-restart-recovery`; merge belongs to Closeout.

## Required proof

- Proposal write and `awaiting_approval` are atomic; returned proposals are cleared before a revision run.
- Startup continuation uses the same stored session and the recovery message, not the original prompt.
- Old visible turns settle without losing partial output; a fresh recovery turn is visible and settles normally.
- Ordinary chat continuation follows the same rule.
- Missing/unusable sessions and impossible mismatches fail honestly without guessing or duplicate submission.
- Repeated restart remains recoverable.
- Shutdown admission closes, discovery stops, and total waiting is bounded.
- Focused backend/browser tests, independent Codex review, and one clean `./verify` on the ticket branch.
