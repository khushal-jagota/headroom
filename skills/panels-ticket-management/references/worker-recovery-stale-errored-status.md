# Worker recovery when Panels says `errored` but Hermes is still alive

Session learning from recovering Panels worker tickets whose `ticket_status` showed `errored` while the underlying Hermes `slash_worker` session kept running.

## Symptom

- Ticket shows `state: in_progress` and `ticket_status: errored`.
- Chat has a recent assistant row like `Operation interrupted: waiting for model response (...)`.
- The ticket has a `chat_session_key` and the matching Hermes `slash_worker --session-key <key>` process is still alive.
- Logs keep showing `agent.conversation_loop` API calls, tool calls, compression, or new `turn_context` rows for that same session key.

## What is really happening

Panels ticket status can become stale relative to the real Hermes worker session. A release/retry clears Panels status, but if the Hermes session is still running, the next prompt can interrupt the existing turn and queue/start another turn in the same persistent `slash_worker` process. That can make the ticket look repeatedly `errored` while the worker is still doing work underneath.

An interrupt ends the current model turn; it does not necessarily kill the persistent `slash_worker` process or gateway session.

## Recovery sequence

1. Inspect the ticket and chat state first.
   - Look at `state`, `ticket_status`, `chat_session_key`, active turn, and recent chat rows.
2. Check the matching Hermes session before release.
   - Is `slash_worker --session-key <chat_session_key>` alive?
   - Do logs for that session key show recent API calls, tool calls, compression start/done, or a new `turn_context`?
3. If the raw Hermes session is active, do **not** immediately release.
   - Let the active turn settle, especially if it is compressing or running verification.
   - User nudges such as `/compress`, `continue`, or a simple message may wake a still-alive session, but they can also queue behind a busy turn.
4. If the user explicitly wants to test release anyway, explain that it may interrupt the active turn, then release and watch for a new worker turn.
5. After a release, inspect whether a new `turn_context` started on the same session key and whether Panels still shows stale `errored`.

## Pitfalls

- Do not infer “worker is dead” from `ticket_status: errored` alone.
- Do not repeatedly release an active live session; it can churn interruptions and hide useful progress.
- A monitor that exits on `Turn ended` may be seeing the turn you intentionally interrupted, not the newly queued turn. Re-check logs for a later `turn_context` and API calls.
- Compression can take minutes on huge sessions. If logs show `context compression started`, wait for `context compression done` before declaring it stuck.
- A first user nudge can appear to do nothing if it lands while the persistent worker session is still compressing, waiting on a huge model request, or otherwise busy. A later nudge may wake the same still-alive session after the busy turn clears. Diagnose this as queued/busy-session behavior before assuming message delivery failed.
