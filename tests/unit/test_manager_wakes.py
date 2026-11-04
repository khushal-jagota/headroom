"""Durable source and delivery rules for Sprint Item manager wakes."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from sqlite3 import Connection
from typing import Any

import pytest

from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
    PromptDeliveryMode,
    PromptDeliveryStarted,
)
from planner.conversation.events import (
    AgentMessageEventPayload,
    ConversationTurnEnding,
    PromptDiscardedEventPayload,
    PromptEventPayload,
    TurnEndedEventPayload,
)
from planner.conversation.lifecycle import (
    LifecycleFact,
    LifecycleKind,
    LifecycleOperation,
    LifecycleStatus,
    LifecycleTransition,
)
from planner.conversation.logic.conversation_start_resolution import (
    resolve_conversation_start_request,
)
from planner.conversation.message_content import MessageContent, text_message_content
from planner.conversation.storage import ConversationStore
from planner.core import change_signal
from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import CHIEF_PRINCIPAL, OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.manager_wakes import data as wake_data
from planner.manager_wakes.contracts import WakeBatchStatus, WakeSourceKind
from planner.manager_wakes.runtime import (
    ManagerWakeLoop,
    deliver_batch,
    reconcile_batch_outcomes,
)
from planner.managers import data as managers_data
from planner.managers.contracts import ManagerLaunchConfiguration
from planner.runtime import conversation_start
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS


@pytest.mark.parametrize(
    "source_kind",
    (WakeSourceKind.proposal, WakeSourceKind.worker_error, WakeSourceKind.turn_failure),
)
def test_ticket_notice_requires_ticket_before_insert(
    tmp_db: Connection, source_kind: WakeSourceKind
) -> None:
    with pytest.raises(ValueError, match="ticket notice requires a Ticket"):
        wake_data.create_wake(
            tmp_db,
            manager_id=None,
            ticket_id=None,
            source_kind=source_kind,
            source_revision=1,
            summary="Invalid ticket notice",
            now=1,
            target=CHIEF_PRINCIPAL,
        )
    assert tmp_db.execute("SELECT COUNT(*) FROM manager_wakes").fetchone()[0] == 0


def _item_and_ticket(conn: Connection, clock: FakeClock) -> tuple[str, str]:
    item = managers_data.create_manager(
        conn, title="Managed manager", project_id="project_vylo", clock=clock
    )
    ticket = tickets_data.create_ticket(
        conn,
        title="Managed ticket",
        principal=Principal(PrincipalKind.manager, item.id),
        now=clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        brief="Start",
        manager_id=item.id,
        stated_ceiling="needs_what_changes",
    )
    return item.id, ticket.id


def _wake_rows(conn: Connection) -> list[dict[str, object]]:
    return [dict(row) for row in conn.execute("SELECT * FROM manager_wakes ORDER BY id")]


def _current_ticket_conversation(conn: Connection, ticket_id: str, conversation_id: str) -> None:
    conn.execute(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,access,"
        "created_at) VALUES (?,'codex','model','/tmp','full',1)",
        (conversation_id,),
    )
    conn.execute(
        "UPDATE tickets SET conversation_id=? WHERE id=?", (conversation_id, ticket_id)
    )


def _append_failed_turn(store: ConversationStore, conversation_id: str) -> None:
    asyncio.run(
        store.append_turn_ending(
            conversation_id,
            (
                AgentMessageEventPayload(text_message_content("progress before failure")),
                TurnEndedEventPayload(ConversationTurnEnding.failed, "provider unavailable"),
            ),
            agent_activity=True,
            automatic_compaction_confirmed=False,
            automatic_compaction_result=None,
            create_failure_notice=True,
        )
    )


@pytest.mark.parametrize("holder_kind", ["chief", "manager", "ticket"])
def test_failed_ticket_turn_snapshots_each_employee_ceiling_holder(
    tmp_db: Connection, fake_clock: FakeClock, holder_kind: str
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    holder_id = item_id
    if holder_kind == "chief":
        holder_id = "chief"
    elif holder_kind == "ticket":
        holder_id = tickets_data.create_ticket(
            tmp_db,
            title="Reviewing ticket",
            principal=OWNER_PRINCIPAL,
            now=2,
            title_max_chars=TITLE_MAX_CHARS,
            worker_type="coding",
            brief="Start",
            stated_ceiling="needs_what_changes",
        ).id
    tmp_db.execute(
        "UPDATE tickets SET ceiling_holder=? WHERE id=?",
        (json.dumps({"kind": holder_kind, "id": holder_id}), ticket_id),
    )
    _current_ticket_conversation(tmp_db, ticket_id, "conv-failed")
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])

    _append_failed_turn(ConversationStore(path, integer_now=lambda: 50), "conv-failed")

    row = _wake_rows(tmp_db)[0]
    assert (row["target_kind"], row["target_id"]) == (holder_kind, holder_id)
    assert (row["source_conversation_id"], row["source_revision"]) == ("conv-failed", 2)
    assert "provider unavailable" in str(row["summary"])




def test_failed_turn_notice_source_identity_is_idempotent(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    target = Principal(PrincipalKind.manager, item_id)
    def create_notice() -> bool:
        return wake_data.create_turn_failure_wake(
            tmp_db,
            target=target,
            ticket_id=ticket_id,
            ticket_title="Managed ticket",
            conversation_id="conv-source",
            event_sequence=12,
            error_summary="provider unavailable",
            now=50,
        )

    assert create_notice()
    assert not create_notice()
    assert len(_wake_rows(tmp_db)) == 1


@pytest.mark.parametrize("target_kind,target_id", [("chief", "chief"), ("ticket", None)])
def test_failure_notices_use_the_chief_and_ticket_conversation_doors(
    tmp_db: Connection,
    target_kind: str,
    target_id: str | None,
) -> None:
    source_id = tickets_data.create_ticket(
        tmp_db,
        title="Failed ticket",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        brief="Start",
        stated_ceiling="needs_what_changes",
    ).id
    if target_kind == "ticket":
        target_id = tickets_data.create_ticket(
            tmp_db,
            title="Responsible ticket",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
            worker_type="coding",
            brief="Start",
            stated_ceiling="needs_what_changes",
        ).id
    assert target_id is not None
    target = Principal(PrincipalKind(target_kind), target_id)
    wake_data.create_turn_failure_wake(
        tmp_db,
        target=target,
        ticket_id=source_id,
        ticket_title="Failed ticket",
        conversation_id="conv-source",
        event_sequence=4,
        error_summary="provider unavailable",
        now=2,
    )
    batch = wake_data.claim_next_batch(tmp_db, process_token="process", now=3)
    assert batch is not None
    database_path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))
    system = _DurableStartedConversationSystem(database_path)

    assert asyncio.run(
        deliver_batch(
            batch,
            connect_database=lambda: connect(str(database_path)),
            conversation_system=system,  # type: ignore[arg-type]
            process_token="process",
            now=lambda: 50,
        )
    )
    assert system.started is not None
    prompts = tmp_db.execute(
        "SELECT payload FROM conversation_events WHERE kind='prompt'"
    ).fetchall()
    assert any("provider unavailable" in str(row["payload"]) for row in prompts)




def test_ticket_reset_between_resolution_and_link_lock_uses_the_new_door(
    tmp_db: Connection,
) -> None:
    source = tickets_data.create_ticket(
        tmp_db,
        title="Failed ticket",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        brief="Start",
        stated_ceiling="needs_what_changes",
    )
    target = tickets_data.create_ticket(
        tmp_db,
        title="Reset recipient",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        brief="Start",
        stated_ceiling="needs_what_changes",
    )
    _current_ticket_conversation(tmp_db, target.id, "conv-before-reset")
    wake_data.create_turn_failure_wake(
        tmp_db,
        target=Principal(PrincipalKind.ticket, target.id),
        ticket_id=source.id,
        ticket_title=source.title,
        conversation_id="conv-source",
        event_sequence=1,
        error_summary="failed",
        now=2,
    )
    batch = wake_data.claim_next_batch(tmp_db, process_token="process", now=3)
    assert batch is not None
    database_path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))
    system = _DurableStartedConversationSystem(database_path)

    async def exercise() -> None:
        lock = conversation_start.conversation_link_lock(f"ticket:{target.id}")
        async with lock:
            delivery = asyncio.create_task(
                deliver_batch(
                    batch,
                    connect_database=lambda: connect(str(database_path)),
                    conversation_system=system,  # type: ignore[arg-type]
                    process_token="process",
                    now=lambda: 50,
                )
            )
            await asyncio.sleep(0)
            tmp_db.execute(
                "UPDATE tickets SET conversation_id=NULL WHERE id=?", (target.id,)
            )
        assert await delivery

    asyncio.run(exercise())
    current = tmp_db.execute(
        "SELECT conversation_id FROM tickets WHERE id=?", (target.id,)
    ).fetchone()[0]
    assert current is not None and current != "conv-before-reset"


def test_busy_ticket_target_releases_batch_and_restart_recovery_is_target_agnostic(
    tmp_db: Connection,
) -> None:
    source = tickets_data.create_ticket(
        tmp_db,
        title="Failed ticket",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        brief="Start",
        stated_ceiling="needs_what_changes",
    )
    target = tickets_data.create_ticket(
        tmp_db,
        title="Busy recipient",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        brief="Start",
        stated_ceiling="needs_what_changes",
    )
    _current_ticket_conversation(tmp_db, target.id, "conv-busy-ticket")
    wake_data.create_turn_failure_wake(
        tmp_db,
        target=Principal(PrincipalKind.ticket, target.id),
        ticket_id=source.id,
        ticket_title=source.title,
        conversation_id="conv-source",
        event_sequence=1,
        error_summary="failed",
        now=2,
    )
    batch = wake_data.claim_next_batch(tmp_db, process_token="old-process", now=3)
    assert batch is not None
    database_path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))

    assert not asyncio.run(
        deliver_batch(
            batch,
            connect_database=lambda: connect(str(database_path)),
            conversation_system=_ActiveConversationSystem(database_path),  # type: ignore[arg-type]
            process_token="old-process",
            now=lambda: 4,
        )
    )
    assert tmp_db.execute(
        "SELECT count(*) FROM manager_wake_batches WHERE id=?", (batch.id,)
    ).fetchone()[0] == 0

    recovered = wake_data.claim_next_batch(tmp_db, process_token="old-process", now=5)
    assert recovered is not None
    wake_data.record_batch_offering(
        tmp_db,
        recovered.id,
        conversation_id="conv-attempt",
        process_token="old-process",
        now=6,
    )
    wake_data.record_batch_accepted(
        tmp_db,
        recovered.id,
        conversation_id="conv-attempt",
        process_token="old-process",
        now=6,
    )
    assert wake_data.recover_accepted_batches_from_other_processes(
        tmp_db, process_token="new-process", now=7
    ) == 1
    row = tmp_db.execute(
        "SELECT status,conversation_id,process_token,target_kind,target_id "
        "FROM manager_wake_batches WHERE id=?",
        (recovered.id,),
    ).fetchone()
    assert tuple(row) == ("pending", None, None, "ticket", target.id)


class _DurableStartedConversationSystem:
    """A durable prompt store behind a minimal successful backend boundary."""

    def __init__(self, database_path: Path) -> None:
        self.store = ConversationStore(str(database_path), integer_now=lambda: 50)
        self.started: ConversationStartRequest | None = None

    async def start_conversation(self, request: ConversationStartRequest) -> None:
        self.started = request
        await self.store.create_conversation(resolve_conversation_start_request(request))

    async def send(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        mode: PromptDeliveryMode,
        sender_message_id: str | None = None,
        **_unused: Any,
    ) -> PromptDeliveryStarted:
        await self.store.append_event(
            conversation_id,
            PromptEventPayload(
                content=content,
                sender_label=sender_label,
                mode=mode,
                sender_message_id=sender_message_id,
            ),
        )
        return PromptDeliveryStarted()

    async def kill(self, _conversation_id: str) -> None:
        return None

    async def is_running(self, _conversation_id: str) -> bool:
        return False


class _ActiveConversationSystem(_DurableStartedConversationSystem):
    async def is_running(self, _conversation_id: str) -> bool:
        return True

    async def send(self, *_args: Any, **_kwargs: Any) -> PromptDeliveryStarted:
        raise AssertionError("a busy manager must not receive or interrupt a prompt")


def test_manager_proposal_wakes_are_atomic_and_each_file_gets_a_generation(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    worker = Principal(PrincipalKind.ticket, ticket_id)

    tickets_data.file_current_proposal(
        tmp_db, ticket_id, body="Same proposal", principal=worker, now=100
    )
    tickets_data.file_current_proposal(
        tmp_db, ticket_id, body="Same proposal", principal=worker, now=100
    )

    rows = _wake_rows(tmp_db)
    assert [(row["manager_id"], row["source_revision"]) for row in rows] == [
        (item_id, 1),
        (item_id, 2),
    ]
    assert {row["source_kind"] for row in rows} == {"proposal"}










def test_readdressing_a_parked_proposal_to_the_item_manager_creates_one_new_wake(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    manager = Principal(PrincipalKind.manager, item_id)
    tickets_data.edit_ticket(
        tmp_db,
        ticket_id,
        edit={"ceiling_holder": OWNER_PRINCIPAL},
        title_max_chars=TITLE_MAX_CHARS,
        principal=manager,
        now=2,
    )
    parked = tickets_data.file_current_proposal(
        tmp_db,
        ticket_id,
        body="Review after handoff",
        principal=Principal(PrincipalKind.ticket, ticket_id),
        now=3,
    )
    assert parked.pending_proposal is not None
    assert _wake_rows(tmp_db) == []

    tickets_data.edit_ticket(
        tmp_db,
        ticket_id,
        edit={"ceiling_holder": manager},
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=4,
    )
    assert (
        tmp_db.execute(
            "SELECT pending_proposal_revision FROM tickets WHERE id=?", (ticket_id,)
        ).fetchone()[0]
        == 2
    )
    assert [(row["manager_id"], row["source_revision"]) for row in _wake_rows(tmp_db)] == [
        (item_id, 2)
    ]

    tickets_data.edit_ticket(
        tmp_db,
        ticket_id,
        edit={"ceiling_holder": manager},
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=5,
    )
    assert (
        tmp_db.execute(
            "SELECT pending_proposal_revision FROM tickets WHERE id=?", (ticket_id,)
        ).fetchone()[0]
        == 2
    )
    assert len(_wake_rows(tmp_db)) == 1




def test_moving_a_parked_proposal_wakes_the_new_manager_and_removes_old_authority(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    old_item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    old_manager = Principal(PrincipalKind.manager, old_item_id)
    tickets_data.file_current_proposal(
        tmp_db,
        ticket_id,
        body="Move this review",
        principal=Principal(PrincipalKind.ticket, ticket_id),
        now=2,
    )
    new_item = managers_data.create_manager(
        tmp_db, title="New managed manager", project_id="project_vylo", clock=fake_clock
    )
    new_manager = Principal(PrincipalKind.manager, new_item.id)
    tickets_data.edit_ticket(
        tmp_db,
        ticket_id,
        edit={"manager_id": new_item.id, "ceiling_holder": new_manager},
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=3,
    )
    rows = _wake_rows(tmp_db)
    assert [(row["manager_id"], row["source_revision"]) for row in rows] == [
        (old_item_id, 1),
        (new_item.id, 2),
    ]
    with pytest.raises(PlannerError) as forbidden:
        tickets_data.accept_proposal(
            tmp_db,
            ticket_id,
            field="what_changes",
            principal=old_manager,
            now=4,
            next_ceiling="needs_implementation",
            next_holder=old_manager,
        )
    assert forbidden.value.code is ErrorCode.agent_forbidden


def test_manager_routed_kickoff_proposal_creates_its_wake_with_ticket(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item = managers_data.create_manager(
        tmp_db, title="Managed manager", project_id="project_vylo", clock=fake_clock
    )
    ticket = tickets_data.create_ticket(
        tmp_db,
        title="Kickoff waits here",
        principal=Principal(PrincipalKind.manager, item.id),
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        brief="Review this kickoff",
        manager_id=item.id,
        stated_ceiling="needs_brief",
    )
    assert ticket.pending_proposal is not None
    rows = _wake_rows(tmp_db)
    assert len(rows) == 1
    assert rows[0]["ticket_id"] == ticket.id
    assert rows[0]["source_revision"] == 1




def test_real_error_transitions_use_claim_revisions_and_repeated_writes_do_not_duplicate(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    first = tickets_data.mark_ticket_errored(tmp_db, ticket_id, now=200)
    repeated = tickets_data.mark_ticket_errored(tmp_db, ticket_id, now=200)
    assert first.worker_step_claim_revision == repeated.worker_step_claim_revision == 1
    rows = _wake_rows(tmp_db)
    assert len(rows) == 1
    assert rows[0]["manager_id"] == item_id
    assert rows[0]["source_kind"] == "worker_error"
    assert rows[0]["source_revision"] == 1


def test_batches_group_wakes_and_close_only_for_the_exact_prompt(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    tickets_data.file_current_proposal(
        tmp_db,
        ticket_id,
        body="Review this",
        principal=Principal(PrincipalKind.ticket, ticket_id),
        now=11,
    )
    tmp_db.execute("UPDATE manager_wakes SET summary='Event 1' WHERE ticket_id=?", (ticket_id,))
    for revision, kind in enumerate(
        (WakeSourceKind.worker_error, WakeSourceKind.turn_failure), start=2
    ):
        wake_data.create_wake(
            tmp_db,
            manager_id=item_id,
            ticket_id=ticket_id,
            source_kind=kind,
            source_revision=revision,
            summary=f"Event {revision}",
            now=10 + revision,
        )
    batch = wake_data.claim_next_batch(tmp_db, process_token="process-a", now=20)
    assert batch is not None
    assert "Event 1" in batch.message and "Event 2" in batch.message
    assert batch.sender_message_id.startswith("manager_delivery_")

    tmp_db.execute(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,access,"
        "created_at) VALUES ('conv-manager','codex','model','/tmp','full',1)"
    )
    wake_data.record_batch_offering(
        tmp_db,
        batch.id,
        conversation_id="conv-manager",
        process_token="process-a",
        now=20,
    )
    wake_data.record_batch_accepted(
        tmp_db,
        batch.id,
        conversation_id="conv-manager",
        process_token="process-a",
        now=21,
    )
    assert reconcile_batch_outcomes(tmp_db, now=22) == 0
    assert all(row["closed_at"] is None for row in _wake_rows(tmp_db))

    payload = json.dumps({"sender_message_id": batch.sender_message_id})
    tmp_db.execute(
        "INSERT INTO conversation_events(conversation_id,sequence,kind,payload,created_at) "
        "VALUES ('conv-manager',1,'prompt',?,22)",
        (payload,),
    )
    assert reconcile_batch_outcomes(tmp_db, now=23) == 1
    assert all(row["closed_at"] == 23 for row in _wake_rows(tmp_db))


def test_busy_manager_leaves_sources_unclaimed_so_later_wakes_combine(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    wake_data.create_worker_error_wake(
        tmp_db,
        manager_id=item_id,
        ticket_id=ticket_id,
        claim_revision=1,
        ticket_title="Managed ticket",
        now=1,
    )
    batch = wake_data.claim_next_batch(tmp_db, process_token="process", now=2)
    assert batch is not None
    database_path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))
    setup_system = _DurableStartedConversationSystem(database_path)

    assert asyncio.run(
        deliver_batch(
            batch,
            connect_database=lambda: connect(str(database_path)),
            conversation_system=setup_system,  # type: ignore[arg-type]
            process_token="process",
            now=lambda: 50,
        )
    )
    wake_data.create_worker_error_wake(
        tmp_db,
        manager_id=item_id,
        ticket_id=ticket_id,
        claim_revision=2,
        ticket_title="Managed ticket",
        now=51,
    )
    waiting = wake_data.claim_next_batch(tmp_db, process_token="process", now=52)
    assert waiting is not None
    system = _ActiveConversationSystem(database_path)

    delivered = asyncio.run(
        deliver_batch(
            waiting,
            connect_database=lambda: connect(str(database_path)),
            conversation_system=system,  # type: ignore[arg-type]
            process_token="process",
            now=lambda: 50,
        )
    )
    assert delivered is False
    assert (
        tmp_db.execute(
            "SELECT count(*) FROM manager_wake_batches WHERE id=?", (waiting.id,)
        ).fetchone()[0]
        == 0
    )
    tickets_data.file_current_proposal(
        tmp_db,
        ticket_id,
        body="Review the current proposal",
        principal=Principal(PrincipalKind.ticket, ticket_id),
        now=53,
    )
    combined = wake_data.claim_next_batch(tmp_db, process_token="process", now=54)
    assert combined is not None
    assert "explicit worker-error" in combined.message
    assert "filed a proposal" in combined.message


def test_running_wake_loop_does_not_spin_on_active_manager_bookkeeping(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    wake_data.create_worker_error_wake(
        tmp_db,
        manager_id=item_id,
        ticket_id=ticket_id,
        claim_revision=1,
        ticket_title="Managed ticket",
        now=1,
    )
    agent_key = managers_data.manager_agent_key(item_id)
    tmp_db.execute(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,access,"
        "created_at) VALUES ('conv-active','hermes','model','/tmp','full',1)"
    )
    tmp_db.execute(
        "UPDATE agents SET conversation_id='conv-active' WHERE agent_key=?", (agent_key,)
    )
    database_path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))
    event_loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=event_loop.run_forever)
    loop_thread.start()
    wake_loop = ManagerWakeLoop(
        str(database_path),
        fake_clock,
        conversation_system=_ActiveConversationSystem(database_path),  # type: ignore[arg-type]
        asyncio_loop=event_loop,
    )
    unsubscribe = change_signal.subscribe(wake_loop.wake)

    def batches_minted() -> int:
        row = tmp_db.execute(
            "SELECT seq FROM sqlite_sequence WHERE name='manager_wake_batches'"
        ).fetchone()
        return 0 if row is None else int(row[0])

    def wait_for_claim(number: int) -> None:
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            batch_count = tmp_db.execute(
                "SELECT count(*) FROM manager_wake_batches"
            ).fetchone()[0]
            if batches_minted() >= number and batch_count == 0:
                return
            time.sleep(0.01)
        raise AssertionError("wake loop did not finish active-manager deferral")

    try:
        wake_loop.start(interval=10)
        wait_for_claim(1)
        time.sleep(0.1)
        assert batches_minted() == 1

        # A real external change wakes one new attempt. Its internal claim and release
        # stay quiet, so this also stops at one attempt instead of feeding itself.
        change_signal.emit()
        wait_for_claim(2)
        time.sleep(0.1)
        assert batches_minted() == 2
    finally:
        unsubscribe()
        wake_loop.stop()
        event_loop.call_soon_threadsafe(event_loop.stop)
        loop_thread.join()
        event_loop.close()


def test_definite_failure_waits_then_creates_a_new_attempt_id(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    wake_data.create_worker_error_wake(
        tmp_db,
        manager_id=item_id,
        ticket_id=ticket_id,
        claim_revision=1,
        ticket_title="Managed ticket",
        now=1,
    )
    first = wake_data.claim_next_batch(tmp_db, process_token="one", now=10)
    assert first is not None
    wake_data.record_batch_failure(
        tmp_db,
        first.id,
        status=WakeBatchStatus.refused,
        conversation_id=None,
        now=10,
    )
    assert wake_data.claim_next_batch(tmp_db, process_token="one", now=14) is None
    second = wake_data.claim_next_batch(tmp_db, process_token="one", now=16)
    assert second is not None
    assert second.sender_message_id != first.sender_message_id


def test_restart_preserves_dispatching_as_uncertain_and_recovers_known_held_batch(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    wake_data.create_worker_error_wake(
        tmp_db,
        manager_id=item_id,
        ticket_id=ticket_id,
        claim_revision=1,
        ticket_title="Managed ticket",
        now=1,
    )
    batch = wake_data.claim_next_batch(tmp_db, process_token="old", now=2)
    assert batch is not None
    wake_data.record_batch_offering(
        tmp_db,
        batch.id,
        conversation_id="conv-intended",
        process_token="old",
        now=3,
    )
    assert wake_data.preserve_interrupted_dispatches(tmp_db, process_token="new", now=4) == 1
    stored = wake_data.batches_waiting_for_outcome(tmp_db)[0]
    assert stored.status is WakeBatchStatus.uncertain
    assert (
        wake_data.recover_accepted_batches_from_other_processes(tmp_db, process_token="new", now=5)
        == 0
    )

    second_ticket = tickets_data.create_ticket(
        tmp_db,
        title="Second managed ticket",
        principal=Principal(PrincipalKind.manager, item_id),
        now=6,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        brief="Start",
        manager_id=item_id,
        stated_ceiling="needs_what_changes",
    )
    wake_data.create_worker_error_wake(
        tmp_db,
        manager_id=item_id,
        ticket_id=second_ticket.id,
        claim_revision=1,
        ticket_title=second_ticket.title,
        now=6,
    )
    queued = wake_data.claim_next_batch(tmp_db, process_token="old", now=6)
    assert queued is not None
    wake_data.record_batch_offering(
        tmp_db,
        queued.id,
        conversation_id="conv-queued",
        process_token="old",
        now=6,
    )
    wake_data.record_batch_accepted(
        tmp_db,
        queued.id,
        conversation_id="conv-queued",
        process_token="old",
        now=6,
    )
    assert (
        wake_data.recover_accepted_batches_from_other_processes(tmp_db, process_token="new", now=7)
        == 1
    )
    recovered = wake_data.pending_batches(tmp_db)[0]
    assert recovered.id == queued.id
    assert recovered.sender_message_id == queued.sender_message_id
    assert recovered.conversation_id is None


def test_held_wake_is_marked_dispatching_before_it_can_leave_the_queue(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    wake_data.create_worker_error_wake(
        tmp_db,
        manager_id=item_id,
        ticket_id=ticket_id,
        claim_revision=1,
        ticket_title="Managed ticket",
        now=1,
    )
    batch = wake_data.claim_next_batch(tmp_db, process_token="old", now=2)
    assert batch is not None
    tmp_db.execute(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,access,"
        "created_at) VALUES ('conv-held','hermes','model','/tmp','full',1)"
    )
    wake_data.record_batch_offering(
        tmp_db,
        batch.id,
        conversation_id="conv-held",
        process_token="old",
        now=3,
    )
    wake_data.record_batch_accepted(
        tmp_db,
        batch.id,
        conversation_id="conv-held",
        process_token="old",
        now=4,
    )
    database_path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))
    store = ConversationStore(str(database_path), integer_now=lambda: 5)

    assert (
        asyncio.run(
            store.mark_held_sender_messages_leaving_queue("conv-held", (batch.sender_message_id,))
        )
        == 1
    )
    marked = wake_data.batches_waiting_for_outcome(tmp_db)[0]
    assert marked.status is WakeBatchStatus.dispatching
    assert (
        wake_data.recover_accepted_batches_from_other_processes(tmp_db, process_token="new", now=6)
        == 0
    )
    assert wake_data.preserve_interrupted_dispatches(tmp_db, process_token="new", now=6) == 1
    assert wake_data.batches_waiting_for_outcome(tmp_db)[0].status is WakeBatchStatus.uncertain


def test_dequeue_marker_wins_the_race_with_the_queued_send_receipt(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    wake_data.create_worker_error_wake(
        tmp_db,
        manager_id=item_id,
        ticket_id=ticket_id,
        claim_revision=1,
        ticket_title="Managed ticket",
        now=1,
    )
    batch = wake_data.claim_next_batch(tmp_db, process_token="process", now=2)
    assert batch is not None
    tmp_db.execute(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,access,"
        "created_at) VALUES ('conv-race','hermes','model','/tmp','full',1)"
    )
    wake_data.record_batch_offering(
        tmp_db,
        batch.id,
        conversation_id="conv-race",
        process_token="process",
        now=3,
    )
    database_path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))
    store = ConversationStore(str(database_path), integer_now=lambda: 4)
    assert asyncio.run(
        store.mark_held_sender_messages_leaving_queue(
            "conv-race", (batch.sender_message_id,)
        )
    ) == 1

    wake_data.record_batch_accepted(
        tmp_db,
        batch.id,
        conversation_id="conv-race",
        process_token="process",
        now=5,
    )

    stored = wake_data.batches_waiting_for_outcome(tmp_db)[0]
    assert stored.status is WakeBatchStatus.dispatching


def test_late_uncertain_manager_prevents_restart_replay(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    wake_data.create_worker_error_wake(
        tmp_db,
        manager_id=item_id,
        ticket_id=ticket_id,
        claim_revision=1,
        ticket_title="Managed ticket",
        now=1,
    )
    batch = wake_data.claim_next_batch(tmp_db, process_token="old", now=2)
    assert batch is not None
    tmp_db.execute(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,access,"
        "created_at) VALUES ('conv-uncertain','claude','model','/tmp','full',1)"
    )
    wake_data.record_batch_offering(
        tmp_db,
        batch.id,
        conversation_id="conv-uncertain",
        process_token="old",
        now=3,
    )
    wake_data.record_batch_accepted(
        tmp_db,
        batch.id,
        conversation_id="conv-uncertain",
        process_token="old",
        now=4,
    )
    tmp_db.execute(
        "INSERT INTO conversation_events(conversation_id,sequence,kind,payload,created_at) "
        "VALUES ('conv-uncertain',1,'prompt_delivery_uncertain',?,5)",
        (json.dumps({"sender_message_id": batch.sender_message_id}),),
    )
    assert reconcile_batch_outcomes(tmp_db, now=6) == 1
    assert wake_data.batches_waiting_for_outcome(tmp_db)[0].status is WakeBatchStatus.uncertain
    assert (
        wake_data.recover_accepted_batches_from_other_processes(tmp_db, process_token="new", now=7)
        == 0
    )




@pytest.mark.parametrize("backend_key", tuple(ConversationBackendKey))
def test_delivery_uses_the_supervisor_boundary_and_closes_on_its_prompt(
    tmp_db: Connection,
    fake_clock: FakeClock,
    backend_key: ConversationBackendKey,
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    managers_data.update_manager_launch_configuration(
        tmp_db,
        item_id,
        ManagerLaunchConfiguration(
            employee_backend=backend_key,
            employee_launch_model="a-model",
            employee_launch_reasoning_effort=None,
        ),
        principal=OWNER_PRINCIPAL,
        clock=fake_clock,
    )
    wake_data.create_worker_error_wake(
        tmp_db,
        manager_id=item_id,
        ticket_id=ticket_id,
        claim_revision=1,
        ticket_title="Managed ticket",
        now=1,
    )
    batch = wake_data.claim_next_batch(tmp_db, process_token="process", now=2)
    assert batch is not None
    database_path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))
    system = _DurableStartedConversationSystem(database_path)

    delivered = asyncio.run(
        deliver_batch(
            batch,
            connect_database=lambda: connect(str(database_path)),
            conversation_system=system,  # type: ignore[arg-type]
            process_token="process",
            now=lambda: 50,
        )
    )

    assert delivered is True
    assert system.started is not None
    assert system.started.backend_key is backend_key
    assert all(row["closed_at"] == 50 for row in _wake_rows(tmp_db))


@pytest.mark.parametrize("source", ["kickoff", "file", "readdress"])
def test_chief_review_proposals_reach_the_real_chief_prompt_boundary(
    tmp_db: Connection, fake_clock: FakeClock, source: str
) -> None:
    _item_id, existing_id = _item_and_ticket(tmp_db, fake_clock)
    if source == "kickoff":
        ticket = tickets_data.create_ticket(
            tmp_db, title="Chief kickoff review", principal=OWNER_PRINCIPAL,
            now=10, title_max_chars=TITLE_MAX_CHARS, worker_type="coding",
            brief="Chief must see this canonical proposal", manager_kind="chief",
            stated_holder=CHIEF_PRINCIPAL,
        )
        ticket_id = ticket.id
    else:
        ticket_id = existing_id
        tickets_data.edit_ticket(
            tmp_db, ticket_id,
            edit={
                "manager": CHIEF_PRINCIPAL,
                "ceiling_holder": CHIEF_PRINCIPAL if source == "file" else OWNER_PRINCIPAL,
            },
            title_max_chars=TITLE_MAX_CHARS, principal=OWNER_PRINCIPAL, now=10,
        )
        ticket = tickets_data.file_current_proposal(
            tmp_db, ticket_id, body="Chief must see this canonical proposal",
            principal=Principal(PrincipalKind.ticket, ticket_id), now=11,
        )
        if source == "readdress":
            assert _wake_rows(tmp_db) == []
            ticket = tickets_data.edit_ticket(
                tmp_db, ticket_id, edit={"ceiling_holder": CHIEF_PRINCIPAL},
                title_max_chars=TITLE_MAX_CHARS, principal=OWNER_PRINCIPAL, now=12,
            )
    assert ticket.ceiling_holder == CHIEF_PRINCIPAL
    assert ticket.manager_kind == "chief"
    assert ticket.manager_id is None
    assert ticket.pending_proposal is not None
    initial_stage = ticket.stage
    expected_revision = 2 if source == "readdress" else 1
    rows = _wake_rows(tmp_db)
    assert len(rows) == 1
    assert (rows[0]["target_kind"], rows[0]["target_id"]) == ("chief", "chief")
    assert rows[0]["source_revision"] == expected_revision
    assert rows[0]["source_kind"] == "proposal"
    tickets_data.edit_ticket(
        tmp_db, ticket_id, edit={"ceiling_holder": CHIEF_PRINCIPAL},
        title_max_chars=TITLE_MAX_CHARS, principal=OWNER_PRINCIPAL, now=13,
    )
    assert len(_wake_rows(tmp_db)) == 1
    batch = wake_data.claim_next_batch(tmp_db, process_token="chief-proof", now=20)
    assert batch is not None and batch.target == CHIEF_PRINCIPAL
    database_path = Path(str(tmp_db.execute("PRAGMA database_list").fetchone()[2]))
    system = _DurableStartedConversationSystem(database_path)
    assert asyncio.run(deliver_batch(
        batch, connect_database=lambda: connect(str(database_path)),
        conversation_system=system,  # type: ignore[arg-type]
        process_token="chief-proof", now=lambda: 50,
    ))
    chief_conversation = tmp_db.execute(
        "SELECT conversation_id FROM agents WHERE agent_key='chief_of_staff'"
    ).fetchone()[0]
    prompt = tmp_db.execute(
        "SELECT payload FROM conversation_events WHERE conversation_id=? AND kind='prompt' "
        "AND json_extract(payload,'$.sender_message_id')=?",
        (chief_conversation, batch.sender_message_id),
    ).fetchone()
    assert prompt is not None and ticket_id in prompt["payload"]
    assert "filed a proposal" in prompt["payload"]
    assert all(row["closed_at"] == 50 for row in _wake_rows(tmp_db))
    unchanged = tickets_data.read_ticket(tmp_db, ticket_id)
    assert unchanged.stage == initial_stage
    assert unchanged.pending_proposal == ticket.pending_proposal
    assert unchanged.manager_kind == "chief"
    assert unchanged.manager_id is None
    # A later explicit owner reroute is still an address change, not employee delivery.
    tickets_data.edit_ticket(
        tmp_db, ticket_id, edit={"ceiling_holder": OWNER_PRINCIPAL},
        title_max_chars=TITLE_MAX_CHARS, principal=OWNER_PRINCIPAL, now=60,
    )
    assert len(_wake_rows(tmp_db)) == 1


@pytest.mark.parametrize(
    "change,expected",
    [
        ("accept", "proposal_resolved"),
        ("reject", "proposal_resolved"),
        ("replace", "revision_changed"),
        ("delete", "ticket_missing"),
        ("transfer", "revision_changed"),
        ("reviewer_only", "reviewer_changed"),
    ],
)
def test_obsolete_sources_retire_before_batching_with_compared_facts(
    tmp_db: Connection, fake_clock: FakeClock, change: str, expected: str
) -> None:
    manager_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    tickets_data.file_current_proposal(
        tmp_db,
        ticket_id,
        body="Original",
        principal=Principal(PrincipalKind.ticket, ticket_id),
        now=2,
    )
    if change == "accept":
        tickets_data.accept_proposal(
            tmp_db,
            ticket_id,
            field="what_changes",
            principal=OWNER_PRINCIPAL,
            now=3,
            next_holder=OWNER_PRINCIPAL,
            next_ceiling="needs_implementation",
        )
    elif change in ("reject", "resolve_transfer"):
        tickets_data.reject_proposal(
            tmp_db,
            ticket_id,
            message=None,
            principal=OWNER_PRINCIPAL,
            now=3,
        )
        if change == "resolve_transfer":
            tickets_data.edit_ticket(
                tmp_db,
                ticket_id,
                edit={"manager": CHIEF_PRINCIPAL, "ceiling_holder": CHIEF_PRINCIPAL},
                title_max_chars=TITLE_MAX_CHARS,
                principal=OWNER_PRINCIPAL,
                now=4,
            )
    elif change == "replace":
        tickets_data.file_current_proposal(
            tmp_db,
            ticket_id,
            body="Replacement",
            principal=Principal(PrincipalKind.ticket, ticket_id),
            now=3,
        )
    elif change == "delete":
        tickets_data.delete_ticket(tmp_db, ticket_id, principal=OWNER_PRINCIPAL, now=3)
    elif change == "transfer":
        tickets_data.edit_ticket(
            tmp_db,
            ticket_id,
            edit={"manager": CHIEF_PRINCIPAL, "ceiling_holder": CHIEF_PRINCIPAL},
            title_max_chars=TITLE_MAX_CHARS,
            principal=OWNER_PRINCIPAL,
            now=3,
        )
    else:
        # Isolate the reviewer comparison from canonical transfer's revision bump.
        tmp_db.execute(
            "UPDATE tickets SET ceiling_holder=? WHERE id=?",
            (
                json.dumps({"kind": "chief", "id": "chief"}),
                ticket_id,
            ),
        )
    batch = wake_data.claim_next_batch(tmp_db, process_token="process", now=4)
    audit = dict(tmp_db.execute("SELECT * FROM manager_wake_suppressions").fetchone())
    assert (audit["reason"], audit["source_revision"], audit["target_id"]) == (
        expected,
        1,
        manager_id,
    )
    assert audit["ticket_exists"] == (change != "delete")
    if change in ("transfer", "reviewer_only"):
        assert (audit["current_target_kind"], audit["current_target_id"]) == ("chief", "chief")
    if change in ("replace", "transfer"):
        assert batch is not None
        assert batch.target == (
            CHIEF_PRINCIPAL
            if change == "transfer"
            else Principal(PrincipalKind.manager, manager_id)
        )
        assert audit["current_revision"] == 2
    else:
        assert batch is None


def test_final_admission_discards_exact_mixed_batch_and_regroups_survivors(
    tmp_db: Connection,
    fake_clock: FakeClock,
) -> None:
    manager_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    tickets_data.file_current_proposal(
        tmp_db,
        ticket_id,
        body="Old review",
        principal=Principal(PrincipalKind.ticket, ticket_id),
        now=2,
    )
    wake_data.create_worker_error_wake(
        tmp_db,
        manager_id=manager_id,
        ticket_id=ticket_id,
        claim_revision=1,
        ticket_title="Managed ticket",
        now=2,
    )
    batch = wake_data.claim_next_batch(tmp_db, process_token="process", now=3)
    assert batch is not None
    tmp_db.execute(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,access,"
        "created_at) VALUES ('conv-admit','codex','model','/tmp','full',1)"
    )
    wake_data.record_batch_offering(
        tmp_db,
        batch.id,
        conversation_id="conv-admit",
        process_token="process",
        now=4,
    )
    tickets_data.file_current_proposal(
        tmp_db,
        ticket_id,
        body="Fresh review",
        principal=Principal(PrincipalKind.ticket, ticket_id),
        now=5,
    )
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    store = ConversationStore(path, integer_now=lambda: 6)
    message = PromptDiscardedEventPayload(
        content=text_message_content(batch.message),
        sender_label="Panels",
        sender_message_id=batch.sender_message_id,
        sent_at_unix_milliseconds=4000,
        sender=CHIEF_PRINCIPAL,
        recipient=Principal(PrincipalKind.manager, manager_id),
    )
    ordinary = PromptDiscardedEventPayload(
        content=text_message_content("Keep this ordinary message"),
        sender_label="Panels",
        sender_message_id="manager_delivery_wake_forged",
    )
    with store.claim_runtime():
        turn = asyncio.run(store.reserve_turn("conv-admit"))
    terminal = (
        LifecycleFact(
            LifecycleKind.turn,
            str(turn),
            turn,
            LifecycleOperation.turn,
            LifecycleStatus.unavailable,
        ),
        LifecycleFact(
            LifecycleKind.attempt,
            f"prompt:{turn}",
            turn,
            LifecycleOperation.prompt,
            LifecycleStatus.suppressed,
        ),
    )

    def fail_participation(_conn: Connection, _candidate: LifecycleTransition) -> None:
        raise ValueError("participant failed")

    failing_store = ConversationStore(
        path, integer_now=lambda: 6, transaction_participant=fail_participation
    )
    with pytest.raises(ValueError, match="participant failed"):
        asyncio.run(
            failing_store.admit_sender_messages(
                "conv-admit",
                (message,),
                lifecycle_if_all_suppressed=terminal,
            )
        )
    assert tmp_db.execute("SELECT COUNT(*) FROM manager_wake_suppressions").fetchone()[0] == 0
    assert (
        tmp_db.execute(
            "SELECT status FROM manager_wake_batches WHERE id=?",
            (batch.id,),
        ).fetchone()[0]
        == "offering"
    )
    assert not asyncio.run(store.read_events_after("conv-admit", 0))
    assert all(
        fact.status is LifecycleStatus.intent
        for fact in asyncio.run(store.lifecycle_facts("conv-admit"))
    )
    assert all(row["closed_at"] is None for row in _wake_rows(tmp_db))
    decisions = asyncio.run(
        store.admit_sender_messages(
            "conv-admit",
            (message, ordinary),
            lifecycle_if_all_suppressed=terminal,
        )
    )
    assert all(
        fact.status is LifecycleStatus.intent
        for fact in asyncio.run(store.lifecycle_facts("conv-admit"))
    )
    assert decisions[0].suppression_reason == "revision_changed"
    assert decisions[0].is_manager_notice
    assert not decisions[1].is_manager_notice and decisions[1].suppression_reason is None
    # Repeated admission returns the exact durable result and does not duplicate its receipt.
    replay = asyncio.run(
        store.admit_sender_messages(
            "conv-admit",
            (message,),
            lifecycle_if_all_suppressed=terminal,
        )
    )[0]
    assert set(asyncio.run(store.lifecycle_facts("conv-admit"))) == set(terminal)
    assert replay.suppression_reason == decisions[0].suppression_reason
    events = asyncio.run(store.read_events_after("conv-admit", 0))
    assert len(events) == 1
    assert events[0].payload == PromptDiscardedEventPayload(
        content=message.content,
        sender_label=message.sender_label,
        sender_message_id=message.sender_message_id,
        sent_at_unix_milliseconds=4000,
        sender=message.sender,
        recipient=message.recipient,
        suppression_reason="revision_changed",
    )
    audit = dict(tmp_db.execute("SELECT * FROM manager_wake_suppressions").fetchone())
    assert (audit["sender_message_id"], audit["source_revision"], audit["current_revision"]) == (
        batch.sender_message_id,
        1,
        2,
    )
    old = tmp_db.execute(
        "SELECT message,status FROM manager_wake_batches WHERE id=?", (batch.id,)
    ).fetchone()
    assert tuple(old) == (batch.message, "discarded")
    next_batch = wake_data.claim_next_batch(tmp_db, process_token="restart", now=12)
    assert next_batch is not None and next_batch.sender_message_id != batch.sender_message_id
    assert (
        "explicit worker-error" in next_batch.message and "filed a proposal" in next_batch.message
    )
    members = tmp_db.execute(
        "SELECT w.source_kind,w.source_revision FROM manager_wakes w "
        "JOIN manager_wake_batch_members m ON m.wake_id=w.id WHERE m.batch_id=? ORDER BY w.id",
        (next_batch.id,),
    ).fetchall()
    assert [tuple(row) for row in members] == [("worker_error", 1), ("proposal", 2)]


@pytest.mark.parametrize("historical_status", [None, "dispatching", "uncertain"])
def test_ceiling_change_keeps_review_notice_and_history_stays_unclassified(
    tmp_db: Connection,
    fake_clock: FakeClock,
    historical_status: str | None,
) -> None:
    _manager_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    tickets_data.file_current_proposal(
        tmp_db,
        ticket_id,
        body="Parked review",
        principal=Principal(PrincipalKind.ticket, ticket_id),
        now=2,
    )
    tickets_data.edit_ticket(
        tmp_db,
        ticket_id,
        edit={"ceiling": "needs_brief"},
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=3,
    )
    batch = wake_data.claim_next_batch(tmp_db, process_token="process", now=4)
    assert batch is not None
    if historical_status is not None:
        tmp_db.execute(
            "UPDATE manager_wake_batches SET status=? WHERE id=?", (historical_status, batch.id)
        )
        tickets_data.reject_proposal(
            tmp_db, ticket_id, message=None, principal=OWNER_PRINCIPAL, now=5
        )
        assert wake_data.claim_next_batch(tmp_db, process_token="restart", now=6) is None
        assert _wake_rows(tmp_db)[0]["closed_at"] is None
    assert tmp_db.execute("SELECT COUNT(*) FROM manager_wake_suppressions").fetchone()[0] == 0


@pytest.mark.parametrize("winner", ["resolution", "admission"])
def test_notice_admission_serializes_with_canonical_resolution(
    tmp_db: Connection,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
    winner: str,
) -> None:
    _manager_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    tickets_data.file_current_proposal(
        tmp_db,
        ticket_id,
        body="Review",
        principal=Principal(PrincipalKind.ticket, ticket_id),
        now=2,
    )
    batch = wake_data.claim_next_batch(tmp_db, process_token="process", now=3)
    assert batch is not None
    tmp_db.execute(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,access,"
        "created_at) VALUES ('conv-serial','codex','model','/tmp','full',1)"
    )
    wake_data.record_batch_offering(
        tmp_db,
        batch.id,
        conversation_id="conv-serial",
        process_token="process",
        now=4,
    )
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    store = ConversationStore(path, integer_now=lambda: 5)
    message = PromptDiscardedEventPayload(
        content=text_message_content(batch.message),
        sender_label="Panels",
        sender_message_id=batch.sender_message_id,
    )
    acquired, attempted, release = threading.Event(), threading.Event(), threading.Event()
    original_connect = store._connect
    original_commit = store._commit_rows

    def admission_connect() -> Connection:
        conn = original_connect()
        if winner == "resolution":
            conn.set_trace_callback(
                lambda sql: attempted.set() if sql == "BEGIN IMMEDIATE" else None
            )
        return conn

    def admission_commit(*args: Any, **kwargs: Any) -> None:
        if winner == "admission":
            acquired.set()
            assert release.wait(5), "resolution did not attempt its transaction"
        original_commit(*args, **kwargs)

    monkeypatch.setattr(store, "_connect", admission_connect)
    monkeypatch.setattr(store, "_commit_rows", admission_commit)

    def resolve() -> None:
        conn = connect(path)
        blocked = False

        def trace(sql: str) -> None:
            nonlocal blocked
            if winner == "admission" and sql == "BEGIN IMMEDIATE":
                attempted.set()
            if (
                winner == "resolution"
                and sql.startswith("UPDATE tickets SET field_values")
                and not blocked
            ):
                blocked = True
                acquired.set()
                assert release.wait(5), "admission did not attempt its transaction"

        conn.set_trace_callback(trace)
        try:
            tickets_data.reject_proposal(
                conn,
                ticket_id,
                message=None,
                principal=OWNER_PRINCIPAL,
                now=5,
            )
        finally:
            conn.close()

    def admit() -> tuple[Any, ...]:
        return asyncio.run(store.admit_sender_messages("conv-serial", (message,)))

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(resolve if winner == "resolution" else admit)
        try:
            assert acquired.wait(5), "first transaction did not reach its barrier"
            second = executor.submit(admit if winner == "resolution" else resolve)
            assert attempted.wait(5), "second transaction did not attempt its write lock"
            assert not second.done()
        finally:
            release.set()
        first_result = first.result(timeout=5)
        second_result = second.result(timeout=5)
    decisions = second_result if winner == "resolution" else first_result
    assert decisions is not None
    assert decisions[0].suppression_reason == (
        "proposal_resolved" if winner == "resolution" else None
    )
    assert tickets_data.read_ticket(tmp_db, ticket_id).pending_proposal is None
    assert tmp_db.execute("SELECT COUNT(*) FROM manager_wake_suppressions").fetchone()[0] == (
        1 if winner == "resolution" else 0
    )
    assert tmp_db.execute(
        "SELECT status FROM manager_wake_batches WHERE id=?", (batch.id,)
    ).fetchone()[0] == ("discarded" if winner == "resolution" else "dispatching")
