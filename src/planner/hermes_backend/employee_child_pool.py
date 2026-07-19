"""EmployeeChildPool: owns N RawFrameChildTransports, one per employee entity.

Spawns on demand with identity env; creates/owns exactly one session per child;
respawns dead children on next demand; is the SOLE issuer of session.create/
session.resume; shuts all down within one shared deadline. Owns its OWN bounded
initialization executor and a separate reserved shutdown path (plan §1 R3-G). NEVER
holds a blocking call under its global lock (R-2).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from time import monotonic as _monotonic
from typing import TYPE_CHECKING, Final

from planner.chat.service import CHIEF_OF_STAFF_ENTITY_ID
from planner.hermes_backend.raw_frame_transport import (
    RawFrameChildTransport,
    RawFrameTransportError,
)
from planner.minds.config import hermes_src_root
from planner.minds.gateway import (
    READY_TIMEOUT_DEFAULT,
    REQUEST_TIMEOUT_DEFAULT,
    SHUTDOWN_GRACE_DEFAULT,
    JsonDict,
    SpawnFn,
    spawn_popen,
)

if TYPE_CHECKING:
    from planner.hermes_backend.employee_child_relay import EmployeeChildRelay

SESSION_COLS: Final = 100
RELAY_SESSION_SOURCE: Final = "panels-relay"
CHILD_CLEANUP_BUDGET_SECONDS: Final = SHUTDOWN_GRACE_DEFAULT
INIT_EXECUTOR_MAX_WORKERS: Final = 8

_PLAN_TICKET_ID_ENV: Final = "PLAN_TICKET_ID"
_HERMES_TUI_SKILLS_ENV: Final = "HERMES_TUI_SKILLS"


class PoolError(Exception):
    """Pool-level failure (closing, spawn/session failure surfaced to the caller)."""


@dataclass
class EmployeeChildRecord:
    """The employee→child record (only S1-consumed state)."""

    employee_entity_id: str
    transport: RawFrameChildTransport
    child_generation: int
    stored_session_id: str


@dataclass
class _InitSlot:
    """The per-employee init latch (R-2), teardown-capable (R3-B)."""

    done: threading.Event
    record: EmployeeChildRecord | None = None
    error: BaseException | None = None
    transport: RawFrameChildTransport | None = None
    generation: int | None = None


class _PoolSessionResponder:
    """Observes the matching response frame for one pool-issued RPC WITHOUT consuming
    it from the ordered routing (plan §1, R-1/R3-D). Waits on (done OR dead_event)."""

    def __init__(self, request_id: int) -> None:
        self._request_id = request_id
        self.done = threading.Event()
        self.frame: JsonDict | None = None

    def observe(self, frame: JsonDict) -> None:
        if self.done.is_set():
            return
        if frame.get("id") == self._request_id and ("result" in frame or "error" in frame):
            self.frame = frame
            self.done.set()


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
    ) -> None:
        self._hermes_python = hermes_python
        self._planner_home = planner_home
        self._base_env = dict(base_env)
        self._relay = relay
        self._loop = loop
        self._spawn = spawn
        self._chief_entity_id = chief_entity_id
        self._ready_timeout = ready_timeout
        self._request_timeout = request_timeout
        self._on_stored_session_bound = on_stored_session_bound
        self._stored_session_resolver = stored_session_resolver

        self._lock = threading.Lock()
        # Per-employee active step submission (S3 §1.4). At most one is registered per
        # employee at a time; the runner registers before submit and unregisters on settle.
        self._turn_submissions: dict[str, TurnSubmission] = {}
        self._turn_submissions_lock = threading.Lock()
        self._records: dict[str, EmployeeChildRecord] = {}
        self._init_slots: dict[str, _InitSlot] = {}
        self._generation_counter = 0
        self._stored_session_id_by_employee: dict[str, str] = {}
        # The current LIVE session id per employee (record carries only the stored id).
        # Populated at first create/resume and every rebind; used to detect a stale rebind.
        self._live_session_id_by_employee: dict[str, str] = {}
        # Per-employee serialization latch for rebind_fresh_session, so concurrent
        # new-conversation requests for one employee do not each mint a live session.
        self._rebind_locks: dict[str, threading.Lock] = {}
        self._rebind_locks_guard = threading.Lock()
        self._closing = False

        # Per-child observer registries, so the permanent on_frame can feed the
        # responder for a pool-issued RPC without a callback swap.
        self._responders: dict[int, list[_PoolSessionResponder]] = {}
        self._responders_lock = threading.Lock()

        self._init_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=INIT_EXECUTOR_MAX_WORKERS, thread_name_prefix="relay-pool-init"
        )
        self._shutdown_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="relay-pool-shutdown"
        )

    @property
    def init_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        return self._init_executor

    @property
    def shutdown_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        return self._shutdown_executor

    # --- durable session adoption (S2b) ------------------------------------

    def adopt_stored_session(self, employee_entity_id: str, stored_session_id: str) -> None:
        """Seed the durable stored-session binding for an employee BEFORE its first spawn,
        so the first `_create_or_resume_session` RESUMES it instead of creating fresh.
        First-write-wins and a no-op once the employee already has a live child (never
        rebind a running child underneath itself)."""
        with self._lock:
            if employee_entity_id in self._records:
                return
            self._stored_session_id_by_employee.setdefault(
                employee_entity_id, stored_session_id
            )

    # --- spawn on demand ---------------------------------------------------

    def child_for_employee(self, employee_entity_id: str) -> EmployeeChildRecord:
        """The single spawn-on-demand entry point. Called only from the init executor.
        No blocking call runs under `_lock` (R-2)."""
        with self._lock:
            if self._closing:
                raise PoolError("relay pool is closing")
            existing = self._records.get(employee_entity_id)
            if existing is not None and existing.transport.alive:
                return existing
            slot = self._init_slots.get(employee_entity_id)
            if slot is not None:
                own = False
            else:
                slot = _InitSlot(done=threading.Event())
                self._init_slots[employee_entity_id] = slot
                own = True
            stale = existing if existing is not None and not existing.transport.alive else None

        if not own:
            slot.done.wait()
            if slot.error is not None:
                raise slot.error
            assert slot.record is not None
            return slot.record

        # We own the slot: perform the ENTIRE blocking spawn OFF the lock.
        try:
            if stale is not None:
                self._shutdown_transport_bounded(stale.transport)
            record = self._spawn_and_bind(employee_entity_id, slot)
        except BaseException as exc:  # noqa: BLE001 — record and re-raise via slot
            with self._lock:
                slot.error = exc
                self._init_slots.pop(employee_entity_id, None)
            slot.done.set()
            raise

        # Publish/discard under `_lock` (short) with a `_closing` re-check (R3-B).
        with self._lock:
            if self._closing:
                slot.error = PoolError("relay pool is closing")
                self._init_slots.pop(employee_entity_id, None)
                published_transport = slot.transport
                published_generation = slot.generation
            else:
                self._records[employee_entity_id] = record
                slot.record = record
                self._init_slots.pop(employee_entity_id, None)
                published_transport = None
                published_generation = None
        slot.done.set()
        if slot.error is not None:
            # Closing raced the publish: tear down the just-built child, unregister.
            if published_transport is not None:
                self._shutdown_transport_bounded(published_transport)
            if published_generation is not None:
                self._relay.unregister_child(published_generation)
            raise slot.error
        return record

    def _spawn_and_bind(self, employee_entity_id: str, slot: _InitSlot) -> EmployeeChildRecord:
        with self._lock:
            self._generation_counter += 1
            generation = self._generation_counter
        env = self._env_for_employee(employee_entity_id)
        responder_registry = self._responders

        def on_frame(frame: JsonDict) -> None:
            # Ordering is load-bearing: QUEUE deliver_child_frame on the loop FIRST, THEN
            # wake the pool responder. If we woke the responder first, a failed session-RPC
            # would unblock the initializer, whose except-path queues unregister_child on
            # the loop — and that unregister could land AHEAD of this frame's delivery in
            # the loop FIFO, dropping the error response (binding already retired). Queuing
            # the delivery before the responder wakes guarantees FIFO delivers the error
            # response (fan-out + tee) before any unregister the initializer schedules.
            self._loop.call_soon_threadsafe(self._relay.deliver_child_frame, generation, frame)
            with self._responders_lock:
                observers = list(responder_registry.get(generation, ()))
            for observer in observers:
                observer.observe(frame)
            # Third sink (S3 §1.4): drive the employee's active step settlement, OFF the SQLite
            # path, IN emission order on this single reader thread. A RESPONSE frame (has `id`)
            # is offered to the submission as its possible `prompt.submit` ACK — arming there is
            # strictly ordered before any later event frame's fold (race-free arm, Codex
            # Findings 1/2). An EVENT frame (no `id`) is folded as an owned/predecessor frame.
            # Swallow any exception so a settlement bug never kills the stdout reader.
            submission = self._turn_submission_for(employee_entity_id)
            if submission is not None:
                try:
                    if "id" in frame:
                        submission.observe_ack_frame(frame)
                    else:
                        submission.fold_frame(frame)
                except BaseException:  # noqa: BLE001 — never kill the reader (§1.4)
                    pass

        def on_dead() -> None:
            self._loop.call_soon_threadsafe(self._relay.deliver_child_death, generation)
            # Settle a running step errored on child death (child reset, §1.4). Off the
            # SQLite path; swallow to protect the reader teardown.
            submission = self._turn_submission_for(employee_entity_id)
            if submission is not None:
                try:
                    submission.fold_dead()
                except BaseException:  # noqa: BLE001
                    pass

        transport = RawFrameChildTransport(
            hermes_python=str(self._hermes_python),
            env=env,
            on_frame=on_frame,
            on_dead=on_dead,
            spawn=self._spawn,
        )
        # Publish the partial transport + generation the instant it exists, and re-check
        # `_closing` in the SAME locked section (R3-B, R3-round3-1).
        with self._lock:
            if self._closing:
                closing_now = True
            else:
                closing_now = False
                slot.transport = transport
                slot.generation = generation
        if closing_now:
            self._shutdown_transport_bounded(transport)
            raise PoolError("relay pool is closing")

        registered = False
        try:
            self._relay.register_child(generation, employee_entity_id, transport)
            registered = True
            transport.start_reading()
            transport.wait_ready(self._ready_timeout)
            live_session_id, stored_session_id, created_fresh = (
                self._create_or_resume_session(transport, employee_entity_id, generation)
            )
            if created_fresh:
                # A never-seen stored id was minted (not a resume of an adopted key): persist
                # the fresh binding BEFORE publishing it, so a restart resumes THIS session,
                # not a stale one. Fail-closed: this call is INSIDE the guarded block, so a
                # persistence failure tears down + unregisters the just-bound child (below)
                # rather than leaving an unpersisted live owner of the durable session — a
                # later spawn must not fork a second owner of the same session (S2B-OWN-001).
                # First create has no predecessor durable id (old = None).
                self._notify_stored_session_bound(employee_entity_id, stored_session_id, None)
            # Publish the binding to the in-memory maps only AFTER any required persist landed,
            # so a failed persist leaves NEITHER a live child NOR a stored-id map entry that a
            # restart would resume without a matching durable DB write.
            with self._lock:
                self._stored_session_id_by_employee[employee_entity_id] = stored_session_id
                self._live_session_id_by_employee[employee_entity_id] = live_session_id
        except BaseException:
            self._shutdown_transport_bounded(transport)
            if registered:
                # Retire the binding on the LOOP (call_soon_threadsafe) so it lands
                # AFTER any deliver_child_frame already queued for this generation
                # (e.g. a failed session-RPC error response) — that frame must still
                # fan out + be tee'd before the binding is gone (defect #2).
                self._loop.call_soon_threadsafe(self._relay.unregister_child, generation)
            raise
        return EmployeeChildRecord(
            employee_entity_id=employee_entity_id,
            transport=transport,
            child_generation=generation,
            stored_session_id=stored_session_id,
        )

    def _env_for_employee(self, employee_entity_id: str) -> dict[str, str]:
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

    def _create_or_resume_session(
        self, transport: RawFrameChildTransport, employee_entity_id: str, generation: int
    ) -> tuple[str, str, bool]:
        """Return `(live_session_id, stored_session_id, created_fresh)`. `created_fresh` is
        True only when a brand-new stored id was minted (session.create), so the caller
        persists that fresh binding; a resume of an adopted/held key is not fresh."""
        with self._lock:
            held = self._stored_session_id_by_employee.get(employee_entity_id)
        if held is None and self._stored_session_resolver is not None:
            # On-demand adoption (S3 §3.1): no eagerly-adopted key is held, so ask the
            # composition-owned resolver for this employee's persisted durable key. The
            # resolver runs OFF the pool lock (it opens its own short-lived DB connection).
            # A non-empty return seeds the resume branch (an adopted key, NOT created fresh —
            # so persistence does not re-bind it); a None/empty return falls through to
            # session.create.
            resolved = self._stored_session_resolver(employee_entity_id)
            if isinstance(resolved, str) and resolved:
                held = resolved
        if held is not None:
            result = self._transport_request(
                transport,
                generation,
                "session.resume",
                {"session_id": held},
                self._request_timeout,
            )
            live = str(result.get("session_id") or held)
            return live, str(result.get("resumed") or held), False
        result = self._transport_request(
            transport,
            generation,
            "session.create",
            {"source": RELAY_SESSION_SOURCE, "cols": SESSION_COLS},
            self._request_timeout,
        )
        # The own-session-resume proof depends on a durable stored id; require a
        # non-empty string. NO fallback to the live session_id (uncontracted).
        stored = result.get("stored_session_id")
        if not isinstance(stored, str) or not stored:
            raise RawFrameTransportError("session.create returned no non-empty stored_session_id")
        live = str(result.get("session_id") or stored)
        return live, stored, True

    # --- new-conversation rebind (S2b) -------------------------------------

    def rebind_fresh_session(
        self, employee_entity_id: str, old_live_session_id: str
    ) -> tuple[str, str]:
        """End the employee child's current session binding and bind a fresh one, on the
        child's OWN transport (never through the downstream denylist seam): close the old
        live session first, then session.create a fresh one. Returns
        `(new_live_session_id, new_stored_session_id)`.

        Per-employee serialized: a concurrent rebind whose `old_live_session_id` no longer
        matches the current live id observes the already-fresh binding and returns it
        without re-closing/re-creating (a stale no-op)."""
        rebind_lock = self._rebind_lock_for(employee_entity_id)
        with rebind_lock:
            with self._lock:
                if self._closing:
                    raise PoolError("relay pool is closing")
                record = self._records.get(employee_entity_id)
                current_live = self._live_session_id_by_employee.get(employee_entity_id)
            if record is None or not record.transport.alive:
                raise PoolError(f"no live child for employee {employee_entity_id!r}")
            if current_live is not None and current_live != old_live_session_id:
                # A rebind already advanced past this caller's old live id: no-op, return
                # the current fresh binding.
                return current_live, record.stored_session_id
            generation = record.child_generation
            self._transport_request(
                record.transport,
                generation,
                "session.close",
                {"session_id": old_live_session_id},
                self._request_timeout,
            )
            result = self._transport_request(
                record.transport,
                generation,
                "session.create",
                {"source": RELAY_SESSION_SOURCE, "cols": SESSION_COLS},
                self._request_timeout,
            )
            new_stored = result.get("stored_session_id")
            if not isinstance(new_stored, str) or not new_stored:
                raise RawFrameTransportError(
                    "session.create returned no non-empty stored_session_id"
                )
            new_live = str(result.get("session_id") or new_stored)
            old_stored = record.stored_session_id
            # Persist the fresh durable binding BEFORE advancing any in-memory state
            # (S2B-OWN-001, rebind half). Fail-closed (§3.2, Codex Finding 5): the OLD live
            # session is ALREADY CLOSED above, so simply leaving the in-memory/DB maps on the
            # old key is NOT safe — a resume of the old key would hit a closed session. If
            # persistence raises, TEAR THE CHILD DOWN so it respawns clean on next demand and
            # RESUMES the still-durable OLD stored key (session.close closes the live session,
            # not the durable stored session); the in-memory maps are cleared with the child so
            # they never point at the unpersisted fresh binding, and the DB still holds the OLD
            # durable key. The freshly-created live session dies with the torn-down child.
            try:
                self._notify_stored_session_bound(employee_entity_id, new_stored, old_stored)
            except BaseException:
                # Pass generation N's OWN transport (a distinct object) so the cleanup shuts down
                # exactly N's child, never whatever a concurrent respawn has since published.
                self._discard_child_after_rebind_persist_failure(
                    employee_entity_id, generation, record.transport
                )
                raise
            with self._lock:
                self._stored_session_id_by_employee[employee_entity_id] = new_stored
                self._live_session_id_by_employee[employee_entity_id] = new_live
                record.stored_session_id = new_stored
            return new_live, new_stored

    def _discard_child_after_rebind_persist_failure(
        self,
        employee_entity_id: str,
        generation: int,
        generation_transport: RawFrameChildTransport,
    ) -> None:
        """Tear generation `generation`'s child down after ITS rebind persistence failure so the
        next demand respawns clean and resumes the still-durable OLD stored key. Leaves the OLD
        durable DB key intact.

        GENERATION-GUARDED (Codex Finding 3): `child_for_employee` respawns a dead child using only
        `_lock`/`_init_slots`, NEVER `rebind_lock` (`:286-315`, publish at `:331`), so a NEWER
        generation N+1 can publish while THIS rebind (N) blocks in its persist callback (up to
        SQLite's 5s busy timeout). N's cleanup must therefore touch NOTHING that belongs to N+1:
        the record pop, the live-id clear, and the relay unregister are ALL conditional on the
        current record still being generation N (or absent). N's OWN transport (a distinct object,
        passed in) is always shut down — it is the failed rebind's child regardless — but never
        N+1's transport (re-reading `_records` for the transport was the bug that killed a healthy
        newer generation)."""
        with self._lock:
            record = self._records.get(employee_entity_id)
            current_is_this_generation = (
                record is not None and record.child_generation == generation
            )
            if current_is_this_generation:
                self._records.pop(employee_entity_id, None)
                # Clear the fresh (unpersisted) live id ONLY when the current binding is still N's;
                # leave the stored-id map on the OLD durable key so a respawn RESUMES it. When N+1
                # already published, its live id is left INTACT.
                self._live_session_id_by_employee.pop(employee_entity_id, None)
        # Shut down N's OWN transport (idempotent; a respawn may already have shut the stale one).
        self._shutdown_transport_bounded(generation_transport)
        # Retire only N's relay binding. `unregister_child` is generation-keyed, so it is a no-op
        # for N+1 even if a respawn published between the check above and this call.
        self._loop.call_soon_threadsafe(self._relay.unregister_child, generation)

    def _rebind_lock_for(self, employee_entity_id: str) -> threading.Lock:
        with self._rebind_locks_guard:
            lock = self._rebind_locks.get(employee_entity_id)
            if lock is None:
                lock = threading.Lock()
                self._rebind_locks[employee_entity_id] = lock
            return lock

    def _notify_stored_session_bound(
        self,
        employee_entity_id: str,
        new_stored_session_id: str,
        old_stored_session_id: str | None,
    ) -> None:
        """Publish a fresh durable binding. The callback carries BOTH the new stored id and
        the OLD one (None on first-create) so a ticket ownership-CAS writer can pass
        `expected → candidate` and assert the write took (§3.2, Codex Finding 5)."""
        callback = self._on_stored_session_bound
        if callback is not None:
            callback(employee_entity_id, new_stored_session_id, old_stored_session_id)

    # --- step submission seam (S3 §1.3-§1.4) -------------------------------

    def register_turn_submission(self, employee_entity_id: str) -> TurnSubmission:
        """Register the active step submission for an employee BEFORE its `prompt.submit`
        streams frames, so the stdout-thread sink can fold them into settlement. Registered
        UNARMED; the caller `arm`s it with the ACK disposition once known. At most one is
        active per employee; a second registration replaces the first (the caller owns the
        one-at-a-time discipline via the runner's active-ticket guard)."""
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
        """Submit `prompt.submit` on the employee's child transport and return the ACK
        DISPOSITION ("streaming"|"queued"|"steered"). The native submit params + the ACK
        `status` field are captured from real source: sessions/service.py:507-508 (params)
        and :556 (`disposition = str(result.get("status") ...)`).

        The submission is told its `prompt.submit` request id BEFORE the frame is sent, so it
        ARMS itself the instant it observes the matching ACK response frame on the stdout thread
        (race-free vs. a runner-thread arm; Codex Findings 1/2). The disposition is still
        returned so the caller can short-circuit `steered` without waiting on a terminal.

        A native busy RPC error (BUSY_CODE 4009) is surfaced by `_transport_request` as a
        RawFrameTransportError carrying the code, which the caller maps to SharedGatewayBusy
        (mirrors shared_gateway.py:836-837)."""
        result = self._transport_request(
            record.transport,
            record.child_generation,
            "prompt.submit",
            {"session_id": live_session_id, "text": text},
            self._request_timeout,
            on_request_id=submission.expect_ack,
        )
        disposition = str(result.get("status") or "")
        if disposition not in ("streaming", "queued", "steered"):
            raise RawFrameTransportError(f"unexpected prompt.submit disposition: {disposition!r}")
        return disposition

    def live_session_id_for(self, employee_entity_id: str) -> str | None:
        """The employee's CURRENT live session id, if a turn is (or was last) live. Used by
        the step gateway for `prompt.submit` targeting and by `interrupt_live_turn`."""
        with self._lock:
            return self._live_session_id_by_employee.get(employee_entity_id)

    def interrupt_live_turn(self, employee_entity_id: str, *, deadline: float) -> None:
        """Issue `session.interrupt {session_id: <live id>}` on the employee's child (§1.6).
        Resolves the employee's CURRENT live session id (the correct target — the record
        carries only the stored id); a no-op when no live id is known (no turn in flight).
        Converts the absolute `deadline` to the remaining relative timeout the transport
        expects. `session.interrupt` params captured from sessions/service.py:679-681."""
        with self._lock:
            record = self._records.get(employee_entity_id)
            live_session_id = self._live_session_id_by_employee.get(employee_entity_id)
        if record is None or not record.transport.alive or not live_session_id:
            return
        remaining = max(0.0, deadline - _monotonic())
        try:
            self._transport_request(
                record.transport,
                record.child_generation,
                "session.interrupt",
                {"session_id": live_session_id},
                remaining,
            )
        except RawFrameTransportError:
            # Best-effort interrupt at shutdown; a dead/unresponsive child needs no interrupt.
            pass

    def _transport_request(
        self,
        transport: RawFrameChildTransport,
        generation: int,
        method: str,
        params: JsonDict,
        timeout: float,
        on_request_id: Callable[[int], None] | None = None,
    ) -> JsonDict:
        rid = self._relay.next_child_request_id(generation)
        # Hand the caller the allocated id BEFORE the frame is enqueued, so a step submission
        # can register it as its pending ACK id before any response can be observed (race-free
        # arm-on-ACK, §1.4). Runs synchronously here on the submitting thread.
        if on_request_id is not None:
            on_request_id(rid)
        responder = _PoolSessionResponder(rid)
        with self._responders_lock:
            self._responders.setdefault(generation, []).append(responder)
        try:
            transport.enqueue_frame(
                {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
            )
            deadline = _monotonic() + timeout
            while True:
                if responder.done.is_set():
                    break
                if transport.dead_event.is_set():
                    raise RawFrameTransportError(
                        f"relay child died before responding to {method}; "
                        f"stderr: {transport.stderr_tail()!r}"
                    )
                remaining = deadline - _monotonic()
                if remaining <= 0:
                    raise RawFrameTransportError(f"no response to {method} within {timeout}s")
                # Wake on either completion or death.
                if responder.done.wait(min(remaining, 0.05)):
                    break
            frame = responder.frame
            assert frame is not None
            err = frame.get("error")
            if isinstance(err, dict):
                raise RawFrameTransportError(
                    f"relay child rpc error for {method}: {err.get('code')} {err.get('message')}"
                )
            result = frame.get("result")
            return result if isinstance(result, dict) else {}
        finally:
            with self._responders_lock:
                observers = self._responders.get(generation)
                if observers is not None:
                    try:
                        observers.remove(responder)
                    except ValueError:
                        pass
                    if not observers:
                        self._responders.pop(generation, None)

    # --- shutdown ----------------------------------------------------------

    def _shutdown_transport_bounded(self, transport: RawFrameChildTransport) -> None:
        transport.shutdown(deadline=_monotonic() + CHILD_CLEANUP_BUDGET_SECONDS)

    def shutdown(self, *, deadline: float) -> None:
        """Keyword-only; MUST be scheduled via functools.partial through run_in_executor
        (R3-A). Marks closing + snapshots under the short lock, then tears down off the
        lock on the reserved shutdown path (R3-G)."""
        with self._lock:
            self._closing = True
            active = list(self._records.values())
            in_progress = list(self._init_slots.values())
            self._records.clear()

        def remaining() -> float:
            return max(0.0, deadline - _monotonic())

        first_failure: BaseException | None = None
        for record in active:
            try:
                record.transport.shutdown(deadline=deadline)
            except BaseException as exc:  # noqa: BLE001
                if first_failure is None:
                    first_failure = exc
        for slot in in_progress:
            transport = slot.transport
            if transport is not None:
                try:
                    transport.shutdown(deadline=deadline)
                except BaseException as exc:  # noqa: BLE001
                    if first_failure is None:
                        first_failure = exc
                if slot.generation is not None:
                    self._relay.unregister_child(slot.generation)
            slot.done.wait(remaining())

        self._init_executor.shutdown(wait=False, cancel_futures=True)
        # LAST action: reclaim the reserved worker (safe from its own worker).
        self._shutdown_executor.shutdown(wait=False, cancel_futures=True)
        if first_failure is not None:
            raise first_failure
