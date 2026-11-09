"""Mac presence: the lease, its refusals, the fence held in memory, and the drains.

Risk: a Mac judged present or away wrongly (by its own clock, or never by time alone), a
refused or replaced process granted execution, a refusal or contact write that churns
the change signal, the silent-turn stall rule fed the wrong start time, or a replaced
process's run closed before its successor has replayed that conversation's items (or
never resumed after a VPS restart). Gap: the WebSocket tests judged a connection, not a
lease; nothing covered presence by time, the in-memory fence, contact coalescing, the
silent-turn and replacement stall sources or the per-conversation drain. (Items not
moving and leases failing are the stall checker's, read from the presence row, and are
tested with it.) Layer: unit, the real VpsExecutor over a real SQLite database, without
HTTP (route adapters are in the integration test) and without the conversation core (a
fake records what the runs ask of it, as in test_executor_command_mailbox.py). Reuses
tests/support/executor_presence.py and the Mac Ticket of test_executor_locale_admission.py.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest
from tests.support.executor_presence import lease_request, mac_identity
from tests.unit.test_executor_locale_admission import mac_ticket

import planner.executor.logic.presence as presence_logic
import planner.executor.vps_presence as vps_presence
from planner.conversation.backends.contracts import BackendChild, TurnToken
from planner.core import change_signal
from planner.core.clock import RealClock
from planner.core.contracts import ErrorCode, PlannerError
from planner.core.db import connect, create_schema
from planner.execution_identity import (
    MAC_NEEDS_UPDATE,
    MAC_VERSION_INCOMPATIBLE,
    ChildReference,
)
from planner.executor.clock import now_ms
from planner.executor.protocol import (
    LeaseRefused,
    LeaseReply,
    WaitingConversation,
    WorkingTurn,
)
from planner.executor.update_configuration import MAC_UPDATE_SCHEMA_VERSION
from planner.executor.update_contracts import MacUpdateCheck
from planner.executor.vps_presence_store import read_presence
from planner.executor.vps_transport import VpsExecutor
from planner.runtime.worker_step_readiness import worker_step_blocker
from planner.stalls.contracts import TrackedWait, TrackedWaitKind
from planner.tickets import locale_configuration
from planner.tickets.contracts import TicketLocale
from planner.worker_types.configuration import configured_worker_type_registry

SETTLES = 5.0


@dataclass
class _Core:
    """The conversation core's side of MacRunConversations, recorded. Its retirement
    closes nothing, as when the core no longer holds the child, so the drain closes the
    run itself afterwards."""

    retired: list[tuple[ChildReference, str]] = field(default_factory=list)
    # With db_path, the run's state when the retirement was called: the core closes it.
    db_path: str = ""
    run_states_at_retirement: list[str] = field(default_factory=list)

    async def emit_answer(
        self, turn: TurnToken, emit: Callable[[], Coroutine[Any, Any, object]]
    ) -> None:
        await emit()

    async def resume_undelivered_prompt(
        self, reference: ChildReference, prompt: object, *, start_needed: bool
    ) -> None:
        raise AssertionError("no prompt is written again here")

    async def retire_replaced_remote_child(
        self,
        reference: ChildReference,
        *,
        replaced_by: str,
        undelivered_prompt: object = None,
    ) -> None:
        if self.db_path:
            self.run_states_at_retirement.append(
                _run_state(self.db_path, reference.conversation_id)
            )
        self.retired.append((reference, replaced_by))

    async def confirm_remote_child_termination(self, reference: ChildReference) -> None:
        pass

    async def stop_interrupted_remote_child(
        self, reference: ChildReference, child: BackendChild
    ) -> None:
        pass


class _Clock:
    """The VPS clock the presence service judges by, moved by the test."""

    def __init__(self, at_ms: int) -> None:
        self.at_ms = at_ms

    def __call__(self) -> int:
        return self.at_ms


def _database(tmp_path: Path) -> str:
    path = str(tmp_path / "vps.sqlite")
    connection = connect(path)
    create_schema(connection)
    connection.close()
    return path


def _executor(path: str, core: _Core | None = None) -> VpsExecutor:
    endpoint = VpsExecutor(path, RealClock())
    endpoint.runs.conversations = core or _Core()
    # Model-list refreshes on return are the catalog's own case; here they add rows.
    endpoint.presence.returned = None
    return endpoint


@asynccontextmanager
async def _endpoint(path: str, core: _Core | None = None) -> AsyncIterator[VpsExecutor]:
    endpoint = _executor(path, core)
    try:
        yield endpoint
    finally:
        await endpoint.close()


@asynccontextmanager
async def _signals() -> AsyncIterator[list[int]]:
    """Count change signals for the duration of the block."""
    seen: list[int] = []
    unsubscribe = change_signal.subscribe(lambda: seen.append(1))
    try:
        yield seen
    finally:
        unsubscribe()


def _read[T](path: str, read: Callable[[sqlite3.Connection], T]) -> T:
    connection = connect(path)
    try:
        return read(connection)
    finally:
        connection.close()


def _waits(endpoint: VpsExecutor, at_ms: int) -> dict[TrackedWaitKind, list[TrackedWait]]:
    def collect(connection: sqlite3.Connection) -> dict[TrackedWaitKind, list[TrackedWait]]:
        found: dict[TrackedWaitKind, list[TrackedWait]] = {}
        for source in endpoint.stall_sources():
            for wait in source.tracked_waits(connection, at_ms):
                found.setdefault(wait.kind, []).append(wait)
        return found

    return _read(endpoint.db_path, collect)


def _run_state(path: str, conversation_id: str) -> str:
    row = _read(
        path,
        lambda connection: connection.execute(
            "SELECT stop_state FROM mac_runs WHERE conversation_id=?", (conversation_id,)
        ).fetchone(),
    )
    return str(row[0])


async def _eventually(condition: Callable[[], bool]) -> None:
    for _ in range(int(SETTLES / 0.01)):
        if condition():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the condition did not become true")


def test_an_accepted_lease_makes_the_mac_present_and_answers_its_update_and_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    check = MacUpdateCheck(MAC_UPDATE_SCHEMA_VERSION, None)

    async def exercise() -> None:
        async with _endpoint(_database(tmp_path)) as endpoint:
            endpoint.presence.update_check = lambda: check
            identity = mac_identity()
            reply = await endpoint.lease(lease_request(identity, queue_depth=3))
            assert isinstance(reply, LeaseReply)
            assert reply.update is check and reply.mailbox_high_sequence == 0
            assert reply.away_after_seconds == 60

            # Health comes from memory: no database read on the loop.
            def no_database(path: str) -> sqlite3.Connection:
                raise AssertionError("health read the database")

            with monkeypatch.context() as patched:
                patched.setattr(vps_presence, "connect", no_database)
                health = endpoint.health()
            assert health.presence == "present" and health.identity == identity
            assert health.since_ms == reply.accepted_at_vps_ms
            assert (health.queue_depth, health.refusal, health.admission_stopped) == (
                3,
                None,
                False,
            )

            child = ChildReference(identity.executor_incarnation, "child", "c")
            endpoint.runs.store.register_child(child, "ticket", "ticket-1", 1)
            proxy = endpoint.runs.recover(child)
            assert proxy is not None
            await proxy.cancel_running_turn()
            again = await endpoint.lease(lease_request(identity))
            assert isinstance(again, LeaseReply) and again.mailbox_high_sequence == 1

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_version_refusal_never_grants_execution_and_is_written_only_when_it_changes(
    tmp_path: Path,
) -> None:
    check = MacUpdateCheck(MAC_UPDATE_SCHEMA_VERSION, None)
    path = _database(tmp_path)

    def stored_refusal_at() -> int:
        record = _read(path, read_presence)
        assert record is not None and record.refusal is not None
        return record.refusal.refused_at_ms

    async def exercise() -> None:
        async with _endpoint(path) as endpoint:
            endpoint.presence.update_check = lambda: check
            old = replace(mac_identity(), protocol_version=21)
            refused = await endpoint.lease(lease_request(old))
            assert isinstance(refused, LeaseRefused)
            assert refused.error["code"] == ErrorCode.executor_version_refused.value
            assert refused.refusal is not None and refused.update is check
            assert (refused.refusal.reason, refused.refusal.peer_identity) == (
                MAC_NEEDS_UPDATE,
                old,
            )
            assert endpoint.health().presence == "refused"

            def admission(connection: sqlite3.Connection) -> tuple[bool, str]:
                return (
                    locale_configuration.can_execute_locale(connection, TicketLocale.macos),
                    locale_configuration.execution_unavailable_reason(
                        connection, TicketLocale.macos
                    ),
                )

            admitted, reason = _read(path, admission)
            assert not admitted
            assert reason.startswith(f"{MAC_NEEDS_UPDATE}: Mac {old.application_revision}")
            assert "protocol 21" in reason

            first_at = stored_refusal_at()
            async with _signals() as signals:
                await asyncio.sleep(0.01)
                assert isinstance(await endpoint.lease(lease_request(old)), LeaseRefused)
                assert stored_refusal_at() == first_at and signals == []
                # Another reason is new evidence: written, and announced.
                newer = replace(mac_identity(), protocol_version=23)
                assert isinstance(await endpoint.lease(lease_request(newer)), LeaseRefused)
                record = _read(path, read_presence)
                assert record is not None and record.refusal is not None
                assert record.refusal.reason == MAC_VERSION_INCOMPATIBLE
                assert signals != []

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_an_old_websocket_refusal_is_kept_across_a_vps_restart(tmp_path: Path) -> None:
    path = _database(tmp_path)

    async def exercise() -> None:
        async with _endpoint(path) as endpoint:
            await endpoint.refuse_old_websocket()
            refusal = endpoint.health().refusal
            assert refusal is not None
            assert (refusal.reason, refusal.peer_identity) == (MAC_NEEDS_UPDATE, None)
        async with _endpoint(path) as restarted:
            health = restarted.health()
            assert health.presence == "refused" and health.refusal == refusal
            reason = _read(
                path,
                lambda connection: locale_configuration.execution_unavailable_reason(
                    connection, TicketLocale.macos
                ),
            )
            assert reason.startswith(MAC_NEEDS_UPDATE) and "old WebSocket" in reason

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_replaced_process_is_refused_at_the_lease_and_at_the_work_route_fence(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with _endpoint(_database(tmp_path)) as endpoint:
            replaced, current = mac_identity(), mac_identity()
            await endpoint.lease(lease_request(replaced))
            await endpoint.lease(lease_request(current))
            refused = await endpoint.lease(lease_request(replaced))
            assert isinstance(refused, LeaseRefused) and refused.refusal is None
            assert refused.error["code"] == ErrorCode.executor_incarnation_not_current.value
            assert endpoint.health().identity == current

            assert (
                endpoint.require_current(
                    incarnation_header=current.executor_incarnation, protocol_header="22"
                )
                == current
            )
            for incarnation, protocol, code in (
                (replaced.executor_incarnation, "22", ErrorCode.executor_incarnation_not_current),
                (None, "22", ErrorCode.executor_incarnation_not_current),
                (current.executor_incarnation, "21", ErrorCode.executor_version_refused),
                (current.executor_incarnation, None, ErrorCode.executor_version_refused),
            ):
                with pytest.raises(PlannerError) as raised:
                    endpoint.require_current(
                        incarnation_header=incarnation, protocol_header=protocol
                    )
                assert raised.value.code is code

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_contact_without_a_lease_is_kept_in_memory_and_flushed_quietly_at_most_each_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(vps_presence, "CONTACT_FLUSH_INTERVAL_SECONDS", 0.3)
    path = _database(tmp_path)

    def stored_contact() -> int | None:
        record = _read(path, read_presence)
        return None if record is None else record.last_contact_at_ms

    async def exercise() -> None:
        async with _endpoint(path) as endpoint, _signals() as signals:
            endpoint.record_contact()
            first = endpoint.health().last_contact_at_ms
            assert first is not None and stored_contact() is None
            await asyncio.sleep(0.05)
            endpoint.record_contact()  # Coalesced into the flush already scheduled.
            second = endpoint.health().last_contact_at_ms
            assert second is not None and second > first
            await asyncio.sleep(0.1)
            assert stored_contact() is None
            await _eventually(lambda: stored_contact() == second)

            await asyncio.sleep(0.01)
            endpoint.record_contact()
            third = endpoint.health().last_contact_at_ms
            assert third is not None and third > second
            await asyncio.sleep(0.1)
            assert stored_contact() == second
            await _eventually(lambda: stored_contact() == third)
            assert endpoint.health().presence == "never"
            assert signals == []

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_the_mac_is_away_by_time_alone_announced_then_and_mac_work_waits_for_it(
    tmp_db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(presence_logic, "MAC_AWAY_AFTER_SECONDS", 1.0)
    ticket = mac_ticket(tmp_db)
    tmp_db.commit()
    definition = configured_worker_type_registry().require("coding")
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])

    async def exercise() -> None:
        async with _endpoint(path) as endpoint:
            identity = mac_identity()
            seen: list[str] = []
            announced = threading.Event()

            def observe() -> None:
                # What a reader woken by the signal sees at that moment.
                seen.append(endpoint.presence.judgement().presence)
                announced.set()

            reply = await endpoint.lease(lease_request(identity))
            assert isinstance(reply, LeaseReply)
            assert (
                worker_step_blocker(tmp_db, ticket, worker_type_definition=definition)
                != locale_configuration.WAITING_FOR_THE_MAC
            )
            unsubscribe = change_signal.subscribe(observe)
            try:
                assert await asyncio.to_thread(announced.wait, SETTLES)
            finally:
                unsubscribe()
            assert seen[0] == "not_heard_from"
            health = endpoint.health()
            assert (health.presence, health.since_ms) == (
                "not_heard_from",
                reply.accepted_at_vps_ms,
            )
            # Away is not a refusal: Mac work stays admitted and simply waits.
            assert locale_configuration.can_execute_locale(tmp_db, TicketLocale.macos)
            assert not locale_configuration.mac_is_present(tmp_db, now_ms())
            assert (
                worker_step_blocker(tmp_db, ticket, worker_type_definition=definition)
                == locale_configuration.WAITING_FOR_THE_MAC
            )

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_silent_working_turn_is_exempted_by_a_running_tool_and_restarted_by_activity(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with _endpoint(_database(tmp_path)) as endpoint:
            clock = _Clock(1_000_000)
            endpoint.presence._clock = clock
            identity = mac_identity()
            child = ChildReference(identity.executor_incarnation, "child", "c")

            async def lease(tool_running: bool) -> None:
                working = (WorkingTurn(child, TurnToken("c", 1), tool_running),)
                await endpoint.lease(lease_request(identity, working=working))

            def silent_since() -> list[int]:
                found = _waits(endpoint, clock.at_ms).get(TrackedWaitKind.mac_turn_silent, [])
                return [wait.started_at_ms for wait in found]

            await lease(tool_running=False)
            assert silent_since() == [1_000_000]
            clock.at_ms = 1_010_000
            await lease(tool_running=False)
            assert silent_since() == [1_000_000]  # Still the same silent stretch.
            clock.at_ms = 1_020_000
            await lease(tool_running=True)
            assert silent_since() == []  # A running tool exempts the turn...
            clock.at_ms = 1_030_000
            await lease(tool_running=False)
            assert silent_since() == [1_020_000]  # ...and counts as its activity.
            clock.at_ms = 1_035_000
            endpoint.presence.note_conversation_activity("c")  # An applied item.
            assert silent_since() == [1_035_000]

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_replaced_process_drains_per_conversation_before_its_run_closes(
    tmp_path: Path,
) -> None:
    path = _database(tmp_path)
    core = _Core(db_path=path)

    async def exercise() -> None:
        async with _endpoint(path, core) as endpoint:
            old, new = mac_identity(), mac_identity()
            await endpoint.lease(lease_request(old))
            held = ChildReference(old.executor_incarnation, "held", "c")
            free = ChildReference(old.executor_incarnation, "free", "d")
            for child in (held, free):
                endpoint.runs.store.register_child(child, "ticket", "ticket-1", 1)

            # The successor still holds c's items; d has none, so d alone drains.
            await endpoint.lease(lease_request(new, waiting=(WaitingConversation("c", 3),)))
            await _eventually(lambda: core.retired == [(free, new.executor_incarnation)])
            await _eventually(lambda: _run_state(path, "d") == "closed")
            assert _run_state(path, "c") == "open"

            # A truncated list may hide c: no drain finishes on it.
            await endpoint.lease(lease_request(new, truncated=True))
            await asyncio.sleep(0.1)
            assert _run_state(path, "c") == "open" and len(core.retired) == 1

            await endpoint.lease(lease_request(new))
            await _eventually(lambda: len(core.retired) == 2)
            assert core.retired[1] == (held, new.executor_incarnation)
            await _eventually(lambda: _run_state(path, "c") == "closed")
            # Each run is still open when the core is asked to retire it, so the core's
            # one transaction closes it with the interrupted turn it records.
            assert core.run_states_at_retirement == ["open", "open"]

    asyncio.run(asyncio.wait_for(exercise(), 20))


def test_a_vps_restart_keeps_a_pending_replacement_and_resumes_its_drain(
    tmp_path: Path,
) -> None:
    path = _database(tmp_path)

    async def exercise() -> None:
        old, new = mac_identity(), mac_identity()
        child = ChildReference(old.executor_incarnation, "child", "c")
        async with _endpoint(path) as endpoint:
            await endpoint.lease(lease_request(old))
            endpoint.runs.store.register_child(child, "ticket", "ticket-1", 1)
            await endpoint.lease(lease_request(new, waiting=(WaitingConversation("c", 3),)))
            pending = _waits(endpoint, now_ms()).get(TrackedWaitKind.mac_replacement_pending)
            assert pending is not None and [wait.conversation_id for wait in pending] == ["c"]

        core = _Core()
        async with _endpoint(path, core) as restarted:
            # Read from the rows, so the stall is still seen after the restart.
            again = _waits(restarted, now_ms()).get(TrackedWaitKind.mac_replacement_pending)
            assert again == pending
            await restarted.start()
            await asyncio.sleep(0.1)
            assert core.retired == [] and _run_state(path, "c") == "open"
            await restarted.lease(lease_request(new))
            await _eventually(lambda: core.retired == [(child, new.executor_incarnation)])
            await _eventually(lambda: _run_state(path, "c") == "closed")
            assert TrackedWaitKind.mac_replacement_pending not in _waits(restarted, now_ms())

    asyncio.run(asyncio.wait_for(exercise(), 20))
