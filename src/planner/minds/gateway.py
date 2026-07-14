"""GatewayChild: one Hermes tui_gateway child over newline-delimited JSON-RPC.

Frame router: a single reader thread routes JSON-RPC responses (keyed by id)
to per-request waiters, sends process events to their process queue, and writes
session events once to the child-wide ordered ingress.
Request ids are integers allocated from 1, incrementing — a documented
contract the tests rely on. Responses may arrive out of order (the gateway
runs long handlers on an internal thread pool); correlation is strictly by id.
"""

from __future__ import annotations

import json
import queue
import subprocess
import threading
from collections import deque
from collections.abc import Callable, Mapping
from time import monotonic as _monotonic
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


class PopenChild:
    """Real ChildProcess backed by a subprocess.Popen in text/line-buffered mode."""

    def __init__(self, proc: subprocess.Popen[str]) -> None:
        # PIPEs by construction (see spawn_popen); assert once, store typed refs.
        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None
        self._proc = proc
        self._stdin = proc.stdin
        self._stdout = proc.stdout
        self._stderr = proc.stderr

    def send(self, line: str) -> None:
        self._stdin.write(line)
        self._stdin.flush()

    def read_stdout(self) -> str | None:
        line = self._stdout.readline()
        return line if line != "" else None

    def read_stderr(self) -> str | None:
        line = self._stderr.readline()
        return line if line != "" else None

    def close_stdin(self) -> None:
        try:
            self._stdin.close()
        except (OSError, ValueError):
            pass

    def kill(self) -> None:
        try:
            self._proc.kill()
        except (OSError, ProcessLookupError):
            pass

    def wait(self, timeout: float | None = None) -> int | None:
        try:
            return self._proc.wait(timeout)
        except subprocess.TimeoutExpired:
            return None


def spawn_popen(argv: list[str], env: dict[str, str]) -> ChildProcess:
    """Spawn the real gateway child (spike §1 lines 24-29)."""
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )
    return PopenChild(proc)


