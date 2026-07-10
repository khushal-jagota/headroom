# t_hs01 implementation report

## Outcome

Implemented the reviewed expand slice without migrating existing chat or employee callers.

- `GatewayChild` now retains every session-scoped event in one child-wide ordered feed from
  construction and permits exactly one consumer to claim it. Legacy per-session drains remain.
- JSON-RPC request registration/write is separate from request-ID-correlated response waiting;
  `request()` remains the compatibility wrapper.
- One `LiveSessionManager` per `SharedGateway` demultiplexes the feed into stored/live session
  bindings, retains pre-bind events, preserves native submit dispositions, and separates interrupt
  acknowledgement from later lifecycle observations.
- Session command locks cover registration/write only. Same-session Stop can be written while a
  prior submit response is still outstanding; different sessions do not share response waits.
- Dormancy requires observed idle and no pending consequence. Resume rebinds the stored key to the
  new live ID. Timeout is per-attempt unknown; child death is separately observable as offline and
  never retries.
- `SharedGateway` binds create/resume/history results before returning and shuts down the manager
  before its child. Existing constructor, product methods, topology, and old drains are unchanged.

No Hermes, server wiring, chat/runtime, database, frontend, or transcript code changed.

## RED-to-GREEN evidence

Tests were added one vertical slice at a time at the reviewed gateway and live-session seams.
Observed RED failures included:

- missing `claim_session_event_ingress()` and `begin_request()`;
- missing `planner.minds.sessions`;
- missing `submit()`, `interrupt()`, and `detach_if_dormant()`;
- RPC rejection incorrectly normalized as transport-unknown;
- resume retaining the obsolete live ID;
- pre-bind start observations not updating observed-active state;
- `SharedGateway` not exposing its bound live session;
- child death racing the manager router before `offline` became observable.

Each slice passed its focused command before the next slice. Final focused run:

```sh
PYTHONPATH=src /Users/khushaljagota/.hermes/planning-v2/.venv/bin/pytest -q \
  tests/unit/test_minds.py tests/unit/test_minds_sessions.py
```

Result before independent review: `50 passed`.

Focused quality gates:

```sh
/Users/khushaljagota/.hermes/planning-v2/.venv/bin/ruff check \
  src/planner/minds/contracts.py src/planner/minds/gateway.py \
  src/planner/minds/shared_gateway.py src/planner/minds/sessions \
  tests/unit/test_minds.py tests/unit/test_minds_sessions.py
```

Result: `All checks passed!`

```sh
PYTHONPATH=src /Users/khushaljagota/.hermes/planning-v2/.venv/bin/mypy \
  src/planner/minds/contracts.py src/planner/minds/gateway.py \
  src/planner/minds/shared_gateway.py src/planner/minds/sessions \
  tests/unit/test_minds_sessions.py
```

Result: `Success: no issues found in 6 source files`.

`git diff --check` passed.

## Full verification history

The first worktree `./verify` run failed during unit collection because the temporary shared
virtualenv's editable install pointed at the main worktree. Ruff, Mypy, build, frontend, and all 56
E2E tests passed. The run ended `VERIFY: FAIL`; this was an invalid worktree source-path setup, not a
product failure.

The second run explicitly placed this worktree's `src` first on `PYTHONPATH`. It found one real race:
350 unit tests passed and the child-death assertion failed because the request waiter observed child
death just before the router applied its offline sentinel. The public `offline`/`active` observation
was fixed to treat the child death flag as immediately authoritative while the router completes
shutdown. Focused regression, Ruff, and Mypy then passed.

After that code change, the corrected full command passed 351 unit tests and 56 E2E tests. A final
acceptance audit then found that manager shutdown stopped ingress but did not directly wake an
already-written request until child shutdown or its timeout. A new RED test left the submit thread
alive after manager shutdown. Request handles are now tracked and cancelled as attempt-unknown when
the manager closes; the focused test then passed while proving the child was still alive and no retry
was sent.

Because that changed production code, the pre-review authoritative full command was run again:

```sh
PYTHONPATH="$PWD/src" ./verify
```

Result:

```text
[verify] gate ruff: ok
[verify] gate mypy: ok
[verify] gate unit suite: ok
[verify] gate build check: ok
[verify] gate frontend: ok
[verify] gate e2e suite: ok

VERIFY: PASS
```

Counts: 352 unit tests and 56 E2E tests passed. The three existing Svelte warnings and existing
Python test warnings remained warnings only.

## Independent implementation review

The reviewer raised six findings, all accepted and resolved test-first: shutdown/write admission,
snapshot-versus-buffer replay order, stronger withheld-response Stop proof, explicit two-session
observation demultiplexing, generated cache cleanup, and separate accounting for transport-unknown
submissions. Full dispositions and focused proof are in `implementation-review.md`.

Those production fixes required one final post-review full run with the same command. Result: every
gate passed, 355 unit tests and 56 E2E tests passed, and the final line was `VERIFY: PASS`.

## Handoff

No files outside the ticket's permitted implementation/test/report scope were changed by this
implementation. No staging or commit was performed. Independent implementation review remains the
integrator's next pipeline step.
