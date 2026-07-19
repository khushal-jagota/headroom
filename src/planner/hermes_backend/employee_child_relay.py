"""EmployeeChildRelay: the relay core.

Owns the per-child id table; namespaces downstream request ids onto each child's own
integer id space; routes each child's frames only to downstreams subscribed to that
child's employee; correlates responses back to origin; fans out EVERY uncorrelated
employee-scoped frame; synthesizes a child-reset event frame on child death; carries
the tee-observer seam; and rejects downstream session-lifecycle frames with a JSON-RPC
error by construction (a denylist).

Concurrency (plan §0, §4): ONE `threading.Lock` guards all relay maps and each
binding's id counter + pending table. It is a `threading.Lock` (not `asyncio.Lock`)
because the delivery callbacks are plain synchronous functions (scheduled on the loop
via `call_soon_threadsafe`) and the pool touches relay maps off-loop in the init
executor. Critical sections are short dict ops + a recipient snapshot; no `await` and
no blocking pipe I/O ever happens while holding it (enqueue to the transport is a
non-blocking `put_nowait`).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import threading
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final

from planner.hermes_backend.relay_tee import RelayFrameDirection, RelayTeeObserver
from planner.minds.employee_child_registry import ChildReader
from planner.minds.gateway import JsonDict

# A relay-owned JSON-RPC error code for a request that cannot complete because its child
# reset (the death-path completion and the R3-C dead-window completion). In the
# implementation-defined server-error range, distinct from any Hermes code.
RELAY_CHILD_RESET_CODE: Final = -32010
# A relay-owned JSON-RPC error code for a denied session-lifecycle request (R-6).
RELAY_LIFECYCLE_DENIED_CODE: Final = -32011

# The nine-member session-lifecycle denylist (plan §3): the six session.* binding
# methods PLUS three generic lifecycle escape hatches that reach session mint/rebind/
# destroy indirectly. Every OTHER native method flows verbatim — this is a denylist.
SESSION_LIFECYCLE_DENYLIST: Final[frozenset[str]] = frozenset(
    {
        "session.create",
        "session.resume",
        "session.branch",
        "session.activate",
        "session.close",
        "session.delete",
        "handoff.request",
        "cli.exec",
        "slash.exec",
    }
)

# The maintained snapshot of the installed tui_gateway registry.
# Source of truth: the @method("...") decorators in tui_gateway/server.py.
# The completeness test scans those decorators from the file and FAILS if this
# snapshot drifts (e.g. an upstream checkout adds a 21st session.* method),
# forcing a conscious re-derivation and denylist re-check.
KNOWN_TUI_GATEWAY_SESSION_METHODS: Final[frozenset[str]] = frozenset(
    {
        "session.activate",
        "session.active_list",
        "session.branch",
        "session.close",
        "session.compress",
        "session.context_breakdown",
        "session.create",
        "session.cwd.set",
        "session.delete",
        "session.history",
        "session.interrupt",
        "session.list",
        "session.most_recent",
        "session.resume",
        "session.save",
        "session.status",
        "session.steer",
        "session.title",
        "session.undo",
        "session.usage",
    }
)
KNOWN_TUI_GATEWAY_LIFECYCLE_CAPABLE: Final[frozenset[str]] = frozenset(
    {
        "session.create",
        "session.resume",
        "session.branch",
        "session.activate",
        "session.close",
        "session.delete",
        "handoff.request",
        "cli.exec",
        "slash.exec",
    }
)


@dataclass
class PendingForward:
    """One forwarded, not-yet-answered request."""

    downstream_id: int
    child_generation: int
    child_request_id: int
    downstream_request_id: Any  # the original id as it appeared on the downstream wire


@dataclass
class DownstreamConnection:
    """One connected browser client."""

    downstream_id: int
    outbound: asyncio.Queue[str]
    subscribed_employee_entity_ids: set[str] = field(default_factory=set)
    pending: dict[tuple[int, int], PendingForward] = field(default_factory=dict)


@dataclass
class ChildBinding:
    """One live child the relay routes for."""

    child_generation: int
    employee_entity_id: str
    transport: ChildReader
    next_request_id: int = 1
    pending_by_child_request_id: dict[int, PendingForward] = field(default_factory=dict)


class EmployeeChildRelay:
    def __init__(
        self,
        *,
        pool_provider: Callable[[], Any | None],
        loop: asyncio.AbstractEventLoop,
        tee_observers: Sequence[RelayTeeObserver] = (),
    ) -> None:
        self._pool_provider = pool_provider
        self._loop = loop
        self._tee_observers = tuple(tee_observers)
        self._lock = threading.Lock()
        self._downstreams: dict[int, DownstreamConnection] = {}
        self._subscribers_by_employee: dict[str, set[int]] = {}
        self._children: dict[int, ChildBinding] = {}
        self._downstream_id_counter = itertools.count(1)

    # --- pool-facing (called off-loop in the init executor) ----------------

    def register_child(
        self, generation: int, employee_entity_id: str, transport: ChildReader
    ) -> None:
        with self._lock:
            self._children[generation] = ChildBinding(
                child_generation=generation,
                employee_entity_id=employee_entity_id,
                transport=transport,
            )

    def unregister_child(self, generation: int) -> None:
        with self._lock:
            self._children.pop(generation, None)

    def next_child_request_id(self, generation: int) -> int:
        with self._lock:
            binding = self._children[generation]
            rid = binding.next_request_id
            binding.next_request_id += 1
            return rid

    def _binding_alive_locked(self, generation: int) -> bool:
        """NON-LOCKING core: assumes the caller ALREADY holds `_lock`."""
        binding = self._children.get(generation)
        return binding is not None and binding.transport.alive

    def binding_alive(self, generation: int) -> bool:
        """Public lock-acquiring wrapper. Used only by callers not already under `_lock`."""
        with self._lock:
            return self._binding_alive_locked(generation)

    # --- downstream registration -------------------------------------------

    def register_downstream(self) -> DownstreamConnection:
        conn = DownstreamConnection(
            downstream_id=next(self._downstream_id_counter),
            outbound=asyncio.Queue(),
        )
        with self._lock:
            self._downstreams[conn.downstream_id] = conn
        return conn

    def unregister_downstream(self, conn: DownstreamConnection) -> None:
        with self._lock:
            self._downstreams.pop(conn.downstream_id, None)
            for employee_id in conn.subscribed_employee_entity_ids:
                subs = self._subscribers_by_employee.get(employee_id)
                if subs is not None:
                    subs.discard(conn.downstream_id)
                    if not subs:
                        self._subscribers_by_employee.pop(employee_id, None)
            # Cancel ALL and ONLY this downstream's pending forwards (O(k)), across every
            # child it touched. No response emitted to a gone socket; entries dropped.
            for pending in list(conn.pending.values()):
                binding = self._children.get(pending.child_generation)
                if binding is not None:
                    binding.pending_by_child_request_id.pop(pending.child_request_id, None)
            conn.pending.clear()

    def subscribe(self, conn: DownstreamConnection, employee_entity_ids: Iterable[str]) -> None:
        new_set = set(employee_entity_ids)
        with self._lock:
            old = conn.subscribed_employee_entity_ids
            for employee_id in old - new_set:
                subs = self._subscribers_by_employee.get(employee_id)
                if subs is not None:
                    subs.discard(conn.downstream_id)
                    if not subs:
                        self._subscribers_by_employee.pop(employee_id, None)
            for employee_id in new_set - old:
                self._subscribers_by_employee.setdefault(employee_id, set()).add(conn.downstream_id)
            conn.subscribed_employee_entity_ids = new_set

    # --- downstream message handling ---------------------------------------

    def handle_downstream_message(
        self, conn: DownstreamConnection, raw_text: str
    ) -> Awaitable[None] | None:
        """The discriminator (plan §3). Returns a `_forward` coroutine when a request
        must be forwarded (spawn may block), else handles it synchronously and returns
        None."""
        try:
            obj = json.loads(raw_text)
        except json.JSONDecodeError:
            self._enqueue_text(
                conn, json.dumps({"relay": "error", "detail": "unparseable message"})
            )
            return None
        if not isinstance(obj, dict) or "relay" not in obj:
            # A bare native frame has no employee address — a relay-control error.
            self._enqueue_text(
                conn,
                json.dumps({"relay": "error", "detail": "missing relay envelope"}),
            )
            return None
        verb = obj.get("relay")
        if verb == "subscribe":
            raw_ids = obj.get("employee_entity_ids", [])
            ids = [str(x) for x in raw_ids] if isinstance(raw_ids, list) else []
            self.subscribe(conn, ids)
            return None
        if verb == "request":
            employee_entity_id = obj.get("employee_entity_id")
            inner = obj.get("frame")
            if not isinstance(employee_entity_id, str) or not isinstance(inner, dict):
                self._enqueue_text(
                    conn,
                    json.dumps({"relay": "error", "detail": "missing address or frame"}),
                )
                return None
            method = inner.get("method")
            if isinstance(method, str) and method in SESSION_LIFECYCLE_DENYLIST:
                inner_id = inner.get("id")
                if inner_id is not None:
                    self._enqueue_text(
                        conn,
                        json.dumps(
                            {
                                "jsonrpc": "2.0",
                                "id": inner_id,
                                "error": {
                                    "code": RELAY_LIFECYCLE_DENIED_CODE,
                                    "message": "session lifecycle is pool-owned",
                                },
                            }
                        ),
                    )
                # else: a lifecycle notification with no id — dropped silently.
                return None
            self._notify_tee(
                employee_entity_id, RelayFrameDirection.FROM_DOWNSTREAM_TO_CHILD, inner
            )
            return self._forward(conn, employee_entity_id, inner)
        # Any object with "relay" not in {"subscribe","request"}.
        self._enqueue_text(
            conn,
            json.dumps({"relay": "error", "detail": "unknown relay verb", "verb": verb}),
        )
        return None

    async def _forward(
        self, conn: DownstreamConnection, employee_entity_id: str, inner_frame: JsonDict
    ) -> None:
        pool = self._pool_provider()
        if pool is None:
            self._complete_forward_error(
                conn, inner_frame, RELAY_CHILD_RESET_CODE, "relay backend unavailable"
            )
            return
        try:
            record = await self._loop.run_in_executor(
                pool.init_executor, pool.child_for_employee, employee_entity_id
            )
        except Exception as exc:  # spawn/resolution failure — before any pending exists
            self._complete_forward_error(
                conn, inner_frame, RELAY_CHILD_RESET_CODE, f"child unavailable: {exc}"
            )
            return
        generation = record.child_generation
        # Validate + allocate + register + enqueue as ONE `_lock` critical section
        # (plan §4). Uses the NON-locking `_binding_alive_locked` because we hold `_lock`.
        with self._lock:
            if not self._binding_alive_locked(generation):
                # R3-C dead-window: child died between resolution and registration.
                inner_id = inner_frame.get("id")
                if inner_id is None:
                    return  # id-less notification: no id to answer, drop silently
                self._enqueue_text(
                    conn,
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": inner_id,
                            "error": {
                                "code": RELAY_CHILD_RESET_CODE,
                                "message": "child reset before responding",
                            },
                        }
                    ),
                )
                return
            binding = self._children[generation]
            inner_id = inner_frame.get("id")
            if inner_id is None:
                # Notification: forward verbatim, no id rewrite, no pending entry.
                binding.transport.enqueue_frame(inner_frame)
                return
            child_request_id = binding.next_request_id
            binding.next_request_id += 1
            pending = PendingForward(
                downstream_id=conn.downstream_id,
                child_generation=generation,
                child_request_id=child_request_id,
                downstream_request_id=inner_id,
            )
            binding.pending_by_child_request_id[child_request_id] = pending
            conn.pending[(generation, child_request_id)] = pending
            frame = dict(inner_frame)
            frame["id"] = child_request_id
            binding.transport.enqueue_frame(frame)

    def _complete_forward_error(
        self, conn: DownstreamConnection, inner_frame: JsonDict, code: int, message: str
    ) -> None:
        inner_id = inner_frame.get("id")
        if inner_id is None:
            return  # notification: no id to answer
        self._enqueue_text(
            conn,
            json.dumps(
                {"jsonrpc": "2.0", "id": inner_id, "error": {"code": code, "message": message}}
            ),
        )

    # --- delivery callbacks (on the loop, scheduled by the transport thread) --

    def deliver_child_frame(self, generation: int, frame: JsonDict) -> None:
        with self._lock:
            binding = self._children.get(generation)
            if binding is None:
                return  # child already retired; drop
            frame_id = frame.get("id")
            has_body = "result" in frame or "error" in frame
            # The relay allocates child-facing ids from an int space (§4), so only an
            # int id can correlate to a pending forward; any other id is uncorrelated.
            pending = (
                binding.pending_by_child_request_id.get(frame_id)
                if has_body and isinstance(frame_id, int)
                else None
            )
            if pending is not None:
                # Correlated response: restore origin id, verbatim otherwise.
                binding.pending_by_child_request_id.pop(pending.child_request_id, None)
                origin = self._downstreams.get(pending.downstream_id)
                if origin is not None:
                    origin.pending.pop((generation, pending.child_request_id), None)
                out_frame = dict(frame)
                out_frame["id"] = pending.downstream_request_id
                employee_entity_id = binding.employee_entity_id
                recipient = origin
                fanout: list[DownstreamConnection] = []
            else:
                # Uncorrelated / employee-scoped frame — fan out to subscribers.
                out_frame = frame
                employee_entity_id = binding.employee_entity_id
                recipient = None
                fanout = self._snapshot_subscribers_locked(employee_entity_id)
        # Off the lock: serialize once and enqueue.
        if pending is not None:
            if recipient is not None:
                self._enqueue_text(recipient, json.dumps(out_frame))
            self._notify_tee(
                employee_entity_id, RelayFrameDirection.FROM_CHILD_TO_DOWNSTREAM, frame
            )
        else:
            text = json.dumps(out_frame)
            for downstream in fanout:
                self._safe_put(downstream, text)
            self._notify_tee(
                employee_entity_id, RelayFrameDirection.FROM_CHILD_TO_DOWNSTREAM, frame
            )

    def deliver_child_death(self, generation: int) -> None:
        with self._lock:
            binding = self._children.get(generation)
            if binding is None:
                return  # already retired (dedup — generation keying)
            self._children.pop(generation, None)
            employee_entity_id = binding.employee_entity_id
            pendings = list(binding.pending_by_child_request_id.values())
            binding.pending_by_child_request_id.clear()
            subscribers = self._snapshot_subscribers_locked(employee_entity_id)
            # Detach each pending from its origin conn.pending.
            origins: list[tuple[DownstreamConnection, PendingForward]] = []
            for pending in pendings:
                origin = self._downstreams.get(pending.downstream_id)
                if origin is not None:
                    origin.pending.pop((generation, pending.child_request_id), None)
                    origins.append((origin, pending))
        # Off the lock. SOLE owner of failure completion for this child's pendings (R-8).
        for origin, pending in origins:
            self._enqueue_text(
                origin,
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": pending.downstream_request_id,
                        "error": {
                            "code": RELAY_CHILD_RESET_CODE,
                            "message": "child reset before responding",
                        },
                    }
                ),
            )
        reset_frame = {
            "relay": "event",
            "type": "child_reset",
            "employee_entity_id": employee_entity_id,
        }
        reset_text = json.dumps(reset_frame)
        for downstream in subscribers:
            self._safe_put(downstream, reset_text)
        # The synthesized child-reset frame is a child→downstream frame; tee it too.
        self._notify_tee(
            employee_entity_id, RelayFrameDirection.FROM_CHILD_TO_DOWNSTREAM, reset_frame
        )

    # --- helpers -----------------------------------------------------------

    def _snapshot_subscribers_locked(self, employee_entity_id: str) -> list[DownstreamConnection]:
        return [
            self._downstreams[i]
            for i in self._subscribers_by_employee.get(employee_entity_id, ())
            if i in self._downstreams
        ]

    def _enqueue_text(self, conn: DownstreamConnection, text: str) -> None:
        try:
            conn.outbound.put_nowait(text)
        except Exception:
            pass

    def _safe_put(self, conn: DownstreamConnection, text: str) -> None:
        try:
            conn.outbound.put_nowait(text)
        except Exception:
            pass

    def _notify_tee(
        self, employee_entity_id: str, direction: RelayFrameDirection, frame: JsonDict
    ) -> None:
        for observer in self._tee_observers:
            try:
                observer.observe(
                    employee_entity_id=employee_entity_id, direction=direction, frame=frame
                )
            except Exception:
                pass
