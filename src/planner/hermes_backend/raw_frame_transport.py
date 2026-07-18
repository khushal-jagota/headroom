"""RawFrameChildTransport: one Hermes tui_gateway child owned directly over its
raw newline-delimited JSON stdio, on the injected `SpawnFn`/`ChildProcess` seam.

This is a PEER of `GatewayChild` built on the same spawn seam, but it uses NONE of
`GatewayChild`'s typed client methods. It gives the relay:
- a single ordered ingress of parsed frames (one permanent `on_frame` callback),
- a single-writer ordered off-loop egress (one outbound writer thread draining a FIFO),
- a bounded readiness wait, a bounded diagnostic stderr tail,
- an atomic single-fire death signal, and a deadline-bounded shutdown.

Concurrency (see plan §0): one stdout reader thread, one outbound writer thread (the
SOLE writer AND SOLE graceful closer of stdin), one stderr reader thread. `on_frame`
is a single PERMANENT callback for the transport's whole life — never swapped. The
reader thread is NOT started in `__init__`; the pool calls `start_reading()` only AFTER
it has registered the child with the relay, so `gateway.ready` (emitted before Hermes
reads stdin) always routes through a live binding and is never lost.
"""

from __future__ import annotations

import json
import queue
import threading
from collections import deque
from collections.abc import Callable, Mapping
from time import monotonic as _monotonic

from planner.minds.gateway import (
    READY_TIMEOUT_DEFAULT,
    STDERR_TAIL_LINES,
    JsonDict,
    SpawnFn,
    spawn_popen,
)


class RawFrameTransportError(Exception):
    """Transport-level failure: spawn failure, child death, ready timeout."""


class _ShutdownSentinel:
    """The single outbound-queue marker that tells the writer thread to drain,
    close stdin itself, and exit."""


_SHUTDOWN_SENTINEL = _ShutdownSentinel()


