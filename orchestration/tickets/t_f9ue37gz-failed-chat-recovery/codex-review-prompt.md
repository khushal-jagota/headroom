You are the final independent implementation reviewer. Inspect directly; do not delegate, invoke another reviewer, or modify files.

Review branch `ticket/t_f9ue37gz-failed-chat-recovery` against:

- `orchestration/tickets/t_f9ue37gz-failed-chat-recovery/implementation-contract.md`
- `orchestration/tickets/t_f9ue37gz-failed-chat-recovery/chat-outcome-plan.html`
- `PRINCIPLES.md`
- the full working-tree diff from `556b2fb`

Focus on concrete violations only:

1. Durable transcript projection and ordering, including no duplicate interrupted partial output.
2. Continuation safety: original prompt is never replayed; exact existing session is required; specific-turn admission is atomic against double-click, later turns, active turns, session changes, and Ticket Employee claims; no external call occurs on rejection.
3. Canonical ownership: `chat_turns` owns terminal state and recovery identity; no conflicting message state is introduced.
4. Migration correctness for existing v20 databases, idempotence, foreign keys, and index uniqueness.
5. Shared UI behavior: visible Failed versus Interrupted, partial Markdown preserved, Continue only when eligible, per-turn pending/error behavior, existing composer/Pause/scroll/resource invalidation preserved.
6. Current surfaces: Ticket and Chief browser behavior; Day state remains supported without inventing a Day Chat screen.
7. Tests prove both actions and important non-actions, refresh and startup restart behavior, and do not silently weaken existing assertions.
8. Docs accurately describe the live system.

Return either `NO VIOLATIONS` or a concise severity-ordered list with exact file/line evidence and required correction. Ignore unrelated repository history and do not request speculative refactors.
