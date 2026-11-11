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
from planner.conversation.start_admission.contracts import StartRequest, StartSnapshot
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
            self._refresh()
            entry = self._requests.setdefault(request.identity, _Reservation(request))
            if entry.active:
                return True
            if not self._reachable(request.destination):
                return False
            active = [r for r in self._requests.values() if r.active]
            if len(active) >= self.limit or self._clock() < self._next_start:
                return False
            if request.work_class == "worker":
                if sum(r.request.work_class == "worker" for r in active) >= self.limit - 1:
                    return False
                if any(
                    r.request.work_class == "supervisor"
                    and not r.active
                    and self._reachable(r.request.destination)
                    for r in self._requests.values()
                ):
                    return False
            entry.active = True
            self._next_start = self._clock() + AUTOMATIC_START_INTERVAL_SECONDS
            return True

    def finish(self, identity: str) -> None:
        with self._lock:
            self._requests.pop(identity, None)

    def finish_unbound(self, identity: str) -> None:
        with self._lock:
            entry = self._requests.get(identity)
            if entry is not None and entry.turn is None:
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
            return all(self.try_start(r.request) for r in entries)

    def snapshots(self) -> tuple[StartSnapshot, ...]:
        with self._lock:
            self._refresh()
            return tuple(
                StartSnapshot(
                    r.request,
                    "deferred" if not r.active else "uncertain" if r.uncertain else "active",
                    r.turn,
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
                "INSERT OR IGNORE INTO automatic_start_bindings "
                "(identity,work_class,destination,conversation_id,turn_number) VALUES (?,?,?,?,?)",
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
                self._requests.setdefault(request.identity, _Reservation(request, turn=turn))

    def accept(self, db_path: str, turn: TurnToken) -> None:
        from planner.core.db import connect

        with self._lock:
            if self._observed_turns.get(turn.conversation_id, 0) >= turn.turn_number:
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
            self._observed_turns[turn.conversation_id] = turn.turn_number

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
