# W1 — Implementation plan: `src/planner/minds/` (agent-operation primitive)

Blueprint for the implementer. Everything here is derived from `ticket.md` (law),
`orchestration/runtime-redesign/notes.md` (Agent runtime / Scheduling & runs),
`orchestration/runtime-redesign/spikes/01-hermes-linkage.md` (spike evidence), and direct
read-only verification of `~/.hermes/hermes-agent/tui_gateway/` source on 2026-07-06. All
`server.py` / `entry.py` / `transport.py` line numbers below were verified against that source.
No further research should be needed.

---

## 0 · Protocol ground truth (verified, cited)

Everything the code relies on, with the source line that proves it:

| Fact | Source |
|---|---|
| Framing: one JSON object per line; `json.dumps(obj) + "\n"`, flushed | `transport.py:137` (StdioTransport.write), flush at `transport.py:165-178` |
| First frame is the `gateway.ready` event, **no `session_id` key**, payload `{"skin": ...}` | `entry.py:316-322` |
| Main loop is `for raw in sys.stdin`; blank lines skipped | `entry.py:324-327` |
| Unparseable line → `{"jsonrpc":"2.0","error":{"code":-32700,"message":"parse error"},"id":null}` (uncorrelatable — id null) | `entry.py:329-335` |
| stdin EOF → clean exit 0 (`[gateway-exit] stdin EOF` on stderr) | `entry.py:344`, spike §1 line 47 |
| Success response `{"jsonrpc":"2.0","id":rid,"result":{...}}`; error `{"jsonrpc":"2.0","id":rid,"error":{"code":int,"message":str}}` | `server.py:867-872` (`_ok`/`_err`) |
| Event frame `{"jsonrpc":"2.0","method":"event","params":{"type":...,"session_id":...,"payload":{...}}}`; **`payload` key OMITTED when payload is None** | `server.py:803-807` (`_emit`) |
| `message.start` is emitted with **no payload key** | `server.py:6512,6555,6592` (`_emit("message.start", sid)`) |
| `message.delta` payload `{"text": str}` | `server.py:6718` |
| Long handlers run on a 4-thread pool; `dispatch` returns `None` for them and the pool worker writes the response later → **responses can arrive out of order and interleave arbitrarily with events**; correlation MUST be by `id` | `server.py:170-188` (`_LONG_HANDLERS`, includes `session.resume`), `server.py:191-201` (pool), `server.py:914-950` (dispatch), `entry.py:337-342` |
| Handler exception → `-32000 "handler error: ..."`; bad request → `-32600`/`-32602`; unknown method → `-32601` | `server.py:944`, `server.py:883-899`, `server.py:908-911` |
| `session.create` result: `{"session_id": <8-hex live handle>, "stored_session_id": <durable YYYYMMDD_HHMMSS_xxxxxx>, "message_count", "messages", "info": {...}}`; slot limit → `4090` | `server.py:4303-4433` (sid `uuid4().hex[:8]` at 4305, result at 4413-4419, 4090 at 4355-4357) |
| `session.create` writes **no DB row**; the row appears lazily on first prompt | `server.py:4393-4398` |
| `session.resume {"session_id": <stored key>}` → `{"session_id": <NEW live handle>, "resumed": <durable key>, "message_count", "messages", ...}`; unknown key → `4007 "session not found"`; resume **follows the compression chain** so `resumed` may be a *newer* durable key than the one passed | `server.py:4555-4620` (4007 at 4599, chain at 4612-4619), `payload["resumed"]` at 4633/4725 |
| `prompt.submit {"session_id": <live sid>, "text": ...}` → inline `{"status":"streaming"}`; busy → `4009 "session busy"`; the run itself happens on a daemon thread after the response | `server.py:6318-6384` (4009 at 6331-6332, result at 6384) |
| Exactly one `message.complete` per run: payload `{"text": str, "usage": {...}, "status": "complete"|"interrupted"|"error"}` + optional `"reasoning"`/`"warning"`/`"rendered"` | `server.py:6770-6805` (status at 6771-6775, payload at 6795-6802, emit at 6805) |
| Agent-init failure (bad `HERMES_TUI_SKILLS`, missing provider) arrives as an `error` **event** `{"message": "agent init failed: ..."}` — possibly before/without any prompt response | `server.py:1094`, `server.py:6365-6380`, spike §1 lines 110-113, 138-141 |
| Other events in play: `session.info`, `thinking.delta`, `reasoning.available`, `tool.start`/`tool.complete`/`tool.generating`, `status.update` | `server.py:2826/2983/3030/3224/3048-3052/825-838` |
| Stray library prints can't corrupt stdout: gateway redirects Python stdout to stderr; real stdout is JSON-only | `server.py:203-207` |
| Child env: `HERMES_PYTHON_SRC_ROOT` (sys.path guard), `HERMES_TUI_SKILLS` (role skill(s), read at every agent build in that process), `HERMES_HOME` (planner home) | `entry.py:4-12`, spike §2 (Q2), spike §1 isolation probe |
| Spawn: `<hermes_python> -m tui_gateway.entry`, PIPE stdin/stdout/stderr, text mode, line-buffered | spike §1 lines 24-29 |
| Error codes in play: `-32700` parse, `-32600`/`-32601`/`-32602`, `-32000`, `4007` not found, `4009` busy, `4023` delete-while-active, `4090` slot limit | verified above + spike §2 Q1 |

---

## 1 · Design decisions (the load-bearing choices)

**D1 — Injection seam = a `ChildProcess` Protocol + a `SpawnFn` spawn hook.**
`gateway.py` defines a `ChildProcess` `typing.Protocol` (blocking line-oriented stdout/stderr
reads, a stdin line write, close/kill/wait) and a `SpawnFn = Callable[[list[str], dict[str, str]],
ChildProcess]`. `GatewayChild` takes `spawn: SpawnFn = spawn_popen`; `run_step` and
`boot_smoke_check` thread the same parameter through. The fake (`fake.py`) implements
`ChildProcess` and offers itself via `fake.spawn`. Consequence: the fake replaces ONLY the OS
process; every frame it produces flows through GatewayChild's real reader thread, router,
pending-request table, and event queue — the exact code path a real child exercises. Nothing in
runner/queue knows whether the child is real.

**D2 — One reader thread per child; responses by id-keyed pending table; events on a Queue.**
A single daemon thread reads stdout lines and routes: response frames resolve entries in a
lock-guarded `dict[int, _Pending]`; event frames go onto an unbounded `queue.Queue`;
`gateway.ready` sets a ready gate instead of being enqueued (it is process-level, not
session-level, and has no `session_id` — entry.py:316-322). EOF fails all pending waiters, wakes
ready-waiters, and enqueues a `None` sentinel so an event drainer wakes. A second daemon thread
tails stderr into a bounded deque.

**D3 — Queue key is a caller-owned, required, non-empty string; `None` session_key never reaches
the queue.** `MindQueue.submit(key: str, item)` — the key is the *mind's* stable identity (wave 3
will pass e.g. `"ticket:17"` or `"global"`), not the Hermes session key. Justification: the
serialization property must hold from step 0, when no durable session key exists yet — two queued
step-0 producers for the same ticket MUST serialize (both would carry `session_key=None`, so
keying on session_key cannot distinguish "same mind" from "different mind"). Treating each `None`
as a unique bucket would permit exactly the concurrent-`session.create` race the queue exists to
prevent (notes.md:99-106 — the gateway busy-guard is per-process only, our queue is the
load-bearing correctness); lumping all `None`s into one bucket would falsely serialize unrelated
minds. Only the caller knows mind identity, so the key is its input. `session_key: str | None`
travels *inside* the queued item and is consumed by `run_step`.

