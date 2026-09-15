"""Durable, idempotent delivery of proposal-ready facts to non-owner holders."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from sqlite3 import Connection
from time import monotonic
from typing import cast
from unittest.mock import AsyncMock

import pytest

from planner.conversation.contracts import (
    PromptDeliveryQueued,
    PromptDeliveryRefusalReason,
    PromptDeliveryRefused,
    PromptDeliveryStarted,
    PromptDeliveryUncertain,
)
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core import loops as core_loops
from planner.core.clock import Clock
from planner.core.clock import TestClock as MutableClock
from planner.core.config import Config
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
from planner.core.loops import BackgroundLoops, start_background_loops
from planner.message_delivery.contracts import MessageDeliveryResult
from planner.message_delivery.service import send_system_message
from planner.proposal_holder_wakes import data as wake_data
from planner.proposal_holder_wakes.contracts import proposal_ready_message
from planner.proposal_holder_wakes.runtime import (
    ProposalHolderWakeLoop,
)
from planner.proposal_holder_wakes.runtime import (
    _deliver_pending_wakes as deliver_pending_wakes,
)
from planner.runtime.lock import ensure_machine_lock, release_machine_lock
from planner.runtime.logic.worker_step_prompt import proposal_returned_for_revision_prompt
from planner.sprints import data as sprints_data
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS, AtCap


def _target(conn: Connection, holder: Principal, *, now: int = 1) -> str:
    ticket = tickets_data.create_ticket(
        conn,
        title="Durable wake",
        principal=holder,
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Work",
        stated_ceiling="needs_success",
        stated_at_cap=AtCap.propose,
    )
    return ticket.id


def _file(conn: Connection, ticket_id: str, *, body: str, now: int) -> None:
    tickets_data.file_current_proposal_with_recap(
        conn,
        ticket_id,
        body=body,
        recap=body,
        principal=Principal(PrincipalKind.ticket, ticket_id),
        now=now,
    )


def _wake_state(conn: Connection, ticket_id: str) -> str:
    row = conn.execute(
        "SELECT state FROM proposal_holder_wakes WHERE ticket_id=?", (ticket_id,)
    ).fetchone()
    assert row is not None
    return str(row["state"])


def _reject(conn: Connection, ticket_id: str, *, now: int = 3) -> None:
    conn.execute(
        "UPDATE tickets SET conversation_id='c_worker' WHERE id=?", (ticket_id,)
    )
    tickets_data.return_for_revision(
        conn,
        ticket_id,
        message="Add evidence.",
        lifecycle_message=proposal_returned_for_revision_prompt(),
        principal=OWNER_PRINCIPAL,
        now=now,
    )


def _start_other_process_lock(lock_path: Path) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,sys; "
                "handle=open(sys.argv[1], 'w'); "
                "fcntl.flock(handle, fcntl.LOCK_EX); "
                "print('ready', flush=True); "
                "sys.stdin.read()"
            ),
            str(lock_path),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    return process


def _other_process_can_lock(lock_path: Path) -> bool:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import fcntl,sys; handle=open(sys.argv[1], 'w'); "
                "\ntry: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)"
                "\nexcept BlockingIOError: raise SystemExit(1)"
            ),
            str(lock_path),
        ],
        check=False,
    )
    return completed.returncode == 0


def test_crash_before_send_recovers_once_and_duplicate_recovery_is_empty(
    tmp_db: Connection, fake_clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="First", now=2)
    send = AsyncMock(
        return_value=MessageDeliveryResult(CHIEF_PRINCIPAL, "c_chief", PromptDeliveryStarted())
    )
    monkeypatch.setattr(
        "planner.proposal_holder_wakes.runtime.message_delivery_service.send_system_message",
        send,
    )

    assert asyncio.run(deliver_pending_wakes(object(), tmp_db, fake_clock)) == 1  # type: ignore[arg-type]
    assert asyncio.run(deliver_pending_wakes(object(), tmp_db, fake_clock)) == 0  # type: ignore[arg-type]
    send.assert_awaited_once()


def test_reconcile_restores_a_missing_intent_for_an_existing_pending_proposal(
    tmp_db: Connection, fake_clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Existing", now=2)
    tmp_db.execute("DELETE FROM proposal_holder_wakes WHERE ticket_id=?", (ticket_id,))
    send = AsyncMock(
        return_value=MessageDeliveryResult(CHIEF_PRINCIPAL, "c_chief", PromptDeliveryStarted())
    )
    monkeypatch.setattr(
        "planner.proposal_holder_wakes.runtime.message_delivery_service.send_system_message",
        send,
    )

    assert asyncio.run(deliver_pending_wakes(object(), tmp_db, fake_clock)) == 1  # type: ignore[arg-type]
    call = send.await_args
    assert call is not None
    assert call.args[3] == CHIEF_PRINCIPAL
    assert call.kwargs["sender_message_id"] == f"proposal-holder-wake:{ticket_id}:1:1"


@pytest.mark.parametrize("holder_kind", ["chief", "sprint_item", "ticket"])
def test_panels_authored_wake_uses_the_exact_holder_conversation_path(
    tmp_db: Connection, fake_clock: Clock, holder_kind: str
) -> None:
    if holder_kind == "chief":
        holder = CHIEF_PRINCIPAL
    elif holder_kind == "sprint_item":
        item = sprints_data.create_item(
            tmp_db, title="Holder Item", project_id="project_vylo", clock=fake_clock
        )
        holder = Principal(PrincipalKind.sprint_item, item.id)
    else:
        holder = Principal(
            PrincipalKind.ticket,
            tickets_data.create_ticket(
                tmp_db,
                title="Holder Ticket",
                principal=OWNER_PRINCIPAL,
                now=1,
                title_max_chars=TITLE_MAX_CHARS,
                worker_type="coding",
                kickoff_note="Hold",
            ).id,
        )
    system = InMemoryConversationSystem()
    message = "Canonical proposal is ready."
    result = asyncio.run(
        send_system_message(
            system,
            tmp_db,
            fake_clock,
            holder,
            message,
            sender_message_id=f"path:{holder_kind}",
        )
    )

    assert isinstance(result.fate, PromptDeliveryStarted)
    assert result.conversation_id is not None
    observation = system.observations(result.conversation_id)[-1]
    assert observation.text == message
    assert observation.sender_label == "Panels"
    assert observation.sender is None
    assert observation.recipient is None


def test_crash_after_success_replays_same_id_without_duplicate_transcript(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Crash window", now=2)
    wake = wake_data.due(tmp_db, now=fake_clock.now_unix(), ticket_id=ticket_id)[0]
    system = InMemoryConversationSystem()
    first = asyncio.run(
        send_system_message(
            system,
            tmp_db,
            fake_clock,
            wake.holder,
            wake.message,
            sender_message_id=wake.sender_message_id,
        )
    )
    assert first.conversation_id is not None
    system.complete_running_turn(first.conversation_id)

    # The prompt succeeded, but the process died before marking its outbox row.
    assert asyncio.run(deliver_pending_wakes(system, tmp_db, fake_clock)) == 1
    matching = [
        observed
        for observed in system.observations(first.conversation_id)
        if observed.text == proposal_ready_message(ticket_id)
    ]
    assert len(matching) == 1


def test_definite_refusal_advances_attempt_then_recovery_delivers_once(
    tmp_db: Connection, fake_clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Retry", now=2)
    attempted_ids: list[str] = []
    transcript: list[str] = []

    async def refuse_then_accept(
        _conversations: object,
        _conn: Connection,
        _clock: Clock,
        recipient: Principal,
        message: str,
        *,
        sender_message_id: str,
    ) -> MessageDeliveryResult:
        attempted_ids.append(sender_message_id)
        if len(attempted_ids) == 1:
            return MessageDeliveryResult(
                recipient,
                "c_chief",
                PromptDeliveryRefused(PromptDeliveryRefusalReason.write_to_backend_failed),
            )
        transcript.append(message)
        return MessageDeliveryResult(recipient, "c_chief", PromptDeliveryStarted())

    monkeypatch.setattr(
        "planner.proposal_holder_wakes.runtime.message_delivery_service.send_system_message",
        refuse_then_accept,
    )
    assert asyncio.run(deliver_pending_wakes(object(), tmp_db, fake_clock)) == 0  # type: ignore[arg-type]
    tmp_db.execute(
        "UPDATE proposal_holder_wakes SET retry_at=? WHERE ticket_id=?",
        (fake_clock.now_unix(), ticket_id),
    )
    assert asyncio.run(deliver_pending_wakes(object(), tmp_db, fake_clock)) == 1  # type: ignore[arg-type]

    assert attempted_ids == [
        f"proposal-holder-wake:{ticket_id}:1:1",
        f"proposal-holder-wake:{ticket_id}:1:2",
    ]
    assert transcript == [proposal_ready_message(ticket_id)]


def test_queued_wake_stays_claimed_until_durable_replay_settles_it(
    tmp_db: Connection, fake_clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Queued", now=2)
    send = AsyncMock(
        side_effect=(
            MessageDeliveryResult(CHIEF_PRINCIPAL, "c", PromptDeliveryQueued(1)),
            MessageDeliveryResult(CHIEF_PRINCIPAL, "c", PromptDeliveryStarted()),
        )
    )
    monkeypatch.setattr(
        "planner.proposal_holder_wakes.runtime.message_delivery_service.send_system_message",
        send,
    )
    assert asyncio.run(deliver_pending_wakes(object(), tmp_db, fake_clock)) == 0  # type: ignore[arg-type]
    row = tmp_db.execute(
        "SELECT state FROM proposal_holder_wakes WHERE ticket_id=?", (ticket_id,)
    ).fetchone()
    assert row is not None and row["state"] == "delivering"
    assert (
        asyncio.run(
            deliver_pending_wakes(
                object(),  # type: ignore[arg-type]
                tmp_db,
                fake_clock,
                ticket_id=ticket_id,
            )
        )
        == 1
    )
    assert (
        send.await_args_list[0].kwargs["sender_message_id"]
        == (send.await_args_list[1].kwargs["sender_message_id"])
    )


def test_queued_wake_crash_recovery_reuses_the_same_attempt(
    tmp_db: Connection, fake_clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Queued crash", now=2)
    send = AsyncMock(
        side_effect=(
            MessageDeliveryResult(CHIEF_PRINCIPAL, "c", PromptDeliveryQueued(1)),
            MessageDeliveryResult(CHIEF_PRINCIPAL, "c", PromptDeliveryStarted()),
        )
    )
    monkeypatch.setattr(
        "planner.proposal_holder_wakes.runtime.message_delivery_service.send_system_message",
        send,
    )
    asyncio.run(deliver_pending_wakes(object(), tmp_db, fake_clock))  # type: ignore[arg-type]
    assert wake_data.recover_interrupted_deliveries(tmp_db, now=fake_clock.now_unix()) == 1
    assert asyncio.run(deliver_pending_wakes(object(), tmp_db, fake_clock)) == 1  # type: ignore[arg-type]
    assert (
        send.await_args_list[0].kwargs["sender_message_id"]
        == (send.await_args_list[1].kwargs["sender_message_id"])
    )


@pytest.mark.parametrize("operation", ["approve", "replace"])
def test_claimed_wake_does_not_block_proposal_writers(
    tmp_db: Connection,
    fake_clock: Clock,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Original", now=2)
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()["file"])
    started = asyncio.Event()
    release = asyncio.Event()
    send_count = 0

    async def blocked_send(*_args: object, **_kwargs: object) -> MessageDeliveryResult:
        nonlocal send_count
        send_count += 1
        started.set()
        await release.wait()
        return MessageDeliveryResult(CHIEF_PRINCIPAL, "c", PromptDeliveryStarted())

    monkeypatch.setattr(
        "planner.proposal_holder_wakes.runtime.message_delivery_service.send_system_message",
        blocked_send,
    )

    async def exercise() -> None:
        delivery = asyncio.create_task(
            deliver_pending_wakes(
                object(),  # type: ignore[arg-type]
                tmp_db,
                fake_clock,
                ticket_id=ticket_id,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        racer = connect(db_path)
        try:
            if operation == "approve":
                tickets_data.accept_proposal(
                    racer,
                    ticket_id,
                    field="success",
                    principal=CHIEF_PRINCIPAL,
                    now=3,
                    next_ceiling="needs_approach",
                    at_cap=AtCap.propose,
                    next_holder=OWNER_PRINCIPAL,
                )
            else:
                _file(racer, ticket_id, body="Replacement", now=3)
        finally:
            racer.close()
        release.set()
        assert await delivery == 0

    asyncio.run(exercise())
    assert send_count == 1
    pending = tickets_data.read_ticket(tmp_db, ticket_id).pending_proposal
    if operation == "approve":
        assert pending is None
        assert _wake_state(tmp_db, ticket_id) == "cancelled"
    else:
        assert pending is not None and pending.body == "Replacement"
        assert _wake_state(tmp_db, ticket_id) == "pending"


def test_claimed_wake_does_not_block_ticket_deletion(
    tmp_db: Connection, fake_clock: Clock
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Delete", now=2)
    assert len(
        wake_data.claim_due(
            tmp_db, now=fake_clock.now_unix(), ticket_id=ticket_id
        )
    ) == 1

    deleted = tickets_data.delete_ticket(
        tmp_db,
        ticket_id,
        principal=OWNER_PRINCIPAL,
        now=3,
    )

    assert deleted.ticket_id == ticket_id
    assert tmp_db.execute(
        "SELECT 1 FROM proposal_holder_wakes WHERE ticket_id=?", (ticket_id,)
    ).fetchone() is None


def test_rejection_messages_retry_in_order_with_stable_attempt_identity(
    tmp_db: Connection, fake_clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket_id = _target(tmp_db, OWNER_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Reject", now=2)
    _reject(tmp_db, ticket_id)
    send = AsyncMock(
        side_effect=(
            MessageDeliveryResult(
                Principal(PrincipalKind.ticket, ticket_id),
                "c-worker",
                PromptDeliveryRefused(
                    PromptDeliveryRefusalReason.write_to_backend_failed
                ),
            ),
            MessageDeliveryResult(
                Principal(PrincipalKind.ticket, ticket_id),
                "c-worker",
                PromptDeliveryStarted(),
            ),
            MessageDeliveryResult(
                Principal(PrincipalKind.ticket, ticket_id),
                "c-worker",
                PromptDeliveryStarted(),
            ),
        )
    )
    monkeypatch.setattr(
        "planner.proposal_holder_wakes.runtime.message_delivery_service."
        "send_ticket_outbox_message",
        send,
    )

    assert asyncio.run(
        deliver_pending_wakes(
            object(),  # type: ignore[arg-type]
            tmp_db,
            fake_clock,
            ticket_id=ticket_id,
        )
    ) == 0
    tmp_db.execute(
        "UPDATE ticket_rejection_messages SET retry_at=? WHERE ticket_id=?",
        (fake_clock.now_unix(), ticket_id),
    )
    assert asyncio.run(
        deliver_pending_wakes(
            object(),  # type: ignore[arg-type]
            tmp_db,
            fake_clock,
            ticket_id=ticket_id,
        )
    ) == 2
    ids = [call.kwargs["sender_message_id"] for call in send.await_args_list]
    assert ids == [
        f"ticket-rejection:{ticket_id}:1:1:1",
        f"ticket-rejection:{ticket_id}:1:1:2",
        f"ticket-rejection:{ticket_id}:1:2:1",
    ]
    assert [call.args[4] for call in send.await_args_list[1:]] == [
        proposal_returned_for_revision_prompt(),
        "Add evidence.",
    ]


def test_uncertain_rejection_message_is_terminal_and_not_retried(
    tmp_db: Connection, fake_clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket_id = _target(tmp_db, OWNER_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Reject", now=2)
    _reject(tmp_db, ticket_id)
    send = AsyncMock(
        return_value=MessageDeliveryResult(
            Principal(PrincipalKind.ticket, ticket_id),
            "c-worker",
            PromptDeliveryUncertain(),
        )
    )
    monkeypatch.setattr(
        "planner.proposal_holder_wakes.runtime.message_delivery_service."
        "send_ticket_outbox_message",
        send,
    )

    for _ in range(2):
        assert asyncio.run(
            deliver_pending_wakes(
                object(),  # type: ignore[arg-type]
                tmp_db,
                fake_clock,
                ticket_id=ticket_id,
            )
        ) == 0
    send.assert_awaited_once()
    states = tmp_db.execute(
        "SELECT sequence,state FROM ticket_rejection_messages "
        "WHERE ticket_id=? ORDER BY sequence",
        (ticket_id,),
    ).fetchall()
    assert [tuple(row) for row in states] == [(1, "uncertain"), (2, "pending")]


def test_recurring_loop_retries_temporary_startup_refusal_without_restart(
    tmp_db: Connection, fake_clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Loop retry", now=2)
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()["file"])
    attempts = 0

    async def temporary_refusal(*_args: object, **_kwargs: object) -> MessageDeliveryResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            test_clock = cast(MutableClock, fake_clock)
            test_clock.set(test_clock.now() + timedelta(seconds=2))
            return MessageDeliveryResult(
                CHIEF_PRINCIPAL,
                "c",
                PromptDeliveryRefused(PromptDeliveryRefusalReason.write_to_backend_failed),
            )
        return MessageDeliveryResult(CHIEF_PRINCIPAL, "c", PromptDeliveryStarted())

    monkeypatch.setattr(
        "planner.proposal_holder_wakes.runtime.message_delivery_service.send_system_message",
        temporary_refusal,
    )

    async def exercise() -> None:
        loop = ProposalHolderWakeLoop(
            db_path,
            fake_clock,
            conversation_system=object(),  # type: ignore[arg-type]
            asyncio_loop=asyncio.get_running_loop(),
        )
        loop.start(0.05)  # type: ignore[arg-type]
        try:
            row = None
            for _ in range(100):
                row = tmp_db.execute(
                    "SELECT state FROM proposal_holder_wakes WHERE ticket_id=?", (ticket_id,)
                ).fetchone()
                if row is not None and row["state"] == "delivered":
                    break
                await asyncio.sleep(0.02)
            assert row is not None and row["state"] == "delivered"
        finally:
            await asyncio.to_thread(loop.stop)

    asyncio.run(exercise())
    assert attempts == 2


def test_lock_owned_recurring_loop_delivers_new_pending_wake_once(
    tmp_db: Connection, fake_clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Loop owns delivery", now=2)
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()["file"])
    send = AsyncMock(
        return_value=MessageDeliveryResult(CHIEF_PRINCIPAL, "c", PromptDeliveryStarted())
    )
    monkeypatch.setattr(
        "planner.proposal_holder_wakes.runtime.message_delivery_service.send_system_message",
        send,
    )

    async def exercise() -> None:
        loop = ProposalHolderWakeLoop(
            db_path,
            fake_clock,
            conversation_system=object(),  # type: ignore[arg-type]
            asyncio_loop=asyncio.get_running_loop(),
        )
        loop.start(0.05)  # type: ignore[arg-type]
        try:
            for _ in range(100):
                if _wake_state(tmp_db, ticket_id) == "delivered":
                    break
                await asyncio.sleep(0.01)
            assert _wake_state(tmp_db, ticket_id) == "delivered"
        finally:
            assert await asyncio.to_thread(loop.stop) is True

    asyncio.run(exercise())
    send.assert_awaited_once()


def test_only_machine_lock_owner_recovers_interrupted_delivery(
    tmp_db: Connection,
    fake_clock: Clock,
    cfg: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Claimed", now=2)
    assert len(wake_data.claim_due(tmp_db, now=fake_clock.now_unix())) == 1
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()["file"])
    lock_path = tmp_path / "proposal-wake.lock"
    config = replace(
        cfg,
        db_path=db_path,
        dispatcher_lock_path=str(lock_path),
        dispatch_enabled=True,
    )
    other_process = _start_other_process_lock(lock_path)
    asyncio_loop = asyncio.new_event_loop()
    try:
        non_owner = start_background_loops(
            config,
            fake_clock,
            conversation_system=object(),  # type: ignore[arg-type]
            worker_context_service=object(),  # type: ignore[arg-type]
            asyncio_loop=asyncio_loop,
        )
        assert _wake_state(tmp_db, ticket_id) == "delivering"
        assert asyncio.run(non_owner.stop()) is True
    finally:
        assert other_process.stdin is not None
        other_process.stdin.close()
        other_process.wait(timeout=5)

    class IdleLoop:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start(self, _interval: int) -> None:
            pass

        def stop(self, *, deadline: float | None = None) -> bool:
            del deadline
            return True

        def wake(self) -> None:
            pass

    monkeypatch.setattr("planner.core.loops.ScheduledTicketLoop", IdleLoop)
    monkeypatch.setattr("planner.core.loops.WorkerStepReadinessLoop", IdleLoop)
    monkeypatch.setattr("planner.core.loops.NotificationLoop", IdleLoop)
    monkeypatch.setattr("planner.core.loops.ProposalHolderWakeLoop", IdleLoop)
    owner = start_background_loops(
        config,
        fake_clock,
        conversation_system=object(),  # type: ignore[arg-type]
        worker_context_service=object(),  # type: ignore[arg-type]
        asyncio_loop=asyncio_loop,
    )
    try:
        assert _wake_state(tmp_db, ticket_id) == "pending"
    finally:
        assert asyncio.run(owner.stop()) is True
        asyncio_loop.close()


def test_shutdown_timeout_retains_machine_lock_until_process_exit(
    tmp_db: Connection,
    fake_clock: Clock,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Blocked shutdown", now=2)
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()["file"])
    lock_path = tmp_path / "shutdown.lock"
    assert ensure_machine_lock(str(lock_path))
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked_send(*_args: object, **_kwargs: object) -> MessageDeliveryResult:
        started.set()
        await release.wait()
        return MessageDeliveryResult(CHIEF_PRINCIPAL, "c", PromptDeliveryStarted())

    monkeypatch.setattr(
        "planner.proposal_holder_wakes.runtime.message_delivery_service.send_system_message",
        blocked_send,
    )

    async def exercise() -> None:
        wake_loop = ProposalHolderWakeLoop(
            db_path,
            fake_clock,
            conversation_system=object(),  # type: ignore[arg-type]
            asyncio_loop=asyncio.get_running_loop(),
        )
        wake_loop.start(0.01)  # type: ignore[arg-type]
        await asyncio.wait_for(started.wait(), timeout=2)
        loops = BackgroundLoops(
            lock_path=str(lock_path),
            shutdown_grace_seconds=0.01,
            proposal_holder_wake_loop=wake_loop,
        )
        assert await loops.stop(deadline=monotonic() + 0.01) is False
        assert _other_process_can_lock(lock_path) is False
        release.set()
        await asyncio.sleep(0)

    try:
        asyncio.run(exercise())
    finally:
        release_machine_lock(str(lock_path))
    assert _other_process_can_lock(lock_path) is True


def test_unsettled_shutdown_refuses_same_process_restart(
    tmp_db: Connection,
    fake_clock: Clock,
    cfg: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()["file"])
    lock_path = tmp_path / "same-process-restart.lock"
    config = replace(
        cfg,
        db_path=db_path,
        dispatcher_lock_path=str(lock_path),
        dispatch_enabled=True,
    )

    class IdleLoop:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start(self, _interval: int) -> None:
            pass

        def stop(self, *, deadline: float | None = None) -> bool:
            del deadline
            return True

        def wake(self) -> None:
            pass

    class UnsettledWakeLoop(IdleLoop):
        def stop(self, *, deadline: float | None = None) -> bool:
            del deadline
            return False

    monkeypatch.setattr(core_loops, "ScheduledTicketLoop", IdleLoop)
    monkeypatch.setattr(core_loops, "WorkerStepReadinessLoop", IdleLoop)
    monkeypatch.setattr(core_loops, "NotificationLoop", IdleLoop)
    monkeypatch.setattr(core_loops, "ProposalHolderWakeLoop", UnsettledWakeLoop)
    asyncio_loop = asyncio.new_event_loop()
    owner = start_background_loops(
        config,
        fake_clock,
        conversation_system=object(),  # type: ignore[arg-type]
        worker_context_service=object(),  # type: ignore[arg-type]
        asyncio_loop=asyncio_loop,
    )
    try:
        assert asyncio.run(owner.stop(deadline=monotonic())) is False
        with pytest.raises(RuntimeError, match="background loops already running"):
            start_background_loops(
                config,
                fake_clock,
                conversation_system=object(),  # type: ignore[arg-type]
                worker_context_service=object(),  # type: ignore[arg-type]
                asyncio_loop=asyncio_loop,
            )
        assert _other_process_can_lock(lock_path) is False
    finally:
        release_machine_lock(str(lock_path))
        core_loops._active = None
        asyncio_loop.close()


def test_queued_delivery_retains_lock_and_refuses_restart_after_future_completes(
    tmp_db: Connection,
    fake_clock: Clock,
    cfg: Config,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Queued at shutdown", now=2)
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()["file"])
    lock_path = tmp_path / "queued-shutdown.lock"
    config = replace(
        cfg,
        db_path=db_path,
        dispatcher_lock_path=str(lock_path),
        dispatch_enabled=True,
        tick_seconds=3600,
    )

    class IdleLoop:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def start(self, _interval: int) -> None:
            pass

        def stop(self, *, deadline: float | None = None) -> bool:
            del deadline
            return True

        def wake(self) -> None:
            pass

    monkeypatch.setattr(core_loops, "ScheduledTicketLoop", IdleLoop)
    monkeypatch.setattr(core_loops, "WorkerStepReadinessLoop", IdleLoop)
    monkeypatch.setattr(core_loops, "NotificationLoop", IdleLoop)
    send = AsyncMock(
        return_value=MessageDeliveryResult(CHIEF_PRINCIPAL, "c", PromptDeliveryQueued(1))
    )
    monkeypatch.setattr(
        "planner.proposal_holder_wakes.runtime.message_delivery_service.send_system_message",
        send,
    )

    async def exercise() -> None:
        owner = start_background_loops(
            config,
            fake_clock,
            conversation_system=object(),  # type: ignore[arg-type]
            worker_context_service=object(),  # type: ignore[arg-type]
            asyncio_loop=asyncio.get_running_loop(),
        )
        wake_loop = owner.proposal_holder_wake_loop
        assert wake_loop is not None
        try:
            for _ in range(100):
                with wake_loop._in_flight_lock:
                    future_completed = not wake_loop._in_flight
                if _wake_state(tmp_db, ticket_id) == "delivering" and future_completed:
                    break
                await asyncio.sleep(0.01)
            assert send.await_count >= 1
            assert {
                call.kwargs["sender_message_id"] for call in send.await_args_list
            } == {send.await_args_list[0].kwargs["sender_message_id"]}
            assert _wake_state(tmp_db, ticket_id) == "delivering"
            with wake_loop._in_flight_lock:
                assert not wake_loop._in_flight
            assert await owner.stop() is False
            assert _other_process_can_lock(lock_path) is False
            with pytest.raises(RuntimeError, match="background loops already running"):
                start_background_loops(
                    config,
                    fake_clock,
                    conversation_system=object(),  # type: ignore[arg-type]
                    worker_context_service=object(),  # type: ignore[arg-type]
                    asyncio_loop=asyncio.get_running_loop(),
                )
        finally:
            await owner.stop()

    try:
        asyncio.run(exercise())
    finally:
        release_machine_lock(str(lock_path))
        core_loops._active = None


@pytest.mark.parametrize("terminal_state", ["delivered", "cancelled", "uncertain"])
def test_terminal_wake_state_does_not_retain_machine_lock(
    tmp_db: Connection,
    fake_clock: Clock,
    terminal_state: str,
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Settled", now=2)
    tmp_db.execute(
        "UPDATE proposal_holder_wakes SET state=? WHERE ticket_id=?",
        (terminal_state, ticket_id),
    )
    db_path = str(tmp_db.execute("PRAGMA database_list").fetchone()["file"])
    asyncio_loop = asyncio.new_event_loop()
    loop = ProposalHolderWakeLoop(
        db_path,
        fake_clock,
        conversation_system=object(),  # type: ignore[arg-type]
        asyncio_loop=asyncio_loop,
    )
    try:
        assert loop.stop() is True
    finally:
        asyncio_loop.close()


def test_replacement_supersedes_generation_and_owner_never_gets_an_outbox_row(
    tmp_db: Connection,
) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="First", now=2)
    _file(tmp_db, ticket_id, body="Replacement", now=3)
    row = tmp_db.execute(
        "SELECT proposal_generation,delivery_attempt,state FROM proposal_holder_wakes "
        "WHERE ticket_id=?",
        (ticket_id,),
    ).fetchone()
    assert row is not None
    assert tuple(row) == (2, 1, "pending")

    owner_ticket_id = _target(tmp_db, OWNER_PRINCIPAL, now=4)
    _file(tmp_db, owner_ticket_id, body="Owner review", now=5)
    assert (
        tmp_db.execute(
            "SELECT 1 FROM proposal_holder_wakes WHERE ticket_id=?", (owner_ticket_id,)
        ).fetchone()
        is None
    )


def test_approval_cancels_a_stale_pending_wake(tmp_db: Connection) -> None:
    ticket_id = _target(tmp_db, CHIEF_PRINCIPAL)
    _file(tmp_db, ticket_id, body="Approve me", now=2)
    tickets_data.accept_proposal(
        tmp_db,
        ticket_id,
        field="success",
        principal=CHIEF_PRINCIPAL,
        now=3,
        next_ceiling="needs_approach",
        at_cap=AtCap.propose,
        next_holder=OWNER_PRINCIPAL,
    )
    row = tmp_db.execute(
        "SELECT state FROM proposal_holder_wakes WHERE ticket_id=?", (ticket_id,)
    ).fetchone()
    assert row is not None
    assert row["state"] == "cancelled"
