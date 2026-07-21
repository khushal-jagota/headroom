# ACP-05 websocket writer cleanup implementation report

## Outcome

The writer now owns both per-iteration wait tasks through one `finally` block. External writer
cancellation, send failure, subscription closure, and ordinary queue delivery all cancel unfinished
waits and gather both tasks before the writer returns or propagates.

This is a small orchestrator-authored dogfood repair: one production control-flow change and one
deterministic regression. No protocol, browser, registry, broker, composition, generated distribution,
config, runtime database, or Hermes-checkout file changed.

## TDD evidence

Before the production change, the focused regression failed because the queue wait remained pending:

```text
$ .venv/bin/pytest -q tests/unit/test_conversation_hub.py::test_websocket_writer_cancellation_awaits_both_iteration_waits
F                                                                        [100%]
E       assert False
E        +  where False = is_set()
E        +    where is_set = <asyncio.locks.Event object ... [unset]>.is_set
E        +      where <asyncio.locks.Event object ... [unset]> = <_CancellationObservedQueue ... _getters[1]>.finished
```

After the `finally` ownership correction:

```text
$ .venv/bin/pytest -q tests/unit/test_conversation_hub.py::test_websocket_writer_cancellation_awaits_both_iteration_waits
.                                                                        [100%]

$ .venv/bin/ruff check src/planner/conversation/hub.py tests/unit/test_conversation_hub.py
All checks passed!

$ .venv/bin/mypy --strict src/planner/conversation/hub.py
Success: no issues found in 1 source file

$ .venv/bin/pytest -q tests/unit/test_conversation_hub.py tests/unit/test_acp_conversation_websocket.py
..............                                                           [100%]
```

The complete affected hub/websocket slice is 14/14. One independent focused review remains; canonical
`./verify` stays reserved for ACP-10.
