# Backend implementation report

## Changed

- Added typed display-safe `ChatActivityObservation` / `ChatActivityEntry` contracts and attached ordered entries to the active `ChatTurn`.
- Added `chat_turn_activity_entries` with foreign-key cascade, identity uniqueness, stable order, and a named 100-entry cap.
- Added one framework-free Hermes activity normalizer and routed both worker observations and human stream chunks through the canonical activity writer.
- SharedGateway now emits structured activity chunks while retaining the text label for compatibility.
- Turn completion, interruption, and failure delete the transient entries; deleting chat turns cascades them.

## RED

Initial persistence/producer run:

`PYTHONPATH=src /Users/khushaljagota/.hermes/planning-v2/.venv/bin/python -m pytest tests/unit/test_chat_activity.py -q`

Result: `7 passed, 2 failed`. The worker path retained raw legacy label `terminal` instead of normalized `Ran terminal`; the human stream path persisted no typed entries.

Shared gateway transport regression:

`PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_minds.py::test_shared_gateway_streams_structured_display_safe_activity -q`

Result: failed because all activity chunks had `activity=None`. The fixture's private reasoning frame was corrected after source inspection showed the session manager intentionally does not route that non-lifecycle event to an accepted consequence; tool start/end remained the actual transport contract under test.

Command normalization regression:

`PYTHONPATH=src .venv/bin/python -m pytest tests/unit/test_chat_activity.py::test_normalizer_maps_supported_hermes_events_without_raw_payload_fields -q`

Result: failed because `command.start` returned `None`.

## GREEN

- `tests/unit/test_chat_activity.py` plus the structured SharedGateway test: `10 passed`.
- Combined chat, command, gateway, database, and ticket-delete regression command: `144 passed` with one existing Starlette/httpx deprecation warning.
- `git diff --check`: clean before review.

## Decisions

Only category, short label, lifecycle, stable identity, order, and timing are persisted. `tool.delta` is ignored; identity-bearing start/end aliases update one row. Terminal tool calls render as commands. Exact `command.start` labels and identity are supported without reading arguments.

## Final gates

Independent review findings were resolved with focused regressions; the final safety follow-up returned
`NO VIOLATIONS`. Canonical `./verify` passed Ruff, Mypy across 106 source files, 419 unit tests,
compile/static and frontend gates, and 59 browser tests, ending with `VERIFY: PASS`.