class RawFrameChildTransport:
    """Own ONE Hermes child directly over its raw stdio."""

    def __init__(
        self,
        *,
        hermes_python: str,
        env: Mapping[str, str],
        on_frame: Callable[[JsonDict], None],
        on_dead: Callable[[], None],
        spawn: SpawnFn = spawn_popen,
        stderr_tail_lines: int = STDERR_TAIL_LINES,
    ) -> None:
        self._on_frame = on_frame
        self._on_dead = on_dead
        argv = [hermes_python, "-m", "tui_gateway.entry"]
        try:
            self._child = spawn(argv, dict(env))
        except OSError as exc:
            raise RawFrameTransportError(f"failed to spawn relay child: {exc}") from exc

        # Egress: one FIFO queue drained by one writer thread (the sole stdin writer).
        self._outbound: queue.Queue[str | _ShutdownSentinel] = queue.Queue()

        # Death guard: atomic single-fire (lock + boolean).
        self._dead_lock = threading.Lock()
        self._dead = False
        self.dead_event = threading.Event()

        # Readiness gate.
        self._ready_gate = threading.Event()
        self._ready_seen = False

        # Diagnostic stderr tail.
        self._stderr_lines: deque[str] = deque(maxlen=stderr_tail_lines)

        # Lifecycle guard for start_reading vs shutdown (plan §1 R3-round3-2).
        self._lifecycle_lock = threading.Lock()
        self._stdout_started = False
        self._shutdown_begun = False
        # Set by the shutdown LEADER when the full teardown (child dead + threads joined)
        # has completed, so a follower can wait on it up to the follower's OWN deadline.
        self._teardown_complete = threading.Event()

        # The stdout reader thread is NOT started here (two-phase init).
        self._stdout_thread = threading.Thread(
            target=self._stdout_loop, name="relay-transport-stdout", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop, name="relay-transport-stderr", daemon=True
        )
        self._outbound_thread = threading.Thread(
            target=self._outbound_loop, name="relay-transport-outbound", daemon=True
        )
        self._stderr_thread.start()
        self._outbound_thread.start()

    # --- lifecycle: start reading (phase two) ------------------------------

    def start_reading(self) -> None:
        """Start the stdout reader thread. Called by the pool AFTER
        `relay.register_child(...)` so the permanent `on_frame` always has a live
        binding. If shutdown already began, raises `RawFrameTransportError` so the
        initializer aborts BEFORE `wait_ready` (which would otherwise block for the
        full ready timeout on a reader that will never start). Idempotent otherwise."""
        with self._lifecycle_lock:
            if self._shutdown_begun:
                raise RawFrameTransportError("transport shut down before reading started")
            if self._stdout_started:
                return
            # Set the started flag only AFTER start() succeeds: if start() raised, the
            # thread never ran, so shutdown() must not later try to join it (R3-round3-2 —
            # joining a never-started threading.Thread raises RuntimeError).
            self._stdout_thread.start()
            self._stdout_started = True

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
                continue  # tolerate garbage; stdout is JSON-only
            if not isinstance(frame, dict):
                continue
            if not self._ready_gate.is_set():
                params = frame.get("params")
                if isinstance(params, dict) and params.get("type") == "gateway.ready":
                    self._ready_seen = True
                    self._ready_gate.set()
            # gateway.ready is STILL forwarded (not swallowed) — one permanent callback.
            self._on_frame(frame)
        self._mark_dead()

    def _stderr_loop(self) -> None:
        while True:
            line = self._child.read_stderr()
            if line is None:
                break
            self._stderr_lines.append(line.rstrip("\n"))

    def _outbound_loop(self) -> None:
        while True:
            item = self._outbound.get()
            if isinstance(item, _ShutdownSentinel):
                self._child.close_stdin()
                break
            try:
                self._child.send(item)
            except OSError:  # BrokenPipeError is an OSError
                self._mark_dead()
                break

    # --- egress ------------------------------------------------------------

    def enqueue_frame(self, frame: JsonDict) -> None:
        """Non-blocking egress entry point, called ON THE LOOP by the relay. Rewrites
        NOTHING — the caller has already produced the exact frame to write."""
        if self._dead:
            return  # best-effort fast drop; the writer also guards
        self._outbound.put_nowait(json.dumps(frame) + "\n")

    # --- readiness ---------------------------------------------------------

    def wait_ready(self, timeout: float = READY_TIMEOUT_DEFAULT) -> None:
        if not self._ready_gate.wait(timeout):
            raise RawFrameTransportError(
                f"gateway.ready not received within {timeout}s; stderr: {self.stderr_tail()!r}"
            )
        if not self._ready_seen:  # gate was opened by death, not by ready
            raise RawFrameTransportError(
                f"relay child exited before ready; stderr: {self.stderr_tail()!r}"
            )

    # --- death -------------------------------------------------------------

    def _mark_dead(self) -> bool:
        """Atomic single-fire death transition. Returns whether THIS caller won."""
        with self._dead_lock:
            if self._dead:
                return False
            self._dead = True
        # Exactly once, by the winner:
        self.dead_event.set()
        self._ready_gate.set()  # wake wait_ready (it checks _ready_seen)
        self._on_dead()
        return True

    # --- diagnostics / shutdown -------------------------------------------

    def stderr_tail(self) -> list[str]:
        return list(self._stderr_lines)

    def shutdown(self, *, deadline: float) -> None:
        """Deadline-bounded teardown, mirroring GatewayChild.shutdown's discipline.
        Coordinates with the outbound writer (which closes stdin on the sentinel) and
        with start_reading (never join an unstarted thread).

        Shutdown-once for the FULL teardown (sentinel/drain/joins run once, by the
        LEADER — the first caller). But a FOLLOWER still enforces ITS OWN (possibly
        earlier) deadline: it waits for the leader's teardown up to its remaining budget,
        and if the child is still alive by the follower's deadline it directly `kill()`s
        it + bounded-waits — so whichever caller has the earliest deadline forces the
        child dead by then (the one-shared-deadline discipline, contract line 60),
        WITHOUT re-running the full teardown or double-joining threads."""

        def remaining() -> float:
            return max(0.0, deadline - _monotonic())

        with self._lifecycle_lock:
            is_leader = not self._shutdown_begun
            if is_leader:
                self._shutdown_begun = True
            stdout_started = self._stdout_started
        # Wake any in-flight `wait_ready`/session-RPC IMMEDIATELY, even when the stdout
        # reader never started (so nothing else would ever open these gates). Their
        # waiters check `_ready_seen`/`dead_event` and raise the child-exited error at
        # once instead of blocking for the full ready/request timeout.
        self.dead_event.set()
        self._ready_gate.set()

        if not is_leader:
            # Follower: wait for the leader's teardown up to OUR deadline; if the child
            # is still alive by then, force it dead ourselves so an earlier follower
            # deadline is honored regardless of the leader's (possibly later) deadline.
            self._teardown_complete.wait(remaining())
            if self._child.wait(remaining()) is None:
                self._child.kill()
                self._child.wait(remaining())
            return

        # Leader: run the full teardown once.
        # Ask the writer to drain and close stdin itself (never racing an in-flight send).
        self._outbound.put_nowait(_SHUTDOWN_SENTINEL)
        if self._child.wait(remaining()) is None:
            self._child.kill()  # breaks a writer still blocked inside child.send
            self._child.wait(remaining())
        # reader/writer threads exit on EOF / sentinel; bounded joins.
        self._stderr_thread.join(timeout=remaining())
        self._outbound_thread.join(timeout=remaining())
        if stdout_started:
            self._stdout_thread.join(timeout=remaining())
        self._teardown_complete.set()  # release any follower waiting on us

    @property
    def alive(self) -> bool:
        return not self._dead
