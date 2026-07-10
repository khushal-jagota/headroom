# t_new01 — Native fresh-session command

Reference: `src/planner/chat/contracts.py` (`CommandRunResult`, `ChatStreamChunk`),
`src/planner/minds/shared_gateway.py`, and D76–D80's ordered-ingress contract.

## Outcome

Typing `/new` in Chief chat completes promptly, persists one new durable Hermes session key, and the
next ordinary message is submitted to that new session's live handle and can produce a reply.

## Contract

1. Recognize the exact `/new` command in both synchronous and streaming command entry points before
   resuming or creating the ordinary command session. Arguments are out of scope; `/new title` keeps
   the existing generic command path until title semantics are deliberately designed.
2. Create one Hermes session through `session.create` with the ordinary chat source and bind its
   returned `stored_session_id` to its returned live `session_id` in the shared session manager.
3. Call the existing session-key callback with the new durable key and return that key in the command
   result/done chunk. The visible result is the system line `New session started.`
4. Do not call `session.resume`, `slash.exec`, `command.dispatch`, or `prompt.submit` for `/new`.
   Do not create a throwaway session when no prior key exists.
5. The next ordinary message using the returned key must reuse the bound live session and submit to
   the new live handle. Existing ordered-ingress, no-retry, and consequence ownership rules remain.
6. No chat contract shape, database schema, or upstream Hermes source changes.

## Owned files

- `src/planner/minds/shared_gateway.py`
- `tests/unit/test_minds.py`
- `tests/unit/test_chat_seed.py` only if the service persistence seam needs extra coverage
- `docs/chat.md`

## Acceptance

- A focused fake-gateway regression drives `/new` then an ordinary message and proves the exact RPC
  sequence and new live handle.
- A no-prior-key case proves only one session is created and only the new key is surfaced.
- Existing command, key-rotation, ordered-ingress, and chat tests remain green.
