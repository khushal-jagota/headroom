# Ticket t_bpy3g36e — Make chat auto-scroll follow normal chat UX

## Accepted success

Panels chat opens at the latest message, follows new and streaming output while the reader is at or near the bottom, and preserves the reader's position after a meaningful scroll upward. A jump-down affordance appears whenever the conversation bottom is meaningfully below the viewport, based on distance rather than whether content is new. Returning to the bottom manually or through the affordance resumes following.

## Accepted boundaries

- The shared `ChatPanel` owns the behavior for every surface that mounts it.
- Follow mode controls whether rendered content growth moves the viewport.
- Actual distance from the bottom independently controls affordance visibility.
- Browser tests prove both scrolling and non-scrolling behavior.
- `docs/chat.md` must replace its now-stale one-time-scroll description.