**D4 — Queue injects the run callable; the queue is pure serialization.**
`MindQueue[T]` is generic over the item type and takes `run: Callable[[str, T], None]` at
construction. Result delivery, status stamping, retries — all belong to the injected callable
(wave 3's System B closure). Queue unit tests inject a controllable stub (Events + counters), so
they never touch the fake gateway, let alone a real one.

**D5 — No overall run wall-clock timeout.** The event drain in `run_step` blocks indefinitely by
design (notes.md:184-185 REMOVE timeout-as-failure). Every terminal outcome has a structural wake:
`message.complete`, an `error` event, or child EOF (the sentinel). The two *protocol handshake*
phases keep injectable timeouts with sane defaults — `ready_timeout=10.0` (spike §4a) and
`request_timeout=60.0` — because a gateway that never answers `gateway.ready` or an RPC is a
transport failure, not a long-but-healthy run. Liveness-blind hangs are the PARKED reclaim
question (notes.md:196-198), out of scope.

**D6 — Smoke script lives at `src/planner/minds/smoke.py` with a `__main__` guard.**
Rationale: `mypy` `files=["src"]` (pyproject.toml:42) type-checks it strictly alongside the
package it exercises (scripts/ is outside mypy scope), `pytest` `testpaths=["tests"]`
(pyproject.toml:47) never collects anything under src/ regardless of name, and the verify
skip-scan only walks `tests/` (scripts/verify.py:119). Run as
`.venv/bin/python -m planner.minds.smoke`. It is NOT imported by `__init__.py`.

**D7 — `RunResult.session_key` always carries the freshest durable key the gateway reported.**
Create path → `stored_session_id`; resume path → the response's `resumed` field (which may be a
*newer* key than the one passed, because resume follows the compression-continuation chain,
server.py:4612-4619 — spike sub-Q3). Failures before any key is learned return the input
`session_key` unchanged (`None` on a failed create path). Notably: a `4009` on submit after a
successful create still returns the new `stored_session_id`, so the caller can persist it.

**D8 — `on_event` receives every event params-dict after ready, including terminal ones and
unknown types.** One uniform rule: whatever the reader enqueued (the `params` dict:
`{"type", "session_id"?, "payload"?}`) is forwarded before the runner acts on it. Consumers
filter. Unknown event types are forwarded and otherwise ignored — never a crash. `on_event`
exceptions are caught and logged; an observer must not kill a run.

---

## 2 · File: `src/planner/minds/__init__.py`

```python
"""minds — the agent-operation primitive (W1).

Drives one Hermes "mind" (a durable gateway session) over the stdio JSON-RPC
gateway: spawn a child per run, create/resume the session, submit one prompt,
observe the single message.complete. Not wired into the server, dispatcher,
DB, or CLI this wave.
"""

from __future__ import annotations

from planner.minds.config import (
    boot_smoke_check,
    hermes_src_root,
    resolve_hermes_python,
    resolve_planner_home,
)
from planner.minds.gateway import (
    ChildProcess,
    GatewayChild,
    GatewayError,
    GatewayRpcError,
    SpawnFn,
    spawn_popen,
)
from planner.minds.queue import MindQueue
from planner.minds.runner import RunResult, RunStatus, run_step

__all__ = [
    "ChildProcess",
    "GatewayChild",
    "GatewayError",
    "GatewayRpcError",
    "MindQueue",
    "RunResult",
    "RunStatus",
    "SpawnFn",
    "boot_smoke_check",
    "hermes_src_root",
    "resolve_hermes_python",
    "resolve_planner_home",
    "run_step",
    "spawn_popen",
]
```

`fake` and `smoke` are deliberately not re-exported (test support / human tool); tests import
`planner.minds.fake` directly. Keep `__all__` sorted (ruff-friendly).

---

## 3 · File: `src/planner/minds/gateway.py`

### Public API (exact)

```python
"""GatewayChild: one Hermes tui_gateway child over newline-delimited JSON-RPC.

Frame router: a single reader thread routes JSON-RPC responses (keyed by id)
to per-request waiters and event frames onto an internal queue. Request ids
are integers allocated from 1, incrementing — a documented contract the tests
rely on. Responses may arrive out of order (the gateway runs long handlers on
an internal thread pool); correlation is strictly by id.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Mapping
from typing import Any, Final, Protocol

JsonDict = dict[str, Any]

READY_TIMEOUT_DEFAULT: Final = 10.0
REQUEST_TIMEOUT_DEFAULT: Final = 60.0
SHUTDOWN_GRACE_DEFAULT: Final = 2.0
STDERR_TAIL_LINES: Final = 100


class GatewayError(Exception):
    """Transport-level failure: spawn failure, child death, ready/request timeout."""


class GatewayRpcError(GatewayError):
    """The gateway answered with a JSON-RPC error frame."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"gateway rpc error {code}: {message}")
        self.code = code
        self.message = message


class ChildProcess(Protocol):
    """What GatewayChild needs from a spawned gateway child (the injection seam)."""

    def send(self, line: str) -> None: ...
    def read_stdout(self) -> str | None: ...
    def read_stderr(self) -> str | None: ...
    def close_stdin(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int | None: ...


SpawnFn = Callable[[list[str], dict[str, str]], ChildProcess]


def spawn_popen(argv: list[str], env: dict[str, str]) -> ChildProcess: ...


class GatewayChild:
    def __init__(
        self,
        hermes_python: str,
        env: Mapping[str, str],
        *,
        spawn: SpawnFn = spawn_popen,
        stderr_tail_lines: int = STDERR_TAIL_LINES,
    ) -> None: ...
    def wait_ready(self, timeout: float = READY_TIMEOUT_DEFAULT) -> None: ...
    def request(
        self,
        method: str,
        params: JsonDict | None = None,
        *,
        timeout: float = REQUEST_TIMEOUT_DEFAULT,
    ) -> JsonDict: ...
    def next_event(self, timeout: float | None = None) -> JsonDict | None: ...
    def stderr_tail(self) -> list[str]: ...
    def shutdown(self, grace: float = SHUTDOWN_GRACE_DEFAULT) -> None: ...
    @property
    def alive(self) -> bool: ...
```

### `ChildProcess` protocol semantics (contract the fake must honor)

- `send(line)` — write one line (caller includes the trailing `"\n"`) to child stdin and flush.
  Raises `BrokenPipeError`/`OSError` when the pipe is gone or stdin was closed.
- `read_stdout()` / `read_stderr()` — block for one line (with or without trailing newline —
  GatewayChild strips); return `None` at EOF, and keep returning `None` thereafter.
- `close_stdin()` — idempotent; never raises for an already-closed pipe.
- `kill()` — hard-kill; idempotent.
- `wait(timeout)` — return the exit code, or `None` if still running when `timeout` elapses
  (wrap `subprocess.TimeoutExpired` — do not let it escape).

### `PopenChild` (the real implementation, module-private class + `spawn_popen`)

```python
subprocess.Popen(
    argv,
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    text=True, bufsize=1, env=env,
)
```
(matches spike §1 lines 24-29; no cwd override — entry.py:4-12's `HERMES_PYTHON_SRC_ROOT` guard
plus its `''`/`'.'` strip make cwd shadowing a non-issue; a per-run workspace cwd is a wave-3
concern). `read_stdout`: `line = proc.stdout.readline()` → `""` means EOF → return `None`, else
return `line`. `close_stdin`: `proc.stdin.close()` inside `try/except OSError/ValueError`.
`wait`: `try: return proc.wait(timeout) except subprocess.TimeoutExpired: return None`.
mypy note: `proc.stdout`/`proc.stdin` are `IO[str] | None` — assert non-None once in `__init__`
(they are PIPEs by construction) and store typed references.

### `GatewayChild` internals

State:
- `self._child: ChildProcess` — spawned in `__init__` via
  `spawn([hermes_python, "-m", "tui_gateway.entry"], dict(env))`. Wrap spawn `OSError` in
  `GatewayError(f"failed to spawn gateway child: {exc}")`.
- `self._pending: dict[int, _Pending]` + `self._lock: threading.Lock` + `self._next_id: int = 1`.
  `_Pending` is a tiny module-private class: `done: threading.Event`, `frame: JsonDict | None`.
- `self._events: queue.Queue[JsonDict | None]` — event params dicts; `None` = child-died sentinel.
- `self._ready_gate: threading.Event`, `self._ready_seen: bool = False`,
  `self._dead: threading.Event`.
- `self._stderr_lines: deque[str]` with `maxlen=stderr_tail_lines`.
- Two daemon threads started in `__init__`: `minds-gw-stdout`, `minds-gw-stderr`.

Reader loop (`_stdout_loop`):
```
while True:
    line = self._child.read_stdout()
    if line is None: break                      # EOF
    line = line.strip()
    if not line: continue
    try: frame = json.loads(line)
    except json.JSONDecodeError: continue       # tolerate garbage; stdout is JSON-only per server.py:203-207
    if not isinstance(frame, dict): continue
    if frame.get("method") == "event":
        raw = frame.get("params")
        params: JsonDict = raw if isinstance(raw, dict) else {}
        if params.get("type") == "gateway.ready":   # entry.py:316-322 — no session_id
            self._ready_seen = True
            self._ready_gate.set()
            continue
        self._events.put(params)                # ALL other events, known or unknown, enqueue
        continue
    rid = frame.get("id")
    if rid is not None and ("result" in frame or "error" in frame):
        with self._lock:
            pending = self._pending.pop(rid, None)
        if pending is not None:
            pending.frame = frame
            pending.done.set()
    # else: ignore — e.g. -32700 parse-error frames carry id null (entry.py:332), uncorrelatable
self._on_child_dead()
```

