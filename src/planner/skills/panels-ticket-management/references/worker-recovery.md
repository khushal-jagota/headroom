# Recovering errored Panels worker tickets without interrupting live work

Use this when a ticket shows `ticket_status: errored` but may still have a live Hermes worker session.

## Key lesson

Do not immediately release/retry an errored ticket if its `chat_session_key` still has an active `tui_gateway.slash_worker` or recent Hermes log activity. A retry can submit into a busy session; Hermes may interrupt/queue the new prompt, causing another `Operation interrupted` result while the original worker turn is still doing useful work.

## Recovery sequence

1. Identify the ticket and session key:
   - `panels ticket show <ticket_id> --json`
   - note `state`, `ticket_status`, and `chat_session_key`.
2. Inspect visible Panels chat state, but treat it as UI/audit state only:
   - `/api/chat/<ticket_id>/state`
   - check `active_turn` and recent messages.
3. Inspect the underlying Hermes session/process before release:
   - `pgrep -fl 'slash_worker --session-key <chat_session_key>|tui_gateway.entry|panels serve'`
   - `tail data/hermes-home/logs/agent.log` filtered by the session key.
4. If the raw Hermes session is making API calls, tool calls, patches, tests, or compression progress, **do not release the ticket**. Wait for the turn to settle and monitor the canonical ticket fields instead.
5. If the user has already sent `/compress` or `continue` to the worker session, verify that those messages reached the real Hermes session via logs before retrying. They may be enough to unblock the worker.
6. When the raw turn ends, re-read the ticket. The canonical `state` or gated field may have advanced even if `ticket_status` remains stale as `errored`.
7. Only use the ticket release endpoint when no raw Hermes turn is active and the worker session is idle/stuck.

## Monitoring pattern

A lightweight monitor should poll:

- ticket `state`, `ticket_status`, `updated_at`, and current gated field/value/proposal;
- the Hermes log for the `chat_session_key`;
- process presence for `tui_gateway.slash_worker --session-key <key>`.

Stop monitoring when the worker turn ends, the ticket state advances, or a result/proposal appears. Report both the canonical ticket state and any stale-status mismatch.

## Pitfall

`ticket_status: errored` is not always authoritative after manual recovery. If `state` advances to `needs_review` or a result value/proposal appears, report the ticket as recovered but flag the stale status for follow-up.