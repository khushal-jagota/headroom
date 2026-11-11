"""Bound automatic starts until positive acceptance, independently of active turns."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic

from planner.conversation.start_admission.config import (
    AUTOMATIC_START_INTERVAL_SECONDS,
    MAX_AUTOMATIC_STARTS,
)
from planner.conversation.start_admission.contracts import (
    ReservationPhase,
    StartRequest,
    StartSnapshot,
)
from planner.execution_identity import ChildReference, TurnToken

manual_start_exempt: ContextVar[bool] = ContextVar("manual_start_exempt", default=False)


@contextmanager
def explicit_start() -> Iterator[None]:
    token = manual_start_exempt.set(True)
    try:
        yield
    finally:
        manual_start_exempt.reset(token)


@dataclass
class _Reservation:
    request: StartRequest
    active: bool = False
    uncertain: bool = False
    turn: TurnToken | None = None
    source: object | None = None
    phase: ReservationPhase = "eligible"
    permit: str | None = None
    managed: bool = False


class AutomaticStartAdmission:
    def __init__(
        self, *, limit: int = MAX_AUTOMATIC_STARTS, clock: Callable[[], float] = monotonic
    ) -> None:
        if limit < 2:
            raise ValueError("Automatic starts need a Worker slot and a supervisor slot")
        self.limit = limit
        self._clock = clock
        self._lock = threading.RLock()
        self._next_start = float("-inf")
        self._last_permit: str | None = None
        self._requests: dict[str, _Reservation] = {}
        self._observed_turns: dict[str, int] = {}
        self._reachable: Callable[[str], bool] = lambda destination: destination == "home"

    def configure_destinations(self, reachable: Callable[[str], bool]) -> None:
        with self._lock:
            self._reachable = reachable

    def _refresh(self) -> None:
        for reservation in self._requests.values():
            if reservation.active and not self._reachable(reservation.request.destination):
                reservation.active = False

    def try_start(self, request: StartRequest) -> bool:
        with self._lock:
            entry = self._requests.setdefault(request.identity, _Reservation(request))
            if entry.phase == "queued":
                return False
            return self._admit_group((request.identity,))

    def _admit_group(self, identities: Iterable[str]) -> bool:
        self._refresh()
        entries = [self._requests[i] for i in identities if i in self._requests]
        if not entries:
            return True
        if not all(self._reachable(r.request.destination) for r in entries):
            return False
        permits = {r.permit for r in entries if r.active}
        if permits:
            permit = next(iter(permits))
        else:
            active = {r.permit: r for r in self._requests.values() if r.active}
            if len(active) >= self.limit or self._clock() < self._next_start:
                return False
            supervisor = any(r.request.work_class == "supervisor" for r in entries)
            if not supervisor:
                if sum(r.request.work_class == "worker" for r in active.values()) >= self.limit - 1:
                    return False
                if any(
                    r.request.work_class == "supervisor"
                    and not r.active
                    and r.phase == "eligible"
                    and self._reachable(r.request.destination)
                    for r in self._requests.values()
                ):
                    return False
            permit = entries[0].request.identity
            self._last_permit = permit
            self._next_start = self._clock() + AUTOMATIC_START_INTERVAL_SECONDS
        for entry in entries:
            entry.active = True
            entry.permit = permit
            if entry.phase != "attempted":
                entry.phase = "preparing"
        return True

    def handoff(self, identity: str | None) -> None:
        with self._lock:
            if identity is not None and (entry := self._requests.get(identity)) is not None:
                entry.managed = True

    def queue(self, identity: str | None) -> None:
        with self._lock:
            if identity is None or (entry := self._requests.get(identity)) is None:
                return
            permit = entry.permit
            refundable = entry.phase == "preparing"
            entry.phase = "queued"
            entry.active = False
            entry.permit = None
            entry.turn = None
            entry.source = None
            entry.uncertain = False
            entry.managed = True
            self._refund_unstarted_permit(permit, refundable)

    def _refund_unstarted_permit(self, permit: str | None, refundable: bool) -> None:
        # Only the latest grant can be returned, and only after its whole group waits.
        if (
            refundable
            and permit is not None
            and self._last_permit == permit
            and not any(r.active and r.permit == permit for r in self._requests.values())
        ):
            self._next_start = min(self._next_start, self._clock())
            self._last_permit = None

    def reuse_outcome(self, identity: str | None, *, queued: bool) -> None:
        with self._lock:
            entry = self._requests.get(identity) if identity is not None else None
            if entry is None:
                return
            if queued:
                if entry.phase != "attempted" and not entry.uncertain:
                    self.queue(identity)
            elif entry.turn is None and not entry.managed:
                self._requests.pop(entry.request.identity)
                self._refund_unstarted_permit(entry.permit, entry.phase == "preparing")

    def wait_if_unadmitted(self, identity: str | None) -> None:
        with self._lock:
            entry = self._requests.get(identity) if identity is not None else None
            if entry is not None and not entry.active and entry.phase in ("eligible", "queued"):
                self.queue(identity)

    def begin_queued(self, identities: Iterable[str | None]) -> bool:
        with self._lock:
            keys = tuple(i for i in identities if i is not None and i in self._requests)
            for identity in keys:
                entry = self._requests[identity]
                if entry.phase == "queued":
                    entry.phase = "eligible"
            return self._admit_group(keys)

    def priority(self, identities: Iterable[str | None]) -> int:
        with self._lock:
            entries = [
                self._requests[i] for i in identities if i is not None and i in self._requests
            ]
            return (
                0
                if not entries or any(r.request.work_class == "supervisor" for r in entries)
                else 1
            )

    def transfer(self, preparation: str, request: StartRequest) -> bool:
        """Attach the actual committed claim identity without a second pacing charge."""
        with self._lock:
            entry = self._requests.pop(preparation)
            if request.identity in self._requests:
                self._requests[preparation] = entry
                raise RuntimeError("Actual claim identity already owns a reservation")
            entry.request = request
            self._requests[request.identity] = entry
            self._refresh()
            return entry.active

    def has_requests(self, identities: Iterable[str | None]) -> bool:
        with self._lock:
            return any(
                identity in self._requests for identity in identities if identity is not None
            )

    def attempted(self, identities: Iterable[str | None]) -> bool:
        with self._lock:
            return any(
                self._requests[i].phase == "attempted"
                for i in identities
                if i is not None and i in self._requests
            )

    def finish(self, identity: str) -> None:
        with self._lock:
            self._requests.pop(identity, None)

    def finish_unbound(self, identity: str) -> None:
        with self._lock:
            entry = self._requests.get(identity)
            if entry is not None and entry.turn is None and not entry.managed:
                self._requests.pop(identity, None)

    def uncertain(self, identity: str) -> None:
        with self._lock:
            if (entry := self._requests.get(identity)) is not None:
                entry.uncertain = True

    def bind(self, identities: Iterable[str | None], turn: TurnToken) -> None:
        with self._lock:
            for identity in identities:
                if identity is not None and (entry := self._requests.get(identity)) is not None:
                    entry.turn = turn

    def observe(self, turn: TurnToken) -> None:
        """Only the core's current-sink, exact-turn activity proves acceptance."""
        with self._lock:
            for identity, entry in tuple(self._requests.items()):
                if entry.turn == turn:
                    del self._requests[identity]

    def allow_dispatch(self, turn: TurnToken) -> bool:
        """A deferred remote prompt re-enters pacing before mailbox delivery."""
        with self._lock:
            entries = [r for r in self._requests.values() if r.turn == turn]
            return self._admit_group(r.request.identity for r in entries)

    def snapshots(self) -> tuple[StartSnapshot, ...]:
        with self._lock:
            self._refresh()
            return tuple(
                StartSnapshot(
                    r.request,
                    (
                        "queued"
                        if r.phase == "queued"
                        else (
                            "deferred" if not r.active else "uncertain" if r.uncertain else "active"
                        )
                    ),
                    r.turn,
                    r.permit if r.active else None,
                )
                for r in self._requests.values()
            )

    def poll_delay(self, default: float) -> float:
        with self._lock:
            self._refresh()
            return min(default, AUTOMATIC_START_INTERVAL_SECONDS) if self._requests else default

    def retain_deferred(self, prefix: str, identities: set[str]) -> None:
        """Forget vanished source work, but never discard bound or uncertain delivery."""
        with self._lock:
            for identity, entry in tuple(self._requests.items()):
                if (
                    identity.startswith(prefix)
                    and identity not in identities
                    and not entry.active
                    and entry.turn is None
                    and not entry.uncertain
                    and not entry.managed
                ):
                    del self._requests[identity]

    def persist_bindings(self, db_path: str, turn: TurnToken) -> None:
        from planner.core.db import connect

        with self._lock:
            requests = [r.request for r in self._requests.values() if r.turn == turn]
        if not requests:
            return
        conn = connect(db_path)
        try:
            conn.executemany(
                "INSERT INTO automatic_start_bindings "
                "(identity,work_class,destination,conversation_id,turn_number) VALUES (?,?,?,?,?) "
                "ON CONFLICT(identity) DO UPDATE SET conversation_id=excluded.conversation_id,"
                "turn_number=excluded.turn_number,executor_incarnation=NULL,child_id=NULL",
                [
                    (
                        r.identity,
                        r.work_class,
                        r.destination,
                        turn.conversation_id,
                        turn.turn_number,
                    )
                    for r in requests
                ],
            )
        finally:
            conn.close()

    def restore_bindings(self, conn: sqlite3.Connection, turn: TurnToken) -> None:
        with self._lock:
            for row in conn.execute(
                "SELECT identity,work_class,destination FROM automatic_start_bindings "
                "WHERE conversation_id=? AND turn_number=?",
                (turn.conversation_id, turn.turn_number),
            ):
                request = StartRequest(row[0], row[1], row[2])
                self._requests.setdefault(
                    request.identity,
                    _Reservation(request, turn=turn, phase="attempted", managed=True),
                )

    def accept(self, db_path: str, turn: TurnToken) -> None:
        from planner.core.db import connect

        with self._lock:
            if self._observed_turns.get(turn.conversation_id, 0) == turn.turn_number:
                return
        conn = connect(db_path)
        try:
            if (
                conn.execute(
                    "SELECT 1 FROM automatic_start_bindings "
                    "WHERE conversation_id=? AND turn_number=?",
                    (turn.conversation_id, turn.turn_number),
                ).fetchone()
                is not None
            ):
                conn.execute(
                    "DELETE FROM automatic_start_bindings "
                    "WHERE conversation_id=? AND turn_number=?",
                    (turn.conversation_id, turn.turn_number),
                )
        finally:
            conn.close()
        self.observe(turn)
        with self._lock:
            self._observed_turns[turn.conversation_id] = max(
                self._observed_turns.get(turn.conversation_id, 0), turn.turn_number
            )

    def bind_child(self, db_path: str, turn: TurnToken, child: ChildReference) -> None:
        from planner.core.db import connect

        conn = connect(db_path)
        try:
            conn.execute(
                "UPDATE automatic_start_bindings SET executor_incarnation=?,child_id=? "
                "WHERE conversation_id=? AND turn_number=?",
                (
                    child.executor_incarnation,
                    child.child_id,
                    turn.conversation_id,
                    turn.turn_number,
                ),
            )
        finally:
            conn.close()

    def reject(self, db_path: str, turn: TurnToken) -> None:
        from planner.core.db import connect

        conn = connect(db_path)
        try:
            conn.execute(
                "DELETE FROM automatic_start_bindings WHERE conversation_id=? AND turn_number=?",
                (turn.conversation_id, turn.turn_number),
            )
        finally:
            conn.close()
        self.observe(turn)

    def bind_source(self, turn: TurnToken, source: object | None) -> None:
        with self._lock:
            for reservation in self._requests.values():
                if reservation.turn == turn:
                    reservation.source = source
                    reservation.phase = "preparing" if source is None else "attempted"

    def retire_source(self, db_path: str, source: object) -> None:
        with self._lock:
            turns = {
                r.turn for r in self._requests.values() if r.source is source and r.turn is not None
            }
        for turn in turns:
            self.reject(db_path, turn)

    def retire_remote(self, db_path: str, reference: ChildReference) -> None:
        from planner.core.db import connect

        conn = connect(db_path)
        try:
            turns = [
                TurnToken(reference.conversation_id, int(row[0]))
                for row in conn.execute(
                    "SELECT DISTINCT turn_number FROM automatic_start_bindings "
                    "WHERE conversation_id=? "
                    "AND executor_incarnation=? AND child_id=?",
                    (reference.conversation_id, reference.executor_incarnation, reference.child_id),
                )
            ]
        finally:
            conn.close()
        for turn in turns:
            self.reject(db_path, turn)

    def settle_ids(self, db_path: str, identities: Iterable[str | None]) -> None:
        """Settle exact sender outcomes, without changing any Worker ownership claim."""
        keys = tuple(identity for identity in identities if identity is not None)
        if not keys:
            return
        from planner.core.db import connect

        conn = connect(db_path)
        try:
            conn.executemany(
                "DELETE FROM automatic_start_bindings WHERE identity=?", [(i,) for i in keys]
            )
        finally:
            conn.close()
        with self._lock:
            for identity in keys:
                self._requests.pop(identity, None)

    def failed_attempt(self, db_path: str, turn: TurnToken) -> None:
        """A bound preparation is not evidence that a prompt write was attempted."""
        with self._lock:
            entries = [r for r in self._requests.values() if r.turn == turn]
            if any(r.phase == "attempted" for r in entries):
                for entry in entries:
                    entry.uncertain = True
                self._refresh()
                return
            identities = tuple(r.request.identity for r in entries)
        self.settle_ids(db_path, identities)
        with self._lock:
            for entry in entries:
                entry.active = False
                entry.permit = None
                entry.turn = None
                entry.source = None
                entry.phase = "failed"
                self._requests[entry.request.identity] = entry

    def failed_sender(self, db_path: str, identity: str | None) -> None:
        with self._lock:
            entry = self._requests.get(identity) if identity is not None else None
            if entry is None or entry.phase == "queued":
                return
            if entry.phase == "attempted":
                entry.uncertain = True
                self._refresh()
                return
        self.settle_ids(db_path, (identity,))