`_on_child_dead()` — ordering matters:
1. `self._dead.set()`
2. under `self._lock`: pop every pending entry, set each `done` (its `frame` stays `None` →
   the waiter raises child-died)
3. `self._ready_gate.set()` (wakes `wait_ready`, which checks `_ready_seen` first)
4. `self._events.put(None)` (wakes an event drainer)

Stderr loop: read lines, `rstrip("\n")`, append to `self._stderr_lines` until `None`.
`stderr_tail()` returns `list(self._stderr_lines)` (GIL-safe snapshot of a deque).

`wait_ready(timeout)`:
```
if not self._ready_gate.wait(timeout):
    raise GatewayError(f"gateway.ready not received within {timeout}s; stderr: {self.stderr_tail()!r}")
if not self._ready_seen:      # gate was set by death
    raise GatewayError(f"gateway child exited before ready; stderr: {self.stderr_tail()!r}")
```

`request(method, params=None, *, timeout)`:
```
if self._dead.is_set():
    raise GatewayError(f"gateway child is dead; cannot send {method}")
with self._lock:
    rid = self._next_id; self._next_id += 1
    pending = _Pending(); self._pending[rid] = pending
frame: JsonDict = {"jsonrpc": "2.0", "id": rid, "method": method}
if params is not None:
    frame["params"] = params
try:
    self._child.send(json.dumps(frame) + "\n")
except OSError as exc:                       # BrokenPipeError is an OSError
    with self._lock: self._pending.pop(rid, None)
    raise GatewayError(f"gateway stdin write failed for {method}: {exc}") from exc
if not pending.done.wait(timeout):
    with self._lock: self._pending.pop(rid, None)
    raise GatewayError(f"no response to {method} within {timeout}s")
resp = pending.frame
if resp is None:
    raise GatewayError(f"gateway child died before responding to {method}; stderr: {self.stderr_tail()!r}")
err = resp.get("error")
if isinstance(err, dict):
    raise GatewayRpcError(int(err.get("code", -1)), str(err.get("message", "")))
result = resp.get("result")
return result if isinstance(result, dict) else {}
```
Registering the pending entry BEFORE sending closes the instant-response race (the reader may
resolve it before `wait` is entered — the Event absorbs that). Out-of-order responses are handled
by construction: the table is keyed by id, never "next line is mine".

