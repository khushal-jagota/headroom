# t_hs01 implementation review resolutions

The independent implementation review raised six actionable findings. All were accepted.

## 1. Shutdown could race an admitted command write

**Finding:** A submit/interrupt could pass `_require_live()`, then shutdown could mark the manager
closed and return before `begin_request()` wrote the frame.

**Resolution:** Shutdown now closes admission first, then acquires every live session's command lock
before marking the manager closed. A command already admitted may finish its ordered write; shutdown
waits for that write. Commands reaching the lock after closing begins fail before writing. Response
waiting remains outside the command lock.

**RED:** A deterministic test paused `begin_request()` after submit admission. The old shutdown
returned while that write was paused, allowing the write after shutdown.

**GREEN:** Shutdown remains blocked until the admitted write is released, that uncertain submit is
settled, a later interrupt is rejected, and Hermes receives only the admitted submit frame.

## 2. Resume snapshot could overwrite newer buffered observations

**Finding:** Existing stored-key rebind replayed buffered events and then applied
`snapshot.running=False`, incorrectly erasing a newer buffered `message.start`.

**Resolution:** Rebind reconciles the resume snapshot first, then replays buffered events in ordered
stdout sequence so newer observations win.

**RED/GREEN:** A pre-bind start plus idle resume snapshot changed from incorrectly inactive to active
after replay.

## 3. Same-session Stop proof depended on a short submit timeout

**Finding:** The original test could not prove the interrupt write preceded submit timeout.

**Resolution:** The submit response is now withheld behind a 30-second timeout. The test proves the
interrupt frame is written and acknowledged while the submit thread is still alive, then manager
shutdown settles the withheld request as unknown.

## 4. Session observation demultiplexing lacked direct no-cross-delivery proof

**Finding:** Low-level feed ordering and two-session response independence were covered, but logical
session observation lanes were not directly asserted against cross-delivery.

**Resolution:** An interleaved four-event stream now proves each session receives only its two events
in order, preserves global sequence numbers, and has no extra observation afterward.

## 5. Generated Python cache artifacts were present

**Finding:** `src/planner/minds/sessions/__pycache__` existed in the worktree.

**Resolution:** Generated `.pyc` files and the cache directory were removed. They are removed again
after verification because Python test execution recreates ignored bytecode caches.

## 6. Unrelated completion could resolve a timed-out submission

**Finding:** Every `message.complete` decremented the sole pending count. After submit timeout, an old
unrelated completion could therefore clear the unknown consequence and permit dormancy.

**Resolution:** Known pending and transport-unknown consequences are counted separately. Timeout or
uncertain write moves the registered consequence to the unknown count. Ordered completions may
resolve only known pending consequences; they never guess that an unknown attempt is terminal.

**RED/GREEN:** After timeout, an unrelated interrupted completion previously reduced the count to
zero. It now leaves one unknown consequence, keeps the session observationally active, and prevents
dormancy.

## Focused resolution proof

```sh
PYTHONPATH=src /Users/khushaljagota/.hermes/planning-v2/.venv/bin/pytest -q \
  tests/unit/test_minds.py tests/unit/test_minds_sessions.py
```

Result: `53 passed`.

Ruff passed for every touched implementation/test path. Mypy passed for the six focused source/test
targets. `git diff --check` passed. The post-review full `./verify` result is recorded in the
implementation report.

The final independent Codex follow-up inspected the resolved implementation and tests against the
ticket and reviewed plan. Its result was:

```text
NO VIOLATIONS
```
