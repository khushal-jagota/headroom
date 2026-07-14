# t_f9ue37gz — implementation review contract

## Accepted outcome

Failed and interrupted Panels Chat turns remain visible in ticket, day, and Chief transcripts. Any safe partial output survives settlement, refresh, and restart. The transcript distinguishes failure from interruption. Recovery is offered only where Panels can continue the already-bound Hermes conversation without resending the original prompt.

## Accepted implementation shape

- `chat_turns` remains the canonical owner of turn status, output, error, and session binding.
- Chat state projects terminal failed/interrupted outcomes into transcript order instead of duplicating canonical status into plain message rows.
- The shared Chat panel renders a compact outcome beneath preserved output. Existing composer, scrolling, live-turn polling, Markdown rendering, and context-specific screens stay unchanged.
- A recovery action targets one specific settled turn. It must validate atomically that the turn is an eligible human failed/interrupted turn, has a session key matching the entity's current stored session, has not already been continued, and no other turn is active.
- Recovery sends a new continuation instruction through `require_existing_session=True`; it never resends the original prompt or creates a fallback session.
- Unsafe and worker-origin terminal turns remain visible without a human recovery action.
- Direct-only auth, event-driven Chat invalidation, Ticket worker exclusion, and startup stale-running-turn recovery remain intact.

## Required proof

Backend and browser coverage must include:

- ordinary failure with no output;
- failure after partial output;
- interruption, distinct from failure;
- refresh persistence;
- backend state/eligibility for ticket, day, and Chief Chat;
- browser rendering in every currently mounted shared `ChatPanel` surface: Ticket and Chief. There is no current Day Chat screen (`docs/days.md` explicitly says Ticket Chat is separate from the Day page), so this ticket must not invent one merely to satisfy a test matrix;
- eligible continuation with exact existing session and continuation payload;
- no action for unbound, mismatched, stale/already-recovered, worker-origin, or competing-active-turn cases;
- restart recovery still preserves the interrupted predecessor and continues the same session.

The visual target is `chat-outcome-plan.html` in this directory. It shows the new outcome row inside the current Chat layout; it does not authorize a composer or screen redesign.