class _Pending:
    """A single in-flight request awaiting its response frame."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.frame: JsonDict | None = None
        self.failure: str | None = None


class GatewayRequestHandle:
    """A written JSON-RPC request whose response remains correlated by request id."""

    def __init__(
        self,
        child: GatewayChild,
        request_id: int,
        method: str,
        pending: _Pending,
    ) -> None:
        self._child = child
        self._request_id = request_id
        self._method = method
        self._pending = pending

    def wait(self, timeout: float = REQUEST_TIMEOUT_DEFAULT) -> JsonDict:
        if not self._pending.done.wait(timeout):
            self._child._forget_request(self._request_id, self._pending)
            raise GatewayError(f"no response to {self._method} within {timeout}s")
        resp = self._pending.frame
        if self._pending.failure is not None:
            raise GatewayError(self._pending.failure)
        if resp is None:
            raise GatewayError(
                f"gateway child died before responding to {self._method}; "
                f"stderr: {self._child.stderr_tail()!r}"
            )
        err = resp.get("error")
        if isinstance(err, dict):
            raise GatewayRpcError(int(err.get("code", -1)), str(err.get("message", "")))
        result = resp.get("result")
        return result if isinstance(result, dict) else {}

    def response_result_if_ready(self) -> JsonDict | None:
        """Inspect a successful response without consuming or waiting for it."""
        if not self._pending.done.is_set() or self._pending.failure is not None:
            return None
        resp = self._pending.frame
        if resp is None or isinstance(resp.get("error"), dict):
            return None
        result = resp.get("result")
        return result if isinstance(result, dict) else {}

    def response_rpc_error_if_ready(self) -> GatewayRpcError | None:
        """Inspect an already-received RPC rejection without consuming it."""
        if not self._pending.done.is_set() or self._pending.failure is not None:
            return None
        resp = self._pending.frame
        if resp is None:
            return None
        error = resp.get("error")
        if not isinstance(error, dict):
            return None
        return GatewayRpcError(
            int(error.get("code", -1)),
            str(error.get("message", "")),
        )

    def cancel(self, detail: str) -> None:
        """Wake this waiter when its owner can no longer use the response."""
        if not self._child._forget_request(self._request_id, self._pending):
            return
        self._pending.failure = detail
        self._pending.done.set()


class ChildSessionEventIngress:
    """The single ordered feed of all session-scoped events from one child."""

    def __init__(self, events: queue.Queue[JsonDict | None]) -> None:
        self._events = events
        self._closed = False

    def next_event(self, timeout: float | None = None) -> JsonDict | None:
        try:
            item = (
                self._events.get(timeout=timeout)
                if timeout is not None
                else self._events.get()
            )
        except queue.Empty:
            raise GatewayError(f"no child session event within {timeout}s") from None
        if item is None:
            self._events.put(None)
            return None
        return item

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._events.put(None)


class GatewayChild:
    """One gateway child + its frame router (reader thread, pending table, event queue)."""

    def __init__(
        self,
        hermes_python: str,
        env: Mapping[str, str],
        *,
        spawn: SpawnFn = spawn_popen,
        stderr_tail_lines: int = STDERR_TAIL_LINES,
    ) -> None:
        argv = [hermes_python, "-m", "tui_gateway.entry"]
        try:
            self._child: ChildProcess = spawn(argv, dict(env))
        except OSError as exc:
            raise GatewayError(f"failed to spawn gateway child: {exc}") from exc
        self._pending: dict[int, _Pending] = {}
        self._lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._next_id = 1
        self._child_session_events: queue.Queue[JsonDict | None] = queue.Queue()
        self._child_session_events_claimed = False
        self._process_events: queue.Queue[JsonDict | None] = queue.Queue()
        self._ready_gate = threading.Event()
        self._ready_seen = False
        self._dead = threading.Event()
        self._stderr_lines: deque[str] = deque(maxlen=stderr_tail_lines)
        self._stdout_thread = threading.Thread(
            target=self._stdout_loop, name="minds-gw-stdout", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop, name="minds-gw-stderr", daemon=True
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    # --- reader threads ----------------------------------------------------

    def _stdout_loop(self) -> None:
        while True:
            line = self._child.read_stdout()
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate garbage; stdout is JSON-only per server.py:203-207
            if not isinstance(frame, dict):
                continue
            if frame.get("method") == "event":
                raw = frame.get("params")
                params: JsonDict = raw if isinstance(raw, dict) else {}
                if params.get("type") == "gateway.ready":  # entry.py:316-322 — no session_id
                    self._ready_seen = True
                    self._ready_gate.set()
                    continue
                self._route_event(params)
                continue
            rid = frame.get("id")
            if rid is not None and ("result" in frame or "error" in frame):
                with self._lock:
                    pending = self._pending.pop(rid, None)
                if pending is not None:
                    pending.frame = frame
                    pending.done.set()
            # else: ignore — e.g. -32700 parse-error frames carry id null, uncorrelatable
        self._on_child_dead()

    def _on_child_dead(self) -> None:
        self._dead.set()
        with self._lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for entry in pending:
            entry.done.set()  # frame stays None -> waiter raises child-died
        self._ready_gate.set()  # wakes wait_ready, which checks _ready_seen first
        self._child_session_events.put(None)
        self._process_events.put(None)

    def _stderr_loop(self) -> None:
        while True:
            line = self._child.read_stderr()
            if line is None:
                break
            self._stderr_lines.append(line.rstrip("\n"))

    def _route_event(self, params: JsonDict) -> None:
        raw_session_id = params.get("session_id")
        if raw_session_id is None:
            self._process_events.put(params)
            return
        self._child_session_events.put(params)

    # --- public API --------------------------------------------------------

    def wait_ready(self, timeout: float = READY_TIMEOUT_DEFAULT) -> None:
        if not self._ready_gate.wait(timeout):
            raise GatewayError(
                f"gateway.ready not received within {timeout}s; stderr: {self.stderr_tail()!r}"
            )
        if not self._ready_seen:  # gate was set by death
            raise GatewayError(
                f"gateway child exited before ready; stderr: {self.stderr_tail()!r}"
            )

    def request(
        self,
        method: str,
        params: JsonDict | None = None,
        *,
        timeout: float = REQUEST_TIMEOUT_DEFAULT,
    ) -> JsonDict:
        return self.begin_request(method, params).wait(timeout)

    def begin_request(
        self,
        method: str,
        params: JsonDict | None = None,
    ) -> GatewayRequestHandle:
        """Register and write a request without waiting for its response."""
        if self._dead.is_set():
            raise GatewayError(f"gateway child is dead; cannot send {method}")
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            pending = _Pending()
            self._pending[rid] = pending
        frame: JsonDict = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            frame["params"] = params
        try:
            with self._send_lock:
                self._child.send(json.dumps(frame) + "\n")
        except OSError as exc:  # BrokenPipeError is an OSError
            with self._lock:
                self._pending.pop(rid, None)
            raise GatewayError(f"gateway stdin write failed for {method}: {exc}") from exc
        return GatewayRequestHandle(self, rid, method, pending)

    def _forget_request(self, request_id: int, pending: _Pending) -> bool:
        with self._lock:
            if self._pending.get(request_id) is pending:
                self._pending.pop(request_id, None)
                return True
            return False

    def claim_session_event_ingress(self) -> ChildSessionEventIngress:
        """Claim the one physical ordered session-event ingress for this child."""
        with self._lock:
            if self._child_session_events_claimed:
                raise RuntimeError("child session event ingress already claimed")
            self._child_session_events_claimed = True
        return ChildSessionEventIngress(self._child_session_events)

    def next_process_event(self, timeout: float | None = None) -> JsonDict | None:
        try:
            item = (
                self._process_events.get(timeout=timeout)
                if timeout is not None
                else self._process_events.get()
            )
        except queue.Empty:
            raise GatewayError(f"no process gateway event within {timeout}s") from None
        if item is None:
            self._process_events.put(None)
            return None
        return item

    def stderr_tail(self) -> list[str]:
        return list(self._stderr_lines)

    def shutdown(
        self,
        grace: float = SHUTDOWN_GRACE_DEFAULT,
        *,
        deadline: float | None = None,
    ) -> None:
        def remaining() -> float:
            return grace if deadline is None else max(0.0, deadline - _monotonic())

        self._child.close_stdin()  # clean path: entry.py:344 exits on stdin EOF
        if self._child.wait(remaining()) is None:
            self._child.kill()
            self._child.wait(remaining())
        # reader threads exit on EOF; bounded join for tidiness (daemon threads anyway)
        self._stdout_thread.join(timeout=remaining())
        self._stderr_thread.join(timeout=remaining())

    @property
    def alive(self) -> bool:
        return not self._dead.is_set()
