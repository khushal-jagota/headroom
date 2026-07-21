# ACP-05 websocket writer cleanup

## Why this ticket exists

Real browser reloads during ACP dogfood repeatedly made asyncio report destroyed pending
`Queue.get()` and `Event.wait()` tasks. The websocket writer creates those two waits for each output
iteration and cleans up the loser after a normal first completion, but external writer cancellation
can interrupt the enclosing `asyncio.wait` before either child is cancelled or awaited.

## Frozen behavior

1. Every writer iteration owns both wait tasks through a `finally` path. Normal queue delivery,
   subscription closure, websocket send failure, and external writer cancellation cancel and await
   every unfinished wait before returning or propagating.
2. Writer cancellation retains normal task-cancellation semantics; it is not translated into a
   transport error and does not send a second close frame.
3. Existing websocket disconnect, subscription detach, close code/reason, FIFO delivery, and
   slow-consumer behavior remain unchanged.

## Required proof

- A focused hub regression cancels the writer while both waits are pending and proves both finish;
  no pending task survives the writer.
- Existing conversation hub and websocket suites pass with scoped Ruff and strict Mypy.
- Do not run canonical `./verify`; ACP-10 owns the one final run.

## Allowed files

- `src/planner/conversation/hub.py`
- `tests/unit/test_conversation_hub.py`
- this ticket's report/review/evidence files
- `PROGRESS.md`
- `decisions.md`

No wire, browser, registry, broker, composition, generated distribution, config, runtime database,
or Hermes-checkout file is in scope.
