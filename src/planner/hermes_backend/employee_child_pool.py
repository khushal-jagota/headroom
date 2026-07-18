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
        on_stored_session_bound: Callable[[str, str], None] | None = None,
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

        self._lock = threading.Lock()
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

        def on_dead() -> None:
            self._loop.call_soon_threadsafe(self._relay.deliver_child_death, generation)

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
                self._notify_stored_session_bound(employee_entity_id, stored_session_id)
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
            # Persist the fresh durable binding BEFORE advancing any in-memory state
            # (S2B-OWN-001, rebind half). Fail-closed: if persistence raises, the in-memory
            # maps still point at the OLD binding and the DB still holds the OLD key — the two
            # stay CONSISTENT (a restart resumes the old key). A concurrent stale `/new` then
            # still sees the OLD current_live (not advanced), so it is NOT a no-op and retries
            # its own close+create+persist rather than returning an unpersisted fresh binding.
            # The freshly-created live session is orphaned on the child but never referenced.
            self._notify_stored_session_bound(employee_entity_id, new_stored)
            with self._lock:
                self._stored_session_id_by_employee[employee_entity_id] = new_stored
                self._live_session_id_by_employee[employee_entity_id] = new_live
                record.stored_session_id = new_stored
            return new_live, new_stored

    def _rebind_lock_for(self, employee_entity_id: str) -> threading.Lock:
        with self._rebind_locks_guard:
            lock = self._rebind_locks.get(employee_entity_id)
            if lock is None:
                lock = threading.Lock()
                self._rebind_locks[employee_entity_id] = lock
            return lock

    def _notify_stored_session_bound(self, employee_entity_id: str, stored_session_id: str) -> None:
        callback = self._on_stored_session_bound
        if callback is not None:
            callback(employee_entity_id, stored_session_id)

    def _transport_request(
        self,
        transport: RawFrameChildTransport,
        generation: int,
        method: str,
        params: JsonDict,
        timeout: float,
    ) -> JsonDict:
        rid = self._relay.next_child_request_id(generation)
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
