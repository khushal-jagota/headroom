"""EmployeeChildRegistry: the provider-neutral, reader-agnostic per-employee child owner.

Owns one child per employee entity: spawns on demand through an injected `reader_factory`,
creates/owns exactly one session per child, respawns dead children on next demand, is the SOLE
issuer of session.create/resume/close/interrupt (via `reader.request()`), and shuts all down
within one shared deadline.

It depends only on Protocols/Callables declared here, so `minds/` stays free of any
`hermes_backend/` import: the concrete raw reader is newed up by the injected `reader_factory`,
and all relay wiring lives behind the injected `ChildFrameSubscriber`. Per-frame order is owned
by the reader (sink1 subscriber-deliver → sink2 the reader's own request/reply settle → sink3
subscriber-fold); the registry only wires the subscriber's phases into each spawned reader.
"""

from __future__ import annotations

import concurrent.futures
import threading
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic as _monotonic
from typing import Final, Protocol

from planner.minds.gateway import (
    READY_TIMEOUT_DEFAULT,
    REQUEST_TIMEOUT_DEFAULT,
    SHUTDOWN_GRACE_DEFAULT,
    JsonDict,
)

INIT_EXECUTOR_MAX_WORKERS: Final = 8
CHILD_CLEANUP_BUDGET_SECONDS: Final = SHUTDOWN_GRACE_DEFAULT


class ChildReaderError(Exception):
    """Neutral base for a reader-level failure (spawn, ready timeout, child death, RPC error).

    The concrete `RawFrameTransportError` subclasses this in `hermes_backend/`, so registry code
    can `except ChildReaderError` while hermes_backend callers keep `except RawFrameTransportError`.
    """


class ChildRegistryClosing(ChildReaderError):
    """The registry is closing (or has no live child for a requested employee)."""


class ChildReader(Protocol):
    """The uniform reader interface the registry drives on a spawned child.

    Honest about the whole surface: the ordered two-phase frame subscription
    (`register_frame_sinks`), request/reply (`request`, carrying the pre-send id hook), send
    (`enqueue_frame`), lifecycle (`start_reading`/`wait_ready`/`shutdown`), and the alive/death
    signal (`alive`/`dead_event`). Both the raw transport (now) and `GatewayChild` (P2) satisfy it.
    """

    def register_frame_sinks(
        self,
        *,
        on_deliver: Callable[[JsonDict], None],
        on_fold: Callable[[JsonDict], None] | None,
        on_dead: Callable[[], None],
    ) -> None: ...

    def start_reading(self) -> None: ...

    def wait_ready(self, timeout: float) -> None: ...

    def request(
        self,
        method: str,
        params: JsonDict,
        *,
        timeout: float,
        on_request_id: Callable[[int], None] | None = None,
    ) -> JsonDict: ...

    def enqueue_frame(self, frame: JsonDict) -> None: ...

    def shutdown(self, *, deadline: float) -> None: ...

    @property
    def alive(self) -> bool: ...

    @property
    def dead_event(self) -> threading.Event: ...


# A construct-only factory the registry calls to spawn one reader. It news up the concrete
# reader (kept in hermes_backend/) with the three per-child frame sinks + its id allocator, but
# performs NO relay register (that is `ChildFrameSubscriber.attach`, so the R3-B partial-transport
# publish can slot between construction and register). Invoked with keyword args:
#   reader_factory(generation=int, employee_entity_id=str, env=Mapping[str, str],
#                  on_deliver=Callable, on_fold=Callable, on_dead=Callable) -> ChildReader
ReaderFactory = Callable[..., ChildReader]


class ChildFrameSubscriber(Protocol):
    """The consumer that subscribes to a registry's children (the relay consumer, in P1).

    `attach`/`detach`/`detach_now` are the relay-binding lifecycle; `deliver`/`fold`/`on_dead` are
    the per-frame dispatch phases the reader invokes in order (deliver → own settle → fold). The
    two detach variants preserve the exact on-loop vs synchronous discipline of the original pool.
    """

    def attach(
        self, *, generation: int, employee_entity_id: str, reader: ChildReader
    ) -> None: ...

    def deliver(
        self, *, generation: int, employee_entity_id: str, frame: JsonDict
    ) -> None: ...

    def fold(
        self, *, generation: int, employee_entity_id: str, frame: JsonDict
    ) -> None: ...

    def on_dead(self, *, generation: int, employee_entity_id: str) -> None: ...

    def detach(self, *, generation: int) -> None: ...

    def detach_now(self, *, generation: int) -> None: ...