`next_event(timeout=None)`:
```
try:
    item = self._events.get(timeout=timeout) if timeout is not None else self._events.get()
except queue.Empty:
    raise GatewayError(f"no gateway event within {timeout}s") from None
if item is None:
    self._events.put(None)      # re-arm the sentinel for any later caller
    return None
return item
```
(`None` return = child died / EOF. The runner's drain calls it with `timeout=None` — see D5.
Tests use explicit timeouts so a bug can't hang pytest.)

`shutdown(grace)` — idempotent, never raises:
```
self._child.close_stdin()                    # clean path: entry.py:344 exits on stdin EOF
if self._child.wait(grace) is None:
    self._child.kill()
    self._child.wait(grace)
# reader threads exit on EOF; bounded join for tidiness (daemon threads anyway)
self._stdout_thread.join(timeout=grace)
self._stderr_thread.join(timeout=grace)
```

`alive` property: `not self._dead.is_set()`.

---

## 4 · File: `src/planner/minds/fake.py`

### Purpose

A scriptable stand-in for the *process only*. It implements `ChildProcess`, so GatewayChild's
real reader/router/pending machinery runs unchanged — the fake merely decides which frames appear
on "stdout" in response to each request line. No subprocess, no model calls.

### Public API (exact)

```python
"""Scriptable fake gateway child (test double). Implements the ChildProcess
protocol so runner/queue/gateway tests exercise the real frame router with
no subprocess and no model calls."""

from __future__ import annotations

import json
import queue
import threading
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from planner.minds.gateway import ChildProcess, JsonDict


def ev(type_: str, session_id: str | None = None, payload: JsonDict | None = None) -> JsonDict:
    """Build an event frame exactly as server.py _emit does (server.py:803-807):
    the payload key is OMITTED when payload is None; session_id omitted when None
    (gateway.ready is the only such event — entry.py:316-322)."""


@dataclass(frozen=True)
class Reply:
    result: JsonDict | None = None            # -> {"jsonrpc":"2.0","id":<rid>,"result":...}
    error: tuple[int, str] | None = None      # -> {"jsonrpc":"2.0","id":<rid>,"error":{...}}
    events_before: tuple[JsonDict, ...] = ()  # event frames written BEFORE the response
    events_after: tuple[JsonDict, ...] = ()   # event frames written AFTER the response
    frames: tuple[JsonDict | str, ...] = ()   # raw frames (dict) or raw lines (str), written last
    die: bool = False                          # then: stdout EOF (child death)

    def __post_init__(self) -> None: ...       # ValueError if both result and error given


class FakeGateway:
    def __init__(
        self,
        script: Mapping[str, Sequence[Reply]],
        *,
        ready: bool = True,
        stderr_lines: Sequence[str] = (),
    ) -> None: ...

    # the spawn hook to inject: GatewayChild(..., spawn=fake.spawn)
    def spawn(self, argv: list[str], env: dict[str, str]) -> ChildProcess: ...

    # ChildProcess protocol
    def send(self, line: str) -> None: ...
    def read_stdout(self) -> str | None: ...
    def read_stderr(self) -> str | None: ...
    def close_stdin(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int | None: ...

    # assertion surface
    sent: list[JsonDict]                # every parsed request frame, in arrival order
    argv: list[str] | None              # recorded by spawn()
    env: dict[str, str] | None          # recorded by spawn()
    closed: bool                        # close_stdin() or kill() was called
    def sent_methods(self) -> list[str]: ...
    def wait_sent(self, count: int, timeout: float = 5.0) -> bool: ...
```

### Mechanics

- `__init__`: copy the script into `dict[str, deque[Reply]]`; create `_out: queue.Queue[str | None]`
  and `_err: queue.Queue[str | None]`; preload `_err` with `stderr_lines`; if `ready`, put the
  `gateway.ready` line — `json.dumps(ev("gateway.ready", None, {"skin": "fake"}))` — onto `_out`.
  `_sent_cond = threading.Condition()`; `_exited = threading.Event()`.
- `spawn(argv, env)`: record `argv`/`env` (assertion surface for role/home/context env), return
  `self`. One FakeGateway = one child; a test that needs two children builds two fakes (the queue
  tests don't use fakes at all, and run_step spawns exactly one child).
- `send(line)`: if stdin closed or dead → `raise BrokenPipeError("fake stdin closed")` (mirrors a
  real dead pipe, keeps shutdown-then-request loud). Parse the line; under `_sent_cond`: append to
  `sent`, `notify_all()`. Look up `script[method]` and `popleft()`; if the method is unscripted or
  exhausted → auto-respond `{"error": {"code": -32601, "message": f"unknown method: {method}"}}`
  (mirrors server.py:910 — unscripted calls fail loud but structured, never hang). For a `Reply`:
  write `events_before` lines → the response line (with the request's own `id`; skipped when
  neither `result` nor `error` is set) → `events_after` lines → `frames` entries (dict →
  `json.dumps`, str → verbatim line) → if `die`: `_die()`.
- `_die()`: set dead flag; `_exited.set()`; put `None` on `_err`; put `None` on `_out`.
- `close_stdin()`: `closed = True`; if not already dead → `_die()` (the real gateway exits on
  stdin EOF, entry.py:344 — so the fake's reader threads also wind down and `wait` returns 0).
- `kill()`: `closed = True`; `_die()`.
- `read_stdout()`: `item = self._out.get()`; if `None` → re-put and return `None`; else return it.
  Same for `read_stderr()`.
- `wait(timeout)`: return `0` if `_exited.wait(timeout)` else `None`.
- `wait_sent(count, timeout)`: `with self._sent_cond: return self._sent_cond.wait_for(lambda: len(self.sent) >= count, timeout)`.

Note for the implementer: `send()` runs on the requester's thread and only *enqueues* stdout
lines; GatewayChild's reader thread consumes them. Cross-thread routing is therefore genuinely
exercised even though the fake itself is synchronous and deterministic.

---

## 5 · File: `src/planner/minds/runner.py`

### Public API (exact)

```python
"""run_step: the agent-operation primitive. Spawn a gateway child with role
env, create/resume the durable session, submit one prompt, drain events to
the single message.complete (or error event / child death), reap the child.

No overall wall-clock timeout by design (notes.md: REMOVE timeout-as-failure);
ready/request timeouts guard only the protocol handshake."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

from planner.minds.config import hermes_src_root
from planner.minds.gateway import (
    READY_TIMEOUT_DEFAULT,
    REQUEST_TIMEOUT_DEFAULT,
    GatewayChild,
    GatewayError,
    GatewayRpcError,
    JsonDict,
    SpawnFn,
    spawn_popen,
)

RunStatus = Literal["complete", "interrupted", "errored"]
OnEvent = Callable[[JsonDict], None]

SESSION_SOURCE: Final = "planner"
SESSION_COLS: Final = 100


@dataclass(frozen=True)
class RunResult:
    status: RunStatus
    text: str
    usage: JsonDict | None
    session_key: str | None
    error: str | None


def run_step(
    session_key: str | None,
    role: str,
    prompt_text: str,
    on_event: OnEvent | None,
    *,
    home: str | Path,
    hermes_python: str | Path,
    context_env: Mapping[str, str] | None = None,
    spawn: SpawnFn = spawn_popen,
    ready_timeout: float = READY_TIMEOUT_DEFAULT,
    request_timeout: float = REQUEST_TIMEOUT_DEFAULT,
    base_env: Mapping[str, str] | None = None,
) -> RunResult: ...
```

The four extra keyword-only parameters beyond the ticket signature are the injection seam
(`spawn`), the two handshake timeouts (required injectable by the ticket's behavior notes), and
`base_env` (defaults to `os.environ`; tests pass `{}` so the assembled env can be asserted exactly
without host leakage).

### Env assembly

```
python = Path(hermes_python).expanduser()
env: dict[str, str] = dict(base_env if base_env is not None else os.environ)
env["HERMES_PYTHON_SRC_ROOT"] = str(hermes_src_root(python))   # entry.py:4-12 guard
env["HERMES_HOME"] = str(Path(home).expanduser())              # spike isolation probe
env["HERMES_TUI_SKILLS"] = role                                # spike §2 Q2 — per-process role skill
if context_env:
    env.update(context_env)                                    # kanban HERMES_KANBAN_* pattern
```

### Control flow (exact)

```
try:
    child = GatewayChild(str(python), env, spawn=spawn)
except GatewayError as exc:
    return RunResult("errored", "", None, session_key, f"spawn failed: {exc}")

resolved_key = session_key
try:
    child.wait_ready(ready_timeout)
    if session_key is None:
        created = child.request(
            "session.create",
            {"source": SESSION_SOURCE, "cols": SESSION_COLS},
            timeout=request_timeout,
        )
        live_sid = str(created.get("session_id") or "")
        stored = created.get("stored_session_id")            # server.py:4417
        resolved_key = str(stored) if stored else None
    else:
        resumed = child.request(
            "session.resume", {"session_id": session_key}, timeout=request_timeout
        )
        live_sid = str(resumed.get("session_id") or "")      # NEW live handle
        tip = resumed.get("resumed")                          # durable key, maybe rotated tip
        resolved_key = str(tip) if tip else session_key       # server.py:4612-4619, 4633
    child.request(
        "prompt.submit", {"session_id": live_sid, "text": prompt_text},
        timeout=request_timeout,
    )                                                         # {"status":"streaming"} — value unused
    while True:                                               # D5: no deadline
        event = child.next_event()
        if event is None:
            return RunResult(
                "errored", "", None, resolved_key,
                f"gateway child died mid-run; stderr: {child.stderr_tail()!r}",
            )
        _notify(on_event, event)                              # D8: forward everything, guarded
        etype = str(event.get("type") or "")
        if etype == "error":
            raw = event.get("payload")
            payload = raw if isinstance(raw, dict) else {}
            return RunResult(
                "errored", "", None, resolved_key,
                str(payload.get("message") or "gateway error event"),
            )
        if etype == "message.complete":
            raw = event.get("payload")
            payload = raw if isinstance(raw, dict) else {}
            text = str(payload.get("text") or "")
            usage_raw = payload.get("usage")
            usage = usage_raw if isinstance(usage_raw, dict) else None
            gw_status = str(payload.get("status") or "complete")
            if gw_status == "complete":
                return RunResult("complete", text, usage, resolved_key, None)
            if gw_status == "interrupted":
                return RunResult("interrupted", text, usage, resolved_key, None)
            return RunResult(
                "errored", text, usage, resolved_key,
                text or "run ended with status=error",
            )
        # any other event type: forwarded above, otherwise ignored — keep draining
except GatewayRpcError as exc:
    return RunResult("errored", "", None, resolved_key, str(exc))
except GatewayError as exc:
    return RunResult("errored", "", None, resolved_key, str(exc))
finally:
    child.shutdown()                                          # child ALWAYS reaped
```

`_notify(on_event, event)` — module-private:
```
if on_event is None: return
try: on_event(event)
except Exception: logging.getLogger(__name__).exception("on_event observer failed")
```

### Status mapping table (normative)

| Observed outcome | `status` | `text` | `usage` | `session_key` | `error` |
|---|---|---|---|---|---|
| `message.complete` status=`complete` (server.py:6774) | `complete` | payload text | payload usage | durable key | `None` |
| `message.complete` status=`interrupted` | `interrupted` | payload text | payload usage | durable key | `None` |
| `message.complete` status=`error` | `errored` | payload text | payload usage | durable key | text, else `"run ended with status=error"` |
| `error` event any time after submit (server.py:1094, 6365-6380) | `errored` | `""` | `None` | durable key | payload `message` |
| child EOF mid-run (`next_event()` → `None`) | `errored` | `""` | `None` | durable key | `"gateway child died mid-run; stderr: ..."` |
| `GatewayRpcError` on `session.resume` (4007) | `errored` | `""` | `None` | input key unchanged | `"gateway rpc error 4007: session not found"` |
| `GatewayRpcError` on `prompt.submit` (4009) | `errored` | `""` | `None` | key from create/resume (still persistable) | `"gateway rpc error 4009: session busy"` |
| any other `GatewayRpcError` (4090, -32601, -32000, …) | `errored` | `""` | `None` | best known | `str(exc)` |
| `GatewayError` (spawn fail / not-ready / request timeout / stdin write fail / died pre-terminal) | `errored` | `""` | `None` | best known | `str(exc)` |

"Durable key" = `stored_session_id` (create) or the response's `resumed` (resume), per D7.

---

## 6 · File: `src/planner/minds/queue.py`

### Public API (exact)

```python
"""Per-mind serialized run queue: at most ONE in-flight run per key; different
keys run concurrently; FIFO within a key. This is load-bearing correctness —
the gateway's 4009 busy-guard is per-process only and cannot protect two
child-per-run processes resuming the same stored session (notes.md:99-106).

The key is the mind's stable identity, supplied by the caller (e.g. a ticket
id or "global") — NOT the Hermes session_key, which does not exist before
step 0. A run request's session_key travels inside the queued item.

Module name note: `planner.minds.queue` cannot shadow the stdlib `queue` —
Python 3 imports are absolute, so `import queue` anywhere (including in this
package) resolves to the stdlib; this module is only reachable as
`planner.minds.queue`.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable

_log = logging.getLogger(__name__)


class MindQueue[T]:
    def __init__(self, run: Callable[[str, T], None]) -> None: ...
    def submit(self, key: str, item: T) -> None: ...
    def wait_idle(self, timeout: float | None = None) -> bool: ...
```

(PEP 695 generic — fine under Python ≥3.12 / mypy 2.1.0 / ruff target py312. If the implementer
hits any tool wrinkle, the fallback is a module-level `TypeVar("T")` + `Generic[T]`; behavior
identical.)

### Semantics

- `run` is the injected run callable (D4). Wave 3 passes a System B closure that calls `run_step`
  and stamps ticket status; unit tests pass a controllable stub. The queue itself never imports
  `runner`.
- `submit(key, item)`: `key` must be a non-empty `str` — raise `ValueError("queue key must be a
  non-empty string")` otherwise (D3 — `None` never reaches the queue by type and empty is
  rejected at runtime).
- FIFO per key; one worker thread per *active* key; different keys → different threads → overlap.
- A completed run triggers the next item for that key simply because the worker loops: pop →
  run → pop … until the key's deque is empty, then the worker retires the key and exits.
- `run` exceptions: caught in the worker, `_log.exception(...)`, drain continues — one poisoned
  item must not stall its mind's queue. (Result/error semantics belong to the injected callable.)
- `wait_idle(timeout)`: block until no key is active; returns `True` if idle was reached. Test
  aid + graceful-shutdown hook for later waves.
- Worker threads are daemon (`name=f"mind-queue-{key}"`): queued-but-unstarted work is dropped on
  process exit; server-lifetime draining is a wave-3 concern.

### Internals (exact)

```
self._run = run
self._lock = threading.Lock()
self._idle = threading.Condition(self._lock)
self._pending: dict[str, deque[T]] = {}
self._active: set[str] = set()
```

`submit`:
```
if not key: raise ValueError("queue key must be a non-empty string")
with self._lock:
    self._pending.setdefault(key, deque()).append(item)
    if key in self._active:
        return
    self._active.add(key)
threading.Thread(
    target=self._drain, args=(key,), name=f"mind-queue-{key}", daemon=True
).start()
```
(Invariant established inside ONE critical section: a key with pending items is always in
`_active` with exactly one worker claimed. `key` is passed via `args=`, not a closure — B023-safe
even when tests submit in loops.)

`_drain(key)`:
```
while True:
    with self._lock:
        dq = self._pending.get(key)
        if not dq:
            self._pending.pop(key, None)
            self._active.discard(key)
            self._idle.notify_all()
            return
        item = dq.popleft()
    try:
        self._run(key, item)
    except Exception:
        _log.exception("mind run failed (key=%s)", key)
```
The emptiness check and the retire happen under the same lock, so a racing `submit` either sees
the key still active (appends; this worker will pop it on its next loop) or sees it retired
(starts a fresh worker). No lost items, no double workers.

`wait_idle`:
```
with self._lock:
    return self._idle.wait_for(lambda: not self._active, timeout)
```

---

## 7 · File: `src/planner/minds/config.py`

```python
"""Standalone resolution of the Hermes interpreter, source root, and planner
home, plus a boot smoke-check. Deliberately independent of planner.core.config
(this wave is additive; wiring into the core Config is a later wave).
Nothing calls boot_smoke_check this wave; it is unit-tested via the fake
spawn hook only — never with a real child inside pytest."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from planner.minds.gateway import READY_TIMEOUT_DEFAULT, GatewayChild, SpawnFn, spawn_popen

DEFAULT_HERMES_PYTHON: Final = "~/.hermes/hermes-agent/venv/bin/python"
DEFAULT_PLANNER_HOME: Final = "data/hermes-home"

ENV_HERMES_PYTHON: Final = "PLAN_HERMES_PYTHON"
ENV_PLANNER_HOME: Final = "PLAN_HERMES_HOME"


def resolve_hermes_python(
    value: str | None = None, env: Mapping[str, str] | None = None
) -> Path: ...

def hermes_src_root(hermes_python: Path | str) -> Path: ...

def resolve_planner_home(
    value: str | None = None, env: Mapping[str, str] | None = None
) -> Path: ...

def boot_smoke_check(
    hermes_python: Path | None = None,
    *,
    spawn: SpawnFn = spawn_popen,
    ready_timeout: float = READY_TIMEOUT_DEFAULT,
    env: Mapping[str, str] | None = None,
) -> None: ...
```

Behaviors:
- `resolve_hermes_python`: precedence explicit `value` → `env[ENV_HERMES_PYTHON]` → default;
  `env` defaults to `os.environ`; the winner gets `Path(...).expanduser()`. (Env override matches
  the house `PLAN_*` config style — core/config.py:93-101; standalone here because W1 must not
  touch existing files.)
- `hermes_src_root(p)`: `~/.hermes/hermes-agent/venv/bin/python` → `~/.hermes/hermes-agent` —
  `Path(p).expanduser()`, then `parents[2]`; if the path is too shallow for `parents[2]`
  (`IndexError`), fall back to `.parent` (documented: assumes the standard venv layout, and the
  guard only feeds the `HERMES_PYTHON_SRC_ROOT` sys.path hint — entry.py:4-12 — so a weird path
  degrades gracefully; `tui_gateway` is importable from the venv python regardless, spike §1).
- `resolve_planner_home`: same precedence with `ENV_PLANNER_HOME` and `DEFAULT_PLANNER_HOME`
  (`data/hermes-home` — provisional, sits in the gitignored data dir; the dedicated-home
  provisioning ticket is spike §5 sub-Q1 and owns the real answer).
- `boot_smoke_check`: spawn → `gateway.ready` → clean exit; raises `GatewayError` on any failure,
  returns `None` on success:
  ```
  python = hermes_python if hermes_python is not None else resolve_hermes_python()
  base = dict(env if env is not None else os.environ)
  base["HERMES_PYTHON_SRC_ROOT"] = str(hermes_src_root(python))
  child = GatewayChild(str(python), base, spawn=spawn)
  try:
      child.wait_ready(ready_timeout)
  finally:
      child.shutdown()
  ```
  Decision (per ticket option): it IS unit-tested, via the injection seam only (tests 27-28
  below). The smoke script additionally exercises the real-child path by hand.

---

## 8 · File: `src/planner/minds/smoke.py`

Human-run only (D6). Never imported by `__init__.py`, never collected by pytest
(`testpaths=["tests"]`), never called by `./verify`. mypy-strict clean (it lives under src/).

```python
"""Real-gateway smoke run — HUMAN-RUN ONLY. Mirrors spike 01 probes 1-3:
spawn -> ready -> session.create -> prompt.submit -> streamed events ->
message.complete; optional resume leg; deletes the session it created
(best effort). Talks to the REAL gateway and the REAL model: costs one
(or two, with --resume) model round trips and briefly creates a session
in the target HERMES_HOME.

Usage:
  .venv/bin/python -m planner.minds.smoke
      [--role SKILL] [--home DIR] [--prompt TEXT] [--resume]
      [--hermes-python PATH]
"""
```

- argparse (stdlib), flags exactly as above. Defaults: `--prompt "Reply with exactly: ok"`;
  `--role` absent → `HERMES_TUI_SKILLS` not set; `--home` absent → `HERMES_HOME` not set (the
  active default home, as the spike's probes 1-3 ran); `--hermes-python` absent →
  `resolve_hermes_python()`.
- Decision: the smoke drives `GatewayChild` directly (not `run_step`) — it mirrors the spike
  probes verbatim, and `run_step` unconditionally sets `HERMES_HOME`/`HERMES_TUI_SKILLS`, which
  would break the absent-flag defaults above. `run_step` itself is exhaustively covered by the
  unit suite; what only a human smoke can prove is the real transport. Env assembled by hand:
  `HERMES_PYTHON_SRC_ROOT` always; `HERMES_HOME`/`HERMES_TUI_SKILLS` only when flags given.
  Concretely:
  1. build env; `child = GatewayChild(...)`; `wait_ready()`; print ready latency.
  2. `created = child.request("session.create", {"source": "minds-smoke", "cols": 100})`;
     print live sid + stored key.
  3. `child.request("prompt.submit", {"session_id": live, "text": prompt})`; loop
     `child.next_event(timeout=120.0)` printing one line per event type, streaming
     `message.delta` text; stop on `message.complete` (print text/status/usage) or `error`
     event (print + exit 1) or `None` (child died, exit 1).
  4. `child.request("session.close", {"session_id": live})`, `child.shutdown()`.
  5. `--resume`: fresh `GatewayChild`, `session.resume {"session_id": stored}`, assert
     `message_count >= 2`, print it; close + shutdown. (Spike §1 probe 3.)
  6. cleanup (best effort, spike §1 probes 1/5: close first, then delete works):
     fresh `GatewayChild` → ready → `session.delete {"session_id": stored}` → print
     `{"deleted": ...}` or the 4023/4007 error → shutdown.
- The event loop here uses a generous explicit `next_event` timeout (120s) — this is an
  interactive tool, not the runner; a stuck smoke should end, not hang a terminal forever.
- `main() -> int`, `if __name__ == "__main__": raise SystemExit(main())`. Exit 0 only if the
  round trip completed.

---

## 9 · File: `tests/unit/test_minds.py`

Standalone: uses NO conftest fixture (do not touch `tests/unit/conftest.py`), no DB, no
subprocess, no real gateway, no network. Deterministic threading only: `threading.Event` /
`Condition.wait_for` / `Barrier` / `join` with generous timeouts (5s) — **no `time.sleep` as
synchronization anywhere**. Every test has real assertions (the verify skip-scan fails the build
on skip/xfail/empty/commented tests — scripts/verify_lib.py:24-101).

Module header + shared helpers:

```python
"""W1 minds primitive: gateway frame routing, run_step status mapping, the
per-mind serialized queue, and config resolution — all against the fake
gateway double (no subprocess, no model calls)."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from planner.minds.config import (
    boot_smoke_check, hermes_src_root, resolve_hermes_python, resolve_planner_home,
)
from planner.minds.fake import FakeGateway, Reply, ev
from planner.minds.gateway import GatewayChild, GatewayError, GatewayRpcError
from planner.minds.queue import MindQueue
from planner.minds.runner import RunResult, run_step

LIVE_SID = "ab12cd34"
STORED_KEY = "20260706_120000_abcdef"
HERMES_PY = "/x/hermes-agent/venv/bin/python"   # hermes_src_root -> /x/hermes-agent


def create_reply(sid: str = LIVE_SID, key: str = STORED_KEY) -> Reply:
    return Reply(result={
        "session_id": sid, "stored_session_id": key,
        "message_count": 0, "messages": [], "info": {"model": "fake"},
    })

def complete_ev(sid: str = LIVE_SID, text: str = "hello", status: str = "complete") -> dict[str, Any]:
    return ev("message.complete", sid, {"text": text, "usage": {"input": 1, "output": 2}, "status": status})

def submit_reply(*events_after: dict[str, Any], events_before: tuple[dict[str, Any], ...] = ()) -> Reply:
    return Reply(result={"status": "streaming"}, events_before=events_before, events_after=tuple(events_after))

def gw(fake: FakeGateway) -> GatewayChild:
    return GatewayChild(HERMES_PY, {}, spawn=fake.spawn)

def run(fake: FakeGateway, session_key: str | None = None, *, role: str = "planner-worker",
        on_event=None, **kw: Any) -> RunResult:
    return run_step(session_key, role, "do the step", on_event,
                    home="/tmp/planner-home", hermes_python=HERMES_PY,
                    spawn=fake.spawn, base_env={}, **kw)
```

(Tests are outside mypy scope — `files=["src"]` — so helper annotations are style, not gate.)

### Gateway routing (6 tests)

1. **`test_gateway_routes_response_and_events_independently`**
   Script `{"ping": [Reply(result={"pong": True}, events_before=(ev("message.delta", LIVE_SID, {"text": "x"}),))]}`.
   `child = gw(fake)`; `child.wait_ready(5.0)`; `resp = child.request("ping", timeout=5.0)`.
   Assert `resp == {"pong": True}`; `evt = child.next_event(timeout=5.0)`;
   assert `evt == {"type": "message.delta", "session_id": LIVE_SID, "payload": {"text": "x"}}`
   (an event arriving before the response is never mistaken for the response); `child.shutdown()`.

2. **`test_gateway_out_of_order_responses_resolve_by_id`**
   Ids are documented to start at 1. Script:
   `{"first": [Reply()]}` (Reply with neither result nor error → NO response written) and
   `{"second": [Reply(frames=({"jsonrpc": "2.0", "id": 2, "result": {"which": "second"}}, {"jsonrpc": "2.0", "id": 1, "result": {"which": "first"}}))]}`.
   Worker thread: `results["first"] = child.request("first", timeout=10.0)`; start it; main:
   `assert fake.wait_sent(1, 5.0)` (request 1 is on the wire before request 2);
   `resp2 = child.request("second", timeout=10.0)`; assert `resp2 == {"which": "second"}`;
   `t.join(10.0)`; assert `not t.is_alive()`; assert `results["first"] == {"which": "first"}`.
   Proves: responses written in REVERSE order resolve to the right waiters purely by id.

3. **`test_gateway_event_without_payload_key`**
   Script `{"go": [Reply(result={}, events_after=(ev("message.start", LIVE_SID),))]}`.
   Request, then `evt = child.next_event(timeout=5.0)`;
   assert `evt == {"type": "message.start", "session_id": LIVE_SID}` and `"payload" not in evt`
   (the real `message.start` has no payload key — server.py:6512/803-807).

4. **`test_gateway_tolerates_garbage_and_unknown_frames`**
   Script `{"go": [Reply(result={}, events_after=(ev("some.future.event", LIVE_SID, {"z": 1}),), frames=("this is not json", {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}, {"totally": "unrelated"}))], "again": [Reply(result={"ok": 1})]}`.
   `request("go")` succeeds; `next_event(5.0)["type"] == "some.future.event"` (unknown event
   types pass through, no crash); then `request("again", timeout=5.0) == {"ok": 1}` (router
   survived the garbage line, the id-null error frame, and the shapeless dict).

5. **`test_gateway_child_death_fails_pending_and_wakes_events`**
   `FakeGateway({"boom": [Reply(die=True)]}, stderr_lines=("traceback: kaboom",))`.
   `pytest.raises(GatewayError, match="died")` on `child.request("boom", timeout=5.0)`;
   `child.next_event(timeout=5.0) is None` (sentinel; call twice — second also `None`, re-armed);
   `child.shutdown()` (joins the stderr thread) then `assert child.stderr_tail() == ["traceback: kaboom"]`;
   `assert child.alive is False`.

6. **`test_gateway_wait_ready_timeout`**
   `FakeGateway({}, ready=False)`; `pytest.raises(GatewayError, match="ready")` on
   `child.wait_ready(timeout=0.2)`; `child.shutdown()`. (Deterministic: ready is never sent;
   the 0.2s expiry is the asserted behavior, not a synchronization sleep.)

### run_step (12 tests)

7. **`test_run_step_create_happy_path`**
   Script: `"session.create": [create_reply()]`, `"prompt.submit": [submit_reply(ev("message.start", LIVE_SID), ev("message.delta", LIVE_SID, {"text": "hel"}), ev("message.delta", LIVE_SID, {"text": "lo"}), ev("tool.start", LIVE_SID, {"name": "terminal"}), ev("weird.future", LIVE_SID, {"x": 1}), complete_ev(text="hello"))]`.
   Collect events via `seen: list[dict]` appender as `on_event`.
   Assert: `res.status == "complete"`, `res.text == "hello"`,
   `res.usage == {"input": 1, "output": 2}`, `res.session_key == STORED_KEY`,
   `res.error is None`;
   `fake.sent_methods() == ["session.create", "prompt.submit"]` (create, NOT resume);
   `fake.sent[0]["params"] == {"source": "planner", "cols": 100}`;
   `fake.sent[1]["params"] == {"session_id": LIVE_SID, "text": "do the step"}` (live handle, not
   the stored key);
   env: `fake.env["HERMES_TUI_SKILLS"] == "planner-worker"`,
   `fake.env["HERMES_HOME"] == "/tmp/planner-home"`,
   `fake.env["HERMES_PYTHON_SRC_ROOT"] == "/x/hermes-agent"`;
   `[e["type"] for e in seen] == ["message.start", "message.delta", "message.delta", "tool.start", "weird.future", "message.complete"]`
   (all events forwarded in order; unknown type tolerated);
   `fake.closed is True` (child reaped).

8. **`test_run_step_resume_path_issues_resume_not_create`**
   Script: `"session.resume": [Reply(result={"session_id": "ffff0000", "resumed": "20260707_090000_tip999", "message_count": 2, "messages": []})]`,
   `"prompt.submit": [submit_reply(complete_ev(sid="ffff0000", text="resumed"))]`.
   `res = run(fake, session_key=STORED_KEY)`.
   Assert: `fake.sent_methods() == ["session.resume", "prompt.submit"]` and
   `"session.create" not in fake.sent_methods()`;
   `fake.sent[0]["params"] == {"session_id": STORED_KEY}`;
   `fake.sent[1]["params"]["session_id"] == "ffff0000"` (the NEW live handle);
   `res.session_key == "20260707_090000_tip999"` (takes the server-resolved durable key — the
   compression-chain tip, server.py:4612-4619); `res.status == "complete"`.

9. **`test_run_step_context_env_rides_child_env`**
   Happy-path script; `run(fake, context_env={"PLANNER_TICKET_ID": "42"})`;
   assert `fake.env["PLANNER_TICKET_ID"] == "42"` and role/home keys still present.

10. **`test_run_step_interrupted`**
    `"prompt.submit": [submit_reply(complete_ev(status="interrupted", text="partial"))]`.
    Assert `res.status == "interrupted"`, `res.text == "partial"`, `res.error is None`,
    `res.session_key == STORED_KEY`.

11. **`test_run_step_complete_status_error`**
    `submit_reply(complete_ev(status="error", text="Error: provider 500"))`.
    Assert `res.status == "errored"`, `res.text == "Error: provider 500"`,
    `res.error == "Error: provider 500"`, usage still captured.

12. **`test_run_step_error_event_maps_errored`** (unknown-skill frame — loud)
    `"prompt.submit": [submit_reply(ev("message.start", LIVE_SID), ev("error", LIVE_SID, {"message": "agent init failed: Unknown skill(s): planner-bogus"}))]`.
    Assert `res.status == "errored"`;
    `"agent init failed: Unknown skill(s): planner-bogus" in (res.error or "")`;
    `res.text == ""`; `fake.closed is True`. (Real shape: server.py:1094, spike §1 line 112.)

13. **`test_run_step_error_event_racing_submit_response`**
    `"prompt.submit": [Reply(result={"status": "streaming"}, events_before=(ev("error", LIVE_SID, {"message": "agent init failed: no provider"}),))]`.
    The error event is on the wire BEFORE the submit response (the create path emits agent-build
    failures on a timer thread — server.py:4404-4411, 1094). Assert `res.status == "errored"`
    and the message survived. (Events are buffered in the queue while `request()` blocks; nothing
    is lost.)

14. **`test_run_step_complete_before_submit_response`**
    `"prompt.submit": [Reply(result={"status": "streaming"}, events_before=(ev("message.start", LIVE_SID), complete_ev(text="fast")))]`.
    The pool can order frames this way (server.py:914-950). Assert `res.status == "complete"`,
    `res.text == "fast"`.

15. **`test_run_step_child_death_mid_run`**
    `"prompt.submit": [Reply(result={"status": "streaming"}, events_after=(ev("message.start", LIVE_SID),), die=True)]`.
    Assert `res.status == "errored"`; `"died" in (res.error or "")`;
    `res.session_key == STORED_KEY` (create succeeded first); `fake.closed is True`
    (shutdown tolerates an already-dead child).

16. **`test_run_step_resume_not_found_4007`**
    `"session.resume": [Reply(error=(4007, "session not found"))]`.
    `res = run(fake, session_key="bogus_key_xyz")`.
    Assert `res.status == "errored"`; `"4007" in (res.error or "")` and
    `"session not found" in (res.error or "")`;
    `fake.sent_methods() == ["session.resume"]` (no prompt.submit attempted);
    `res.session_key == "bogus_key_xyz"` (input unchanged).

17. **`test_run_step_busy_4009`**
    `"session.create": [create_reply()]`, `"prompt.submit": [Reply(error=(4009, "session busy"))]`.
    Assert `res.status == "errored"`; `"4009" in (res.error or "")` and
    `"session busy" in (res.error or "")`;
    `res.session_key == STORED_KEY` (the fresh durable key is still returned for persistence);
    `fake.closed is True`.

18. **`test_run_step_on_event_exception_does_not_break_run`**
    Happy-path script; `on_event` raises `ValueError` on every call.
    Assert `res.status == "complete"` and `res.text == "hello"` (observer isolation).

### Queue (5 tests)

Shared stub machinery (module-level in the test file):
```python
class Job:
    def __init__(self, name: str, hold: bool = False) -> None:
        self.name = name
        self.started = threading.Event()
        self.release = threading.Event()
        if not hold:
            self.release.set()

class Recorder:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.order: list[str] = []
        self.inflight = 0
        self.max_inflight = 0
        self.errors: list[str] = []
    def run(self, key: str, job: Job) -> None:
        with self.lock:
            self.inflight += 1
            self.max_inflight = max(self.max_inflight, self.inflight)
            self.order.append(job.name)
        job.started.set()
        if not job.release.wait(5.0):
            with self.lock: self.errors.append(f"{job.name}: release timeout")
        with self.lock:
            self.inflight -= 1
```

19. **`test_queue_same_key_serializes_fifo`**
    `q = MindQueue(rec.run)`; `a = Job("a", hold=True)`; `b = Job("b")`.
    `q.submit("ticket:1", a)`; `q.submit("ticket:1", b)`;
    `assert a.started.wait(5.0)`; `assert not b.started.is_set()` (b not started while a held);
    `a.release.set()`; `assert q.wait_idle(5.0)`;
    `assert rec.order == ["a", "b"]`; `assert rec.max_inflight == 1` (the never-two-in-flight
    invariant — deterministic both directions: had b overlapped a at all, both increments land
    while `a` is held, so `max_inflight` would be 2); `assert rec.errors == []`.

20. **`test_queue_different_keys_overlap`**
    Stub for this test: `barrier = threading.Barrier(2)`; run: record inflight/max as above,
    then `barrier.wait(timeout=5.0)` inside try/except `threading.BrokenBarrierError` →
    append to `errors`. Submit `("ticket:1", j1)` and `("ticket:2", j2)`;
    `assert q.wait_idle(5.0)`; `assert errors == []` (the barrier passing PROVES both were in
    flight simultaneously); `assert rec.max_inflight == 2`.

21. **`test_queue_fifo_order_many_and_key_reuse_after_drain`**
    Submit jobs `a..d` (no holds) on `"k"`; `assert q.wait_idle(5.0)`;
    `assert rec.order == ["a", "b", "c", "d"]`; `assert rec.max_inflight == 1`.
    Then submit `e` on `"k"` (a retired key spawns a fresh worker);
    `assert q.wait_idle(5.0)`; `assert rec.order[-1] == "e"`.

22. **`test_queue_run_exception_does_not_stall_key`**
    Stub raises `RuntimeError("boom")` for job "boom", records normally otherwise.
    Submit `("k", boom_job)`, `("k", after_job)`; `assert q.wait_idle(5.0)`;
    assert "after" ran (in `rec.order`) — the key survived the poisoned item.

23. **`test_queue_rejects_empty_key`**
    `pytest.raises(ValueError)` on `q.submit("", Job("x"))`. (D3: keys are required identities;
    `None` is already excluded by the signature.)

### Config (5 tests)

24. **`test_resolve_hermes_python_precedence`**
    `resolve_hermes_python(None, env={}) == Path("~/.hermes/hermes-agent/venv/bin/python").expanduser()`;
    `resolve_hermes_python("/opt/py", env={"PLAN_HERMES_PYTHON": "/env/py"}) == Path("/opt/py")`;
    `resolve_hermes_python(None, env={"PLAN_HERMES_PYTHON": "~/envpy"}) == Path("~/envpy").expanduser()`.

25. **`test_hermes_src_root`**
    `hermes_src_root(Path("/x/hermes-agent/venv/bin/python")) == Path("/x/hermes-agent")`;
    shallow fallback: `hermes_src_root(Path("/python")) == Path("/")`.

26. **`test_resolve_planner_home`**
    default (`env={}`) `== Path("data/hermes-home")`; explicit beats env; env beats default;
    `~` expanded.

27. **`test_boot_smoke_check_with_fake`**
    `fake = FakeGateway({})`; `boot_smoke_check(Path(HERMES_PY), spawn=fake.spawn, env={})`
    returns `None` (no raise); `fake.closed is True`;
    `fake.env["HERMES_PYTHON_SRC_ROOT"] == "/x/hermes-agent"`;
    `fake.argv == [HERMES_PY, "-m", "tui_gateway.entry"]`.

28. **`test_boot_smoke_check_ready_timeout`**
    `fake = FakeGateway({}, ready=False)`;
    `pytest.raises(GatewayError)` on
    `boot_smoke_check(Path(HERMES_PY), spawn=fake.spawn, ready_timeout=0.2, env={})`;
    `fake.closed is True` (the finally still reaps).

Ticket-bullet coverage map: happy path → 7; resume-not-create → 8; error event → 12;
child death → 15; interrupted → 10; unknown-skill loud → 12 (+13 racing variant);
`message.complete status=error` → 11; 4007 → 16; 4009 → 17; queue same-key never-two-in-flight
+ ordering → 19/21; different keys overlap → 20; responses-vs-events routing → 1/2/3/4.

---

## 10 · Edge cases & risks (implementer must handle / know)

- **Out-of-order responses are normal.** `session.resume` runs on the gateway's 4-thread pool
  (server.py:184, 191-201); its response is written by the pool worker whenever it finishes and
  can interleave with events and other responses. The pending-table-by-id design absorbs this;
  never assume "the next line answers my request" (tests 2, 13, 14).
- **`error` event racing the `prompt.submit` response / `message.complete` before the submit
  response.** Both orderings occur (agent build failures emit on a timer/daemon thread —
  server.py:4404-4411, 6365-6383). The reader enqueues events while `request()` blocks; the
  drain finds them afterward. Nothing may treat "event before response" as a protocol violation.
- **`message.start` has NO payload key; `gateway.ready` has NO session_id key** (server.py:803-807,
  entry.py:316-322). Every payload access must be `event.get("payload")` + isinstance-guard;
  never `event["payload"]`.
- **Unknown event types must be ignored (forwarded, not crashed on).** The gateway emits event
  types we don't enumerate (`status.update`, `session.info`, future ones); the router enqueues
  all, the runner forwards and drains past them (tests 4, 7).
- **Child death signaling order matters**: set dead → fail pending → open ready gate → event
  sentinel (§3 `_on_child_dead`). Getting this wrong deadlocks `wait_ready` or the drain.
- **Parse-error frames carry `id: null`** (entry.py:332) — uncorrelatable; the router ignores
  them. We never send malformed lines, so no waiter can be stranded by this.
- **Stdlib `queue` vs `planner.minds.queue`**: safe — Python 3 imports are absolute, `import
  queue` inside `gateway.py`/`fake.py` resolves to the stdlib; the package module is only
  reachable as `planner.minds.queue`. Ruff's shadowing rule (A005) is not in the selected set
  (pyproject.toml:34). Do not add relative imports.
- **mypy strict gotchas** (mypy 2.1.0, `strict=true`, src only):
  - Never return `json.loads(...)` or `frame.get(...)` directly where a typed dict is declared —
    `warn_return_any` fires. Use the isinstance-narrowing patterns written into §3/§5 pseudocode
    (`result if isinstance(result, dict) else {}`).
  - `Popen.stdin/stdout/stderr` are `IO[str] | None` — capture non-None references once in
    `PopenChild.__init__` (assert, then store typed attributes).
  - Annotate every def, including test-support code in `fake.py` (it lives under src/ and is
    strict-checked; the *tests* are not — `files=["src"]`).
  - `Literal` status: construct `RunResult` only with the three literals; no `str` passthrough
    from the wire — the mapping in §5 converts explicitly.
- **ruff gotchas** (E,F,W,I,UP,B; line length 100):
  - B023: never capture a loop variable in a thread closure — `MindQueue.submit` passes `key`
    via `args=(key,)`; tests submitting in loops should pass items positionally too.
  - UP: `X | None` unions, builtin generics (`dict[str, Any]`, `deque[str]`), no
    `typing.Optional/Dict/Tuple`; import `Callable`/`Mapping`/`Sequence` from `collections.abc`.
  - I001: `from __future__ import annotations` first, stdlib block, then `planner.*` block.
  - B008 is about *calls* in defaults — `spawn: SpawnFn = spawn_popen` (a reference) is fine.
- **Fake fidelity rules**: response frames must carry the request's own id verbatim; event frames
  must be built with `ev()` (payload-key omission is the point); `send()` after close/death must
  raise `BrokenPipeError`; `close_stdin()` must produce stdout EOF (the real gateway exits on
  stdin EOF — entry.py:344) so `GatewayChild.shutdown()` completes cleanly against the fake.
- **No sleeps as synchronization in tests.** Events/Conditions/Barriers/joins with 5s bounds; the
  only timeouts that *expire* in a passing suite are the two 0.2s never-ready cases (6, 28),
  where expiry IS the asserted behavior.
- **Session-key rotation (spike §5 sub-Q3)** is handled to the extent this wave can: resume
  returns the chain tip and `RunResult.session_key` propagates it (D7). Mid-run rotation
  handling beyond that is a later-wave integration concern — do not build more here.

### What the implementer must NOT do

- Do NOT wire anything into the server, dispatcher, DB, chat, or CLI — nothing outside
  `src/planner/minds/` may import it this wave.
- Do NOT modify ANY existing file: not `tests/unit/conftest.py`, not `planner/core/config.py`,
  not `pyproject.toml`, nothing. The owned files are exactly: `src/planner/minds/__init__.py`,
  `gateway.py`, `runner.py`, `queue.py`, `fake.py`, `config.py`, `smoke.py`, and
  `tests/unit/test_minds.py`.
- Do NOT spawn a real gateway (or any subprocess) anywhere pytest can reach. The real child is
  touched only by a human running `python -m planner.minds.smoke`.
- Do NOT add dependencies — stdlib only (`subprocess`, `threading`, `json`, `queue`,
  `collections`, `dataclasses`, `argparse`, `logging`, `pathlib`, `typing`).
- Do NOT add an overall run wall-clock timeout to `run_step` (D5).
- Do NOT read from or write to `~/.hermes/**` (research was already done; it's all cited here).
  Never run git.

---

## 11 · Build order & self-check

1. `gateway.py` (protocol + PopenChild + GatewayChild) → 2. `fake.py` → 3. gateway tests (1-6)
   green → 4. `config.py` (+ tests 24-28) → 5. `runner.py` (+ tests 7-18) → 6. `queue.py`
   (+ tests 19-23) → 7. `__init__.py` → 8. `smoke.py` (compile-only confidence: mypy/ruff; do NOT
   execute it).

Self-check before handing back (run all three, show output):
```
.venv/bin/ruff check .
.venv/bin/mypy src/
.venv/bin/pytest tests/unit/test_minds.py -q
```
Do not run full `./verify` yourself — the integrator runs it serially (verify runs must not be
concurrent). The suite must pass with zero skips; expected runtime well under 10s (the only
deliberate waits are two 0.2s expiries).

---

## 12 · BINDING AMENDMENTS (post-review — these override earlier sections where they conflict)

Codex plan review (see `plan-review.md`) accepted the plan except for D3's queue-key contract.
The following amendments are binding on the implementer.

**A1 — Scope pin.** Write EXACTLY these files and nothing else:
`src/planner/minds/__init__.py`, `gateway.py`, `runner.py`, `queue.py`, `fake.py`, `config.py`,
`smoke.py`, and `tests/unit/test_minds.py`. No edits to any existing file. If something seems to
require touching another file, stop and report instead.

**A2 — D3 is REVISED: the queue key IS the durable Hermes `session_key` (ticket contract).**
- Drop the "mind identity" concept entirely — no `"ticket:17"` / `"global"` examples, no claim
  that the key is "not the Hermes session key". The module docstring states: the key is the
  mind's durable `session_key` (the `stored_session_id`), and the queue guarantees at most one
  in-flight run per `session_key`, FIFO within a key, concurrency across keys — load-bearing
  because the gateway's 4009 busy-guard is per-process only (notes.md).
- Mechanics are UNCHANGED from §6: `submit(key: str, item: T)` with `ValueError` on empty key,
  per-key on-demand daemon worker, one lock + Condition, `wait_idle`. Codex confirmed the
  locking race-free; do not redesign it.
- Drop the sentence "`session_key` travels inside the queued item" — the queue is generic over
  `T` and prescribes NOTHING about item contents.
- Add to the module docstring an explicit wave-3 boundary note, in this spirit: "Step-0 runs
  (no `session_key` yet) are not routed through this queue in W1 — nothing invokes the queue
  this wave. Wave 3's wiring must serialize kickoff itself and should resolve the mind's current
  stored `session_key` at execution time (not capture it at enqueue), so queued step-0 work
  converges on the first created key."

**A3 — Queue tests re-keyed to session keys.** Tests 19, 20, 21 use durable-session-key-shaped
queue keys so the ticket bullets are covered verbatim:
- test 19 (`test_queue_same_key_serializes_fifo`): key `"20260706_120000_aaaaaa"` for BOTH
  producers — two producers on the SAME `session_key` never overlap.
- test 20 (`test_queue_different_keys_overlap`): keys `"20260706_120000_aaaaaa"` and
  `"20260706_120000_bbbbbb"`.
- test 21 (key-reuse-after-drain): a single session-key-shaped key throughout.
- tests 22, 23 unchanged (any non-empty key string is fine; 23 keeps asserting the empty-key
  `ValueError`).
No "step-0 convergence" test exists in W1 (there is no convergence machinery to test — see
plan-review.md finding 2).

**A4 — Everything else stands as written.** Codex found protocol shapes, run_step status
mapping, locking, hermeticity, mypy/ruff posture, and scope clean. Implement §§2-11 as
specified, with A1-A3 applied.
