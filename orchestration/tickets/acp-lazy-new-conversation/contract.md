# Empty Panels conversations and lazy backend sessions

## Problem

Panels currently treats a Panels conversation and an ACP backend session as the same thing.
Opening an unbound conversation or pressing **New** therefore calls `session/new` immediately.
That is not a safe universal contract: a backend may not make an empty session loadable until its
first turn. After Panels restarts, the browser then tries to load an identifier that the backend
cannot load and the pane becomes unwritable.

## Contract

1. A Panels conversation exists independently of an ACP backend session.
2. Opening an employee with no backend session returns an empty, ready, writable conversation. It
   does not spawn a backend child and does not call `session/new` or `session/load`.
3. **New** advances the Panels conversation generation, clears the visible transcript and durable
   backend binding, and leaves the new conversation empty. It does not create a backend session.
4. The first human prompt or Automatic Employee prompt creates the selected backend session,
   durably binds it to the current Panels conversation generation, and submits that prompt through
   the normal broker. Later prompts and reloads use the durable binding as before.
5. An empty conversation survives a Panels restart and remains ready and writable.
6. The backend, model, and reasoning configuration selected for a new Chief conversation is
   snapshotted when **New** is accepted. A later settings edit does not silently alter that empty
   conversation's eventual launch configuration.
7. Browser cursors identify the Panels conversation generation. If a browser presents a cursor for
   another generation, Panels sends a full reset for the current generation. It does not reject the
   WebSocket.
8. ACP session identifiers are optional only while a Panels conversation is empty. Every ACP
   notification, permission, queue item, echo, and programmatic prompt still carries and validates
   the real bound ACP session identifier.
9. Failures to create, bind, load, replay, or deliver a session are logged with the employee,
   conversation generation, operation, and backend where known.

## Acceptance tests

- Browser attach to an unbound Ticket and Chief creates no backend session and receives empty
  reset/ready state.
- **New** on a bound conversation creates no replacement backend session.
- Restart after **New** returns the same empty generation and permits a prompt.
- The first browser prompt creates exactly one backend session and is delivered exactly once.
- The first Automatic Employee prompt does the same without requiring a browser.
- Two racing first prompts cannot create two durable winners.
- A stale generation cursor receives the current full reset rather than WebSocket close 1008.
- Existing bound attach, replay, compaction, cancellation, permissions, and worker delivery tests
  remain green.

## Out of scope

- Changing provider-specific replay formats.
- Reworking the turn queue, compaction, permissions, or transcript presentation.
- General conversation architecture cleanup beyond separating the two identities that caused this
  failure.
