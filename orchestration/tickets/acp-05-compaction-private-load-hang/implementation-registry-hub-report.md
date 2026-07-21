# ACP-05 registry and hub implementation report

## Outcome

The registry/hub lane now implements the corrected fresh-child compaction topology.

- Per-employee lifecycle mutation is separate from publication/update quiescence.
- The source child performs `session/fork`; a newly initialized unpublished child privately loads the
  fork and owns the replacement runtime generation and record identity.
- Repository CAS and resolution, ACP lifecycle calls, response barriers, and child shutdown all run
  outside the publication/update gate.
- Normal N+1 publication waits for admitted N public sinks, installs the fresh child only after the
  durable candidate wins, and retires N after publication.
- Pre-CAS failure invalidates the mutated source generation. There is no same-child restore path or
  `original restore` diagnostic phase.
- Ambiguous CAS resolves the durable binding. An exact external N+1 winner is loaded into another fresh
  child; malformed or skipped-generation winners fail the source generation instead of being adopted.
- The hub owns one aggregate-bounded transition FIFO retaining source identity, session ID, and typed
  notification. It admits both legal candidate origins, drains winner entries once between replay and
  `ready`, discards losers, and clears synchronously on abort, fatal failure, expiry, shutdown, and final
  settlement.
- Registry shutdown now closes and clears both sides of an outstanding prepared capture and releases its
  lifecycle reservation.
- The existing 300-second capture budget and exact deadline diagnostics remain unchanged.

## Test-driven evidence

The topology regression first failed because the source child received the private fork load. After the
change, the source records only the fork and the fresh child records the private load and N+1 publication.

The gate regression first left an ordinary N publication task pending while candidate private load was
held. After the split, the ordinary N sink completes while lifecycle mutation remains reserved.

The hub regression first produced no candidate envelopes because both pre-publication origins were
dropped. After the transition FIFO, two attached browsers observe the exact order:

`ordinary N -> reset -> private replay -> source-origin winner update -> replacement-origin winner update -> ready -> human echo -> queue snapshot -> later ordinary N+1`

Additional focused proof covers normalization and durable-CAS generation fatality, prepared-capture
shutdown, exact external-winner validation, and exact-session private response-epoch integration.

## Verification

Owned unit suites:

```text
$ .venv/bin/pytest -q --tb=short tests/unit/test_acp_employee_registry.py tests/unit/test_conversation_hub.py tests/unit/test_conversation_turn_broker.py tests/unit/test_acp_conversation_composition.py
........................................................................ [ 62%]
...........................................                              [100%]
115 tests collected; all passed
```

Owned official-SDK e2e suite:

```text
$ .venv/bin/pytest -q --tb=short tests/e2e/test_acp_conversation.py
..............                                                           [100%]
14 passed
```

The automatic queued-successor e2e initially exposed a stale same-process test assumption: every child
inherited one UDP release address, so the fresh candidate process could not bind it while N was alive.
The owned harness now assigns source and replacement generations distinct release addresses. Its isolated
proof passes before the full e2e run:

```text
$ .venv/bin/pytest -q --tb=short tests/e2e/test_acp_conversation.py::test_automatic_official_fork_retargets_and_runs_queued_successor
.                                                                        [100%]
```

Scoped lint:

```text
$ .venv/bin/ruff check src/planner/conversation/runtime_ports.py src/planner/conversation/employee_registry.py src/planner/conversation/hub.py tests/unit/test_acp_employee_registry.py tests/unit/test_conversation_turn_broker.py tests/unit/test_conversation_hub.py tests/e2e/test_acp_conversation.py
All checks passed!
```

Strict affected-source typing:

```text
$ .venv/bin/mypy --strict src/planner/conversation/runtime_ports.py src/planner/conversation/employee_registry.py src/planner/conversation/hub.py
Success: no issues found in 3 source files
```

The only test warning was the pre-existing Starlette `TestClient`/`httpx` deprecation warning.
Canonical `./verify`, server restart, generated distribution, Computer Use, and Codex review were not run;
they remain root-owned by the dispatch.

## Files changed by this lane

- `src/planner/conversation/runtime_ports.py`
- `src/planner/conversation/employee_registry.py`
- `src/planner/conversation/hub.py`
- `tests/unit/test_acp_employee_registry.py`
- `tests/unit/test_conversation_turn_broker.py`
- `tests/unit/test_conversation_hub.py`
- `tests/e2e/test_acp_conversation.py`
- this report

No ingress-owned file was modified.
