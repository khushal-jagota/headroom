"""EmployeeChildPool: the relay-consumer adapter over a shared EmployeeChildRegistry.

The registry (in `minds/`) owns the per-employee children — spawn/respawn, the generation
counter, session create/resume/close/interrupt, stored/live session maps, rebind, fail-closed
persistence, and one-deadline shutdown-all. This pool is the relay CONSUMER + the registry's
`ChildFrameSubscriber`: it builds the concrete `RawFrameChildTransport` (the default
`reader_factory`), wires the relay (`attach`/`deliver`/`on_dead`/`detach`/`detach_now`), owns the
step-submission settlement (`TurnSubmission` + `submit_step_prompt`), and proxies its historical
privates to the registry so the public surface is unchanged.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Final

from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID
from planner.hermes_backend.raw_frame_transport import (
    RawFrameChildTransport,
    RawFrameTransportError,
)
from planner.hermes_backend.raw_frame_transport import (
    _PoolSessionResponder as _PoolSessionResponder,
)
from planner.minds.config import hermes_src_root
from planner.minds.employee_child_registry import (
    INIT_EXECUTOR_MAX_WORKERS as INIT_EXECUTOR_MAX_WORKERS,
)
from planner.minds.employee_child_registry import (
    ChildReader,
    ChildRegistryClosing,
    EmployeeChildRecord,
    EmployeeChildRegistry,
    ReaderFactory,
)
from planner.minds.gateway import (
    READY_TIMEOUT_DEFAULT,
    REQUEST_TIMEOUT_DEFAULT,
    JsonDict,
    SpawnFn,
    spawn_popen,
)

if TYPE_CHECKING:
    from planner.hermes_backend.employee_child_relay import EmployeeChildRelay

SESSION_COLS: Final = 100
RELAY_SESSION_SOURCE: Final = "panels-relay"

_PLAN_TICKET_ID_ENV: Final = "PLAN_TICKET_ID"
_HERMES_TUI_SKILLS_ENV: Final = "HERMES_TUI_SKILLS"

# The registry's closing/no-live-child error, re-exported under the pool's historical name so
# callers (and tests) that `except PoolError` keep catching the SAME class the registry raises.
PoolError = ChildRegistryClosing


class TurnSubmission:
    """The per-employee settlement state for ONE step submission (S3 §1.3-§1.4).

    Folded ENTIRELY on the child stdout thread (`_fold_frame`), read by the runner thread
    that blocks on `settled`. The stdout thread only mutates in-memory fields under `_lock`,
    enqueues streamed frames, and sets `settled` — it NEVER calls `on_event` or touches a
    SQLite connection (Codex Finding 2). The runner thread drains `frames` and shapes
    `on_event` itself.

    Terminal ownership follows the `prompt.submit` ACK DISPOSITION (Collision #A, A1):
    `terminals_to_skip` predecessor terminals are skipped before the step OWNS the next one.
    """

    # Disposition → predecessor terminals to skip before OUR turn's terminal (Collision #A):
    # streaming = our turn is the current execution (0); queued = one interrupted predecessor
    # terminal precedes ours (1); steered never reaches settlement (no independent execution).
    _SKIP_BY_DISPOSITION = {"streaming": 0, "queued": 1}

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.settled = threading.Event()
        self.frames: queue.Queue[JsonDict] = queue.Queue()
        # Ownership follows POST-ACK ordering, NOT registration order. `prompt.submit` is
        # acknowledged (an id-correlated RPC result) BEFORE Hermes streams OUR accepted turn's
        # frames. ARMING happens ON THE STDOUT THREAD when this submission OBSERVES its own ACK
        # frame in emission order (`observe_ack_frame`) — arm is then strictly ordered before any
        # later frame's fold on the same single reader thread.
        #
        # BUT a pre-ACK terminal is NOT reliably safe to DROP-and-forget (Codex Finding 1, peer
        # round): for the `queued` disposition the ACK is CONSTRUCTED under history_lock
        # (tui_gateway/server.py:5091,5098) but WRITTEN LATER by the entry loop (entry.py:371-373)
        # AFTER the lock releases — so the interrupted predecessor's `message.complete` can legally
        # land on stdout BEFORE the queued ACK (server.py:9145-9147). If that pre-ACK terminal were
        # DROPPED WITHOUT counting it, arming skip=1 would then eat the STEP'S OWN terminal → the
        # runner hangs (there is NO whole-step timeout). So pre-ACK TERMINALS are BUFFERED in
        # emission order (`_pre_ack_terminals`) and, on arm, DECREMENT the skip. An invariant makes
        # the accounting exact: OUR OWN turn's terminal ALWAYS arrives AFTER the ACK (Hermes streams
        # the accepted turn only after acknowledging it), so a pre-ACK terminal is NEVER ours — it
        # is dropped after counting, never settled on. Thus streaming (skip 0): a pre-ACK
        # predecessor terminal is dropped and our post-ACK terminal settles; queued (skip 1): the
        # pre-ACK predecessor terminal decrements the skip to 0 and our post-ACK terminal settles.
        # Pre-ACK STREAM frames (predecessor deltas) are still NOT enqueued to `on_event` (that
        # would corrupt the worker turn's transcript with foreign text, Codex Finding 2); only
        # terminals are buffered, and only for the skip accounting.
        self._ack_request_id: int | None = None
        self._armed = False
        self._terminals_to_skip = 0
        # Pre-ACK terminals (kind, payload) recorded in emission order while unarmed, replayed
        # through the same skip/settle logic on arm.
        self._pre_ack_terminals: list[tuple[str, JsonDict]] = []
        # Settlement outcome, set once under `_lock`:
        self.terminal_kind: str | None = None  # "complete"|"error"|"dead"
        self.terminal_payload: JsonDict = {}

    def expect_ack(self, request_id: int) -> None:
        """Register the `prompt.submit` request id BEFORE it is sent, so this submission arms
        itself the instant it observes the matching ACK response frame on the stdout thread."""
        with self._lock:
            self._ack_request_id = request_id

    def observe_ack_frame(self, frame: JsonDict) -> bool:
        """Called ON THE STDOUT THREAD for a RESPONSE frame (carries `id`). If it is our
        `prompt.submit` ACK, ARM per its disposition and return True; a steered ACK (no
        independent execution) settles errored immediately. Otherwise a no-op returning False.

        On arm, any terminals buffered pre-ACK (`_pre_ack_terminals`) DECREMENT the skip — a
        pre-ACK terminal is never ours (our turn streams only after the ACK), so it is counted and
        dropped, never settled. That leaves OUR own post-ACK terminal to settle live."""
        with self._lock:
            if self._armed or self.settled.is_set() or self._ack_request_id is None:
                return False
            if frame.get("id") != self._ack_request_id:
                return False
            result = frame.get("result")
            disposition = str(result.get("status") or "") if isinstance(result, dict) else ""
            if disposition == "steered":
                # Delivered by steering the active execution; no independent turn to own.
                self.terminal_kind = "steered"
                self.settled.set()
                return True
            self._armed = True
            self._terminals_to_skip = self._SKIP_BY_DISPOSITION.get(disposition, 0)
            # Count each buffered pre-ACK terminal against the skip (a queued predecessor that
            # raced ahead of the ACK), then DROP it — a pre-ACK terminal is never ours. Our own
            # (post-ACK) terminal folds later on this same reader thread and settles.
            buffered = self._pre_ack_terminals
            self._pre_ack_terminals = []
            for _kind, _payload in buffered:
                if self._terminals_to_skip > 0:
                    self._terminals_to_skip -= 1
            return True

    def _settle_terminal_locked(self, kind: str, payload: JsonDict) -> None:
        """Settle on an OWNED terminal (caller holds `_lock`, submission armed, skip exhausted)."""
        self.terminal_kind = kind
        self.terminal_payload = payload
        self.settled.set()

    def fold_frame(self, frame: JsonDict) -> None:
        """Called ON THE STDOUT THREAD for an EVENT frame (no `id`). While unarmed, BUFFER a
        terminal (for the skip accounting) but drop a stream frame (predecessor deltas must not
        corrupt the owned transcript). Once armed, own a frame only past every skipped predecessor
        terminal; the first owned terminal settles. The pool's `on_frame` wrapper never lets this
        raise to the reader."""
        params = frame.get("params")
        if not isinstance(params, dict):
            return
        event_type = params.get("type")
        if not isinstance(event_type, str):
            return
        raw_payload = params.get("payload")
        payload = raw_payload if isinstance(raw_payload, dict) else {}
        is_terminal = event_type in ("error", "message.complete")
        kind = "error" if event_type == "error" else "complete"
        with self._lock:
            if self.settled.is_set():
                return
            if not self._armed:
                # Pre-ACK: RECORD a terminal in order (it may be a predecessor to skip OR, once
                # the ACK arms, our own) but never enqueue a predecessor delta (Finding 2).
                if is_terminal:
                    self._pre_ack_terminals.append((kind, payload))
                return
            if self._terminals_to_skip > 0:
                # Still draining a queued predecessor's frames; drop them. A terminal here is the
                # predecessor's — count it down but never enqueue or settle.
                if is_terminal:
                    self._terminals_to_skip -= 1
                return
            # This frame is OURS: enqueue for on_event, and settle on our terminal.
            self.frames.put(frame)
            if not is_terminal:
                return
            self._settle_terminal_locked(kind, payload)

    def fold_dead(self) -> None:
        """Called on child death (deliver path). Settles errored (child reset)."""
        with self._lock:
            if self.settled.is_set():
                return
            self.terminal_kind = "dead"
            self.settled.set()


class EmployeeChildPool:
    """Relay-consumer adapter: builds + subscribes to a per-employee EmployeeChildRegistry.

    The registry owns the children and the lifecycle; this pool is the registry's
    `ChildFrameSubscriber` (relay wiring) plus the step-submission settlement half. Its public
    surface and constructor are unchanged; historical privates are proxied to the registry.
    """

    def __init__(
        self,
        *,
        hermes_python: Path,
        planner_home: Path,
        base_env: Mapping[str, str],
        relay: EmployeeChildRelay,
        loop: asyncio.AbstractEventLoop,
        spawn: SpawnFn = spawn_popen,
        chief_entity_id: str = CHIEF_OF_STAFF_ENTITY_ID,
        ready_timeout: float = READY_TIMEOUT_DEFAULT,
        request_timeout: float = REQUEST_TIMEOUT_DEFAULT,
        on_stored_session_bound: Callable[[str, str, str | None], None] | None = None,
        stored_session_resolver: Callable[[str], str | None] | None = None,
        reader_factory: ReaderFactory | None = None,
    ) -> None:
        self._hermes_python = hermes_python
        self._planner_home = planner_home
        self._base_env = dict(base_env)
        self._relay = relay
        self._loop = loop
        self._spawn = spawn
        self._chief_entity_id = chief_entity_id
        self._request_timeout = request_timeout

        # Per-employee active step submission (S3 §1.4). At most one is registered per employee at
        # a time; the runner registers before submit and unregisters on settle. This is the pool's
        # OWN (step-fold) state — the registry owns everything else.
        self._turn_submissions: dict[str, TurnSubmission] = {}
        self._turn_submissions_lock = threading.Lock()

        # Build the registry, injecting THIS pool as the frame subscriber + the default (or a
        # composition-injected) reader factory. Passing a half-built `self` is safe: no subscriber
        # or factory method runs during construction.
        self._registry = EmployeeChildRegistry(
            reader_factory=reader_factory or self._default_reader_factory,
            subscriber=self,
            identity_env_strategy=self._build_identity_env,
            session_source=RELAY_SESSION_SOURCE,
            session_cols=SESSION_COLS,
            on_stored_session_bound=on_stored_session_bound,
            stored_session_resolver=stored_session_resolver,
            ready_timeout=ready_timeout,
            request_timeout=request_timeout,
        )

    # --- registry executors + private-state proxies (frozen test reads) ----

    @property
    def init_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        return self._registry.init_executor

    @property
    def shutdown_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        return self._registry.shutdown_executor

    @property
    def _records(self) -> dict[str, EmployeeChildRecord]:
        return self._registry._records

    @property
    def _stored_session_id_by_employee(self) -> dict[str, str]:
        return self._registry._stored_session_id_by_employee

    @property
    def _live_session_id_by_employee(self) -> dict[str, str]:
        return self._registry._live_session_id_by_employee

    @property
    def _closing(self) -> bool:
        return self._registry._closing

    @_closing.setter
    def _closing(self, value: bool) -> None:
        self._registry._closing = value

    @property
    def _ready_timeout(self) -> float:
        return self._registry._ready_timeout

    @_ready_timeout.setter
    def _ready_timeout(self, value: float) -> None:
        self._registry._ready_timeout = value

    # --- identity env strategy + default reader factory (relay flavor) -----

    def _build_identity_env(self, employee_entity_id: str) -> dict[str, str]:
        env = dict(self._base_env)
        env.pop(_PLAN_TICKET_ID_ENV, None)
        env.pop(_HERMES_TUI_SKILLS_ENV, None)
        env["HERMES_PYTHON_SRC_ROOT"] = str(hermes_src_root(self._hermes_python))
        env["HERMES_HOME"] = str(self._planner_home)
        if employee_entity_id == self._chief_entity_id:
            env["PLAN_ACTOR"] = "chief"
        else:
            env[_PLAN_TICKET_ID_ENV] = employee_entity_id
            env["PLAN_ACTOR"] = "worker"
        return env

    def _default_reader_factory(
        self,
        *,
        generation: int,
        employee_entity_id: str,
        env: Mapping[str, str],
        on_deliver: Callable[[JsonDict], None],
        on_fold: Callable[[JsonDict], None],
        on_dead: Callable[[], None],
    ) -> ChildReader:
        return RawFrameChildTransport(
            hermes_python=str(self._hermes_python),
            env=env,
            on_frame=on_deliver,
            on_dead=on_dead,
            spawn=self._spawn,
            on_fold=on_fold,
            allocate_request_id=lambda: self._relay.next_child_request_id(generation),
        )

    # --- ChildFrameSubscriber (the relay consumer) -------------------------

    def attach(self, *, generation: int, employee_entity_id: str, reader: ChildReader) -> None:
        self._relay.register_child(generation, employee_entity_id, reader)

    def deliver(self, *, generation: int, employee_entity_id: str, frame: JsonDict) -> None:
        # sink1: QUEUE deliver_child_frame on the loop FIRST (load-bearing ordering — see the
        # reader's _stdout_loop). The reader runs this before settling its own responders (sink2),
        # so a failed session-RPC error frame is delivered before the initializer (woken by the
        # responder) can schedule an unregister.
        self._loop.call_soon_threadsafe(self._relay.deliver_child_frame, generation, frame)

    def fold(self, *, generation: int, employee_entity_id: str, frame: JsonDict) -> None:
        # sink3: drive the employee's active step settlement, off the SQLite path, in emission
        # order on the reader thread. A RESPONSE frame (has `id`) is offered as its possible
        # prompt.submit ACK; an EVENT frame is folded as an owned/predecessor frame. Swallow any
        # exception so a settlement bug never kills the reader.
        submission = self._turn_submission_for(employee_entity_id)
        if submission is not None:
            try:
                if "id" in frame:
                    submission.observe_ack_frame(frame)
                else:
                    submission.fold_frame(frame)
            except BaseException:  # noqa: BLE001 — never kill the reader (§1.4)
                pass

    def on_dead(self, *, generation: int, employee_entity_id: str) -> None:
        self._loop.call_soon_threadsafe(self._relay.deliver_child_death, generation)
        # Settle a running step errored on child death (child reset). Off the SQLite path.
        submission = self._turn_submission_for(employee_entity_id)
        if submission is not None:
            try:
                submission.fold_dead()
            except BaseException:  # noqa: BLE001
                pass

    def detach(self, *, generation: int) -> None:
        # On-loop retire (spawn except path / rebind discard): lands AFTER any deliver already
        # queued on the loop for this generation.
        self._loop.call_soon_threadsafe(self._relay.unregister_child, generation)

    def detach_now(self, *, generation: int) -> None:
        # Synchronous retire (closing-race / shutdown in-progress slots).
        self._relay.unregister_child(generation)

    # --- lifecycle delegations to the registry -----------------------------

    def child_for_employee(self, employee_entity_id: str) -> EmployeeChildRecord:
        return self._registry.get_or_spawn(employee_entity_id)

    def adopt_stored_session(self, employee_entity_id: str, stored_session_id: str) -> None:
        self._registry.adopt_stored_session(employee_entity_id, stored_session_id)

    def rebind_fresh_session(
        self, employee_entity_id: str, old_live_session_id: str
    ) -> tuple[str, str]:
        return self._registry.rebind_fresh_session(employee_entity_id, old_live_session_id)

    def live_session_id_for(self, employee_entity_id: str) -> str | None:
        return self._registry.live_session_id_for(employee_entity_id)

    def interrupt_live_turn(self, employee_entity_id: str, *, deadline: float) -> None:
        self._registry.interrupt_live_turn(employee_entity_id, deadline=deadline)

    def shutdown(self, *, deadline: float) -> None:
        self._registry.shutdown(deadline=deadline)

    # --- step submission seam (S3 §1.3-§1.4) -------------------------------

    def register_turn_submission(self, employee_entity_id: str) -> TurnSubmission:
        """Register the active step submission for an employee BEFORE its `prompt.submit` streams
        frames, so the fold sink can fold them into settlement. Registered UNARMED; the submission
        arms itself on its ACK. At most one is active per employee; a second registration replaces
        the first (the caller owns the one-at-a-time discipline via the runner's active-ticket
        guard)."""
        submission = TurnSubmission()
        with self._turn_submissions_lock:
            self._turn_submissions[employee_entity_id] = submission
        return submission

    def unregister_turn_submission(
        self, employee_entity_id: str, submission: TurnSubmission
    ) -> None:
        """Drop the submission once settled (idempotent; only drops if still the current one)."""
        with self._turn_submissions_lock:
            if self._turn_submissions.get(employee_entity_id) is submission:
                self._turn_submissions.pop(employee_entity_id, None)

    def _turn_submission_for(self, employee_entity_id: str) -> TurnSubmission | None:
        with self._turn_submissions_lock:
            return self._turn_submissions.get(employee_entity_id)

    def submit_step_prompt(
        self,
        record: EmployeeChildRecord,
        live_session_id: str,
        text: str,
        submission: TurnSubmission,
    ) -> str:
        """Submit `prompt.submit` on the employee's CAPTURED child reader and return the ACK
        DISPOSITION ("streaming"|"queued"|"steered").

        The submission is told its `prompt.submit` request id BEFORE the frame is sent (via
        `on_request_id=submission.expect_ack`), so it ARMS itself the instant it observes the
        matching ACK response frame on the reader thread (race-free). The request is issued on the
        record's OWN reader — never re-resolved by employee — so a concurrent rebind cannot redirect
        it (the original target-transport discipline).

        A native busy RPC error (BUSY_CODE 4009) surfaces as a RawFrameTransportError carrying the
        code, which the caller maps to SharedGatewayBusy."""
        result = record.transport.request(
            "prompt.submit",
            {"session_id": live_session_id, "text": text},
            timeout=self._request_timeout,
            on_request_id=submission.expect_ack,
        )
        disposition = str(result.get("status") or "")
        if disposition not in ("streaming", "queued", "steered"):
            raise RawFrameTransportError(f"unexpected prompt.submit disposition: {disposition!r}")
        return disposition
