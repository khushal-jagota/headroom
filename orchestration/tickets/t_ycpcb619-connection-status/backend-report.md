# Backend heartbeat slice report

## Changed files

- `src/planner/core/config.py`
- `src/planner/core/server.py`
- `src/planner/core/ws.py`
- `tests/unit/test_config.py`
- `tests/unit/test_server_events.py`
- `orchestration/tickets/t_ycpcb619-connection-status/backend-report.md`

No frontend source, browser tests, docs, generated assets, `PROGRESS.md`, `decisions.md`, or commits were touched.

## RED evidence

Command:

```sh
PYTHONPATH=/private/tmp/panels-t_ycpcb619/src .venv/bin/pytest tests/unit/test_config.py::test_config_defaults_expose_only_live_runtime_knobs tests/unit/test_config.py::test_ws_heartbeat_ms_can_be_overridden_by_environment tests/unit/test_server_events.py -q
```

Result:

```text
FFFFFF                                                                   [100%]
FAILED tests/unit/test_config.py::test_config_defaults_expose_only_live_runtime_knobs
FAILED tests/unit/test_config.py::test_ws_heartbeat_ms_can_be_overridden_by_environment
FAILED tests/unit/test_server_events.py::test_meta_serves_event_stream_heartbeat_cadence
FAILED tests/unit/test_server_events.py::test_tail_events_sends_quiet_heartbeat_without_advancing_cursor
FAILED tests/unit/test_server_events.py::test_tail_events_preserves_event_batches_and_heartbeats_at_current_cursor
FAILED tests/unit/test_server_events.py::test_tail_events_disconnect_exits_promptly_during_quiet_period
```

Representative failures:

```text
AttributeError: 'Config' object has no attribute 'ws_heartbeat_ms'
KeyError: 'ws_heartbeat_ms'
TypeError: tail_events() got an unexpected keyword argument 'ws_heartbeat_ms'
```

## GREEN evidence

Initial focused GREEN after production code:

```sh
PYTHONPATH=/private/tmp/panels-t_ycpcb619/src .venv/bin/pytest tests/unit/test_config.py::test_config_defaults_expose_only_live_runtime_knobs tests/unit/test_config.py::test_ws_heartbeat_ms_can_be_overridden_by_environment tests/unit/test_server_events.py -q
```

```text
......                                                                   [100%]
```

Final focused backend regression GREEN:

```sh
PYTHONPATH=/private/tmp/panels-t_ycpcb619/src .venv/bin/pytest tests/unit/test_config.py tests/unit/test_server_events.py tests/unit/test_trusted_ingress.py::test_events_websocket_trusted_ingress_and_origin_policy tests/unit/test_trusted_ingress.py::test_events_websocket_rejects_duplicate_tailscale_login tests/unit/test_trusted_ingress.py::test_events_websocket_rejects_duplicate_origin -q
```

```text
....................                                                     [100%]
```

## Ruff evidence

First Ruff run found one test-only unused import:

```text
F401 [*] `sqlite3` imported but unused
```

After removing it:

```sh
PYTHONPATH=/private/tmp/panels-t_ycpcb619/src .venv/bin/ruff check src/planner/core/config.py src/planner/core/server.py src/planner/core/ws.py tests/unit/test_config.py tests/unit/test_server_events.py
```

```text
All checks passed!
```

## Behavior implemented

- Added explicit `ws_heartbeat_ms` config, defaulting to `15000`, overrideable through `PLAN_WS_HEARTBEAT_MS`.
- Served `ws_heartbeat_ms` from `GET /api/meta`.
- Passed the cadence into `/api/events`.
- `tail_events` now sends quiet heartbeat envelopes shaped as `{"events": [], "cursor": current_cursor}`.
- Heartbeats do not advance the cursor.
- Non-empty event batches still advance the cursor and are sent unchanged before later quiet heartbeats.
- Disconnect cleanup still exits promptly while the tailer is quiet.