@dataclass
class EmployeeChildRecord:
    """The employee→child record (only registry-consumed state)."""

    employee_entity_id: str
    transport: ChildReader
    child_generation: int
    stored_session_id: str


@dataclass
class _InitSlot:
    """The per-employee init latch (R-2), teardown-capable (R3-B)."""

    done: threading.Event
    record: EmployeeChildRecord | None = None
    error: BaseException | None = None
    transport: ChildReader | None = None
    generation: int | None = None


class EmployeeChildRegistry:
    def __init__(
        self,
        *,
        reader_factory: ReaderFactory,
        subscriber: ChildFrameSubscriber,
        identity_env_strategy: Callable[[str], dict[str, str]],
        session_source: str,
        session_cols: int,
        on_stored_session_bound: Callable[[str, str, str | None], None] | None = None,
        stored_session_resolver: Callable[[str], str | None] | None = None,
        ready_timeout: float = READY_TIMEOUT_DEFAULT,
        request_timeout: float = REQUEST_TIMEOUT_DEFAULT,
    ) -> None:
        self._reader_factory = reader_factory
        self._subscriber = subscriber
        self._identity_env_strategy = identity_env_strategy
        self._session_source = session_source
        self._session_cols = session_cols
        self._on_stored_session_bound = on_stored_session_bound
        self._stored_session_resolver = stored_session_resolver
        self._ready_timeout = ready_timeout
        self._request_timeout = request_timeout

        self._lock = threading.Lock()
        self._records: dict[str, EmployeeChildRecord] = {}
        self._init_slots: dict[str, _InitSlot] = {}
        self._generation_counter = 0
        self._stored_session_id_by_employee: dict[str, str] = {}
        # The current LIVE session id per employee (record carries only the stored id).
        self._live_session_id_by_employee: dict[str, str] = {}
        # Per-employee serialization latch for rebind_fresh_session.
        self._rebind_locks: dict[str, threading.Lock] = {}
        self._rebind_locks_guard = threading.Lock()
        self._closing = False

        self._init_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=INIT_EXECUTOR_MAX_WORKERS, thread_name_prefix="child-registry-init"
        )
        self._shutdown_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="child-registry-shutdown"
        )

    @property
    def init_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        return self._init_executor

    @property
    def shutdown_executor(self) -> concurrent.futures.ThreadPoolExecutor:
        return self._shutdown_executor

    # --- durable session adoption (S2b) ------------------------------------

    def adopt_stored_session(self, employee_entity_id: str, stored_session_id: str) -> None:
        """Seed the durable stored-session binding for an employee BEFORE its first spawn, so the
        first `_create_or_resume_session` RESUMES it instead of creating fresh. First-write-wins
        and a no-op once the employee already has a live child."""
        with self._lock:
            if employee_entity_id in self._records:
                return
            self._stored_session_id_by_employee.setdefault(
                employee_entity_id, stored_session_id
            )

    # --- spawn on demand ---------------------------------------------------

    def get_or_spawn(self, employee_entity_id: str) -> EmployeeChildRecord:
        """The single spawn-on-demand entry point. Called only from the init executor.
        No blocking call runs under `_lock` (R-2)."""
        with self._lock:
            if self._closing:
                raise ChildRegistryClosing("child registry is closing")
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
                slot.error = ChildRegistryClosing("child registry is closing")
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
                self._subscriber.detach_now(generation=published_generation)
            raise slot.error
        return record

    def _spawn_and_bind(
        self, employee_entity_id: str, slot: _InitSlot
    ) -> EmployeeChildRecord:
        with self._lock:
            self._generation_counter += 1
            generation = self._generation_counter
        env = self._identity_env_strategy(employee_entity_id)

        def on_deliver(frame: JsonDict) -> None:
            self._subscriber.deliver(
                generation=generation, employee_entity_id=employee_entity_id, frame=frame
            )

        def on_fold(frame: JsonDict) -> None:
            self._subscriber.fold(
                generation=generation, employee_entity_id=employee_entity_id, frame=frame
            )

        def on_dead() -> None:
            self._subscriber.on_dead(
                generation=generation, employee_entity_id=employee_entity_id
            )

        reader = self._reader_factory(
            generation=generation,
            employee_entity_id=employee_entity_id,
            env=env,
            on_deliver=on_deliver,
            on_fold=on_fold,
            on_dead=on_dead,
        )
        # Publish the partial reader + generation the instant it exists, and re-check
        # `_closing` in the SAME locked section (R3-B).
        with self._lock:
            if self._closing:
                closing_now = True
            else:
                closing_now = False
                slot.transport = reader
                slot.generation = generation
        if closing_now:
            self._shutdown_transport_bounded(reader)
            raise ChildRegistryClosing("child registry is closing")

        registered = False
        try:
            self._subscriber.attach(
                generation=generation, employee_entity_id=employee_entity_id, reader=reader
            )
            registered = True
            reader.start_reading()
            reader.wait_ready(self._ready_timeout)
            live_session_id, stored_session_id, created_fresh = self._create_or_resume_session(
                reader, employee_entity_id
            )
            if created_fresh:
                # A never-seen stored id was minted (not a resume of an adopted key): persist
                # the fresh binding BEFORE publishing it. Fail-closed: this call is INSIDE the
                # guarded block, so a persistence failure tears down + unregisters the just-bound
                # child (below). First create has no predecessor durable id (old = None).
                self._notify_stored_session_bound(employee_entity_id, stored_session_id, None)
            # Publish the binding to the in-memory maps only AFTER any required persist landed.
            with self._lock:
                self._stored_session_id_by_employee[employee_entity_id] = stored_session_id
                self._live_session_id_by_employee[employee_entity_id] = live_session_id
        except BaseException:
            self._shutdown_transport_bounded(reader)
            if registered:
                # Retire the binding ON THE LOOP so it lands AFTER any deliver already queued for
                # this generation (e.g. a failed session-RPC error response).
                self._subscriber.detach(generation=generation)
            raise
        return EmployeeChildRecord(
            employee_entity_id=employee_entity_id,
            transport=reader,
            child_generation=generation,
            stored_session_id=stored_session_id,
        )

    def _create_or_resume_session(
        self, reader: ChildReader, employee_entity_id: str
    ) -> tuple[str, str, bool]:
        """Return `(live_session_id, stored_session_id, created_fresh)`. `created_fresh` is True
        only when a brand-new stored id was minted (session.create), so the caller persists that
        fresh binding; a resume of an adopted/held key is not fresh."""
        with self._lock:
            held = self._stored_session_id_by_employee.get(employee_entity_id)
        if held is None and self._stored_session_resolver is not None:
            # On-demand adoption: ask the composition-owned resolver for this employee's persisted
            # durable key. A non-empty return seeds the resume branch (an adopted key, NOT created
            # fresh); a None/empty return falls through to session.create.
            resolved = self._stored_session_resolver(employee_entity_id)
            if isinstance(resolved, str) and resolved:
                held = resolved
        if held is not None:
            result = reader.request(
                "session.resume", {"session_id": held}, timeout=self._request_timeout
            )
            live = str(result.get("session_id") or held)
            return live, str(result.get("resumed") or held), False
        result = reader.request(
            "session.create",
            {"source": self._session_source, "cols": self._session_cols},
            timeout=self._request_timeout,
        )
        # The own-session-resume proof depends on a durable stored id; require a non-empty string.
        stored = result.get("stored_session_id")
        if not isinstance(stored, str) or not stored:
            raise ChildReaderError("session.create returned no non-empty stored_session_id")
        live = str(result.get("session_id") or stored)
        return live, stored, True

    # --- new-conversation rebind (S2b) -------------------------------------

    def rebind_fresh_session(
        self, employee_entity_id: str, old_live_session_id: str
    ) -> tuple[str, str]:
        """End the employee child's current session binding and bind a fresh one, on the child's
        OWN reader: close the old live session first, then session.create a fresh one. Returns
        `(new_live_session_id, new_stored_session_id)`.

        Per-employee serialized: a concurrent rebind whose `old_live_session_id` no longer matches
        the current live id observes the already-fresh binding and returns it without re-close/
        re-create (a stale no-op)."""
        rebind_lock = self._rebind_lock_for(employee_entity_id)
        with rebind_lock:
            with self._lock:
                if self._closing:
                    raise ChildRegistryClosing("child registry is closing")
                record = self._records.get(employee_entity_id)
                current_live = self._live_session_id_by_employee.get(employee_entity_id)
            if record is None or not record.transport.alive:
                raise ChildRegistryClosing(f"no live child for employee {employee_entity_id!r}")
            if current_live is not None and current_live != old_live_session_id:
                # A rebind already advanced past this caller's old live id: no-op.
                return current_live, record.stored_session_id
            generation = record.child_generation
            record.transport.request(
                "session.close",
                {"session_id": old_live_session_id},
                timeout=self._request_timeout,
            )
            result = record.transport.request(
                "session.create",
                {"source": self._session_source, "cols": self._session_cols},
                timeout=self._request_timeout,
            )
            new_stored = result.get("stored_session_id")
            if not isinstance(new_stored, str) or not new_stored:
                raise ChildReaderError("session.create returned no non-empty stored_session_id")
            new_live = str(result.get("session_id") or new_stored)
            old_stored = record.stored_session_id
            # Persist the fresh durable binding BEFORE advancing any in-memory state. Fail-closed:
            # the OLD live session is ALREADY CLOSED above, so leaving the maps on the old key is
            # NOT safe. If persistence raises, TEAR THE CHILD DOWN so it respawns clean and RESUMEs
            # the still-durable OLD stored key; the maps are cleared with the child, and the DB
            # still holds the OLD durable key. The freshly-created live session dies with the child.
            try:
                self._notify_stored_session_bound(employee_entity_id, new_stored, old_stored)
            except BaseException:
                # Pass generation N's OWN reader so the cleanup shuts down exactly N's child.
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
        generation_transport: ChildReader,
    ) -> None:
        """Tear generation `generation`'s child down after ITS rebind persistence failure so the
        next demand respawns clean and resumes the still-durable OLD stored key. Leaves the OLD
        durable DB key intact.

        GENERATION-GUARDED: `get_or_spawn` respawns a dead child using only `_lock`/`_init_slots`,
        NEVER `rebind_lock`, so a NEWER generation N+1 can publish while THIS rebind (N) blocks in
        its persist callback. N's cleanup must therefore touch NOTHING that belongs to N+1: the
        record pop and the live-id clear are conditional on the current record still being
        generation N (or absent). N's OWN reader (passed in) is always shut down — it is the failed
        rebind's child regardless — but never N+1's reader."""
        with self._lock:
            record = self._records.get(employee_entity_id)
            current_is_this_generation = (
                record is not None and record.child_generation == generation
            )
            if current_is_this_generation:
                self._records.pop(employee_entity_id, None)
                # Clear the fresh (unpersisted) live id ONLY when the current binding is still N's;
                # leave the stored-id map on the OLD durable key so a respawn RESUMEs it.
                self._live_session_id_by_employee.pop(employee_entity_id, None)
        # Shut down N's OWN reader (idempotent; a respawn may already have shut the stale one).
        self._shutdown_transport_bounded(generation_transport)
        # Retire only N's binding, ON THE LOOP. `unregister_child` is generation-keyed, so it is a
        # no-op for N+1 even if a respawn published between the check above and this call.
        self._subscriber.detach(generation=generation)

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
        """Publish a fresh durable binding. The callback carries BOTH the new stored id and the
        OLD one (None on first-create) so a ticket ownership-CAS writer can pass
        `expected → candidate` and assert the write took."""
        callback = self._on_stored_session_bound
        if callback is not None:
            callback(employee_entity_id, new_stored_session_id, old_stored_session_id)

    # --- live-session lookups + interrupt ----------------------------------

    def live_session_id_for(self, employee_entity_id: str) -> str | None:
        """The employee's CURRENT live session id, if a turn is (or was last) live."""
        with self._lock:
            return self._live_session_id_by_employee.get(employee_entity_id)

    def interrupt_live_turn(self, employee_entity_id: str, *, deadline: float) -> None:
        """Issue `session.interrupt {session_id: <live id>}` on the employee's child. Resolves the
        employee's CURRENT live session id (the record carries only the stored id); a no-op when no
        live id is known. Converts the absolute `deadline` to the remaining relative timeout."""
        with self._lock:
            record = self._records.get(employee_entity_id)
            live_session_id = self._live_session_id_by_employee.get(employee_entity_id)
        if record is None or not record.transport.alive or not live_session_id:
            return
        remaining = max(0.0, deadline - _monotonic())
        try:
            record.transport.request(
                "session.interrupt", {"session_id": live_session_id}, timeout=remaining
            )
        except ChildReaderError:
            # Best-effort interrupt at shutdown; a dead/unresponsive child needs no interrupt.
            pass

    # --- shutdown ----------------------------------------------------------

    def _shutdown_transport_bounded(self, reader: ChildReader) -> None:
        reader.shutdown(deadline=_monotonic() + CHILD_CLEANUP_BUDGET_SECONDS)

    def shutdown(self, *, deadline: float) -> None:
        """Keyword-only. Marks closing + snapshots under the short lock, then tears down off the
        lock within one shared deadline."""
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
                    self._subscriber.detach_now(generation=slot.generation)
            slot.done.wait(remaining())

        self._init_executor.shutdown(wait=False, cancel_futures=True)
        # LAST action: reclaim the reserved worker (safe from its own worker).
        self._shutdown_executor.shutdown(wait=False, cancel_futures=True)
        if first_failure is not None:
            raise first_failure
