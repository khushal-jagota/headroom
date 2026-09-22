"""Durable source and delivery rules for Sprint Item manager wakes."""

from __future__ import annotations

import asyncio
import json
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
from planner.conversation.events import PromptEventPayload
from planner.conversation.logic.conversation_start_resolution import (
    resolve_conversation_start_request,
)
from planner.conversation.message_content import MessageContent
from planner.conversation.storage import ConversationStore
from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
from planner.manager_wakes import data as wake_data
from planner.manager_wakes.contracts import WakeBatchStatus, WakeSourceKind
from planner.manager_wakes.runtime import deliver_batch, reconcile_batch_outcomes
from planner.sprints import data as sprints_data
from planner.sprints.contracts import SprintItemSupervisorLaunchConfiguration
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS


def _item_and_ticket(conn: Connection, clock: FakeClock) -> tuple[str, str]:
    item = sprints_data.create_item(
        conn, title="Managed outcome", project_id="project_vylo", clock=clock
    )
    ticket = tickets_data.create_ticket(
        conn,
        title="Managed ticket",
        principal=Principal(PrincipalKind.sprint_item, item.id),
        now=clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Start",
        sprint_item_id=item.id,
        stated_ceiling="needs_success_condition",
    )
    return item.id, ticket.id


def _wake_rows(conn: Connection) -> list[dict[str, object]]:
    return [
        dict(row) for row in conn.execute("SELECT * FROM manager_wakes ORDER BY id")
    ]


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
    assert [(row["sprint_item_id"], row["source_revision"]) for row in rows] == [
        (item_id, 1),
        (item_id, 2),
    ]
    assert {row["source_kind"] for row in rows} == {"proposal"}


def test_owner_routed_proposal_does_not_create_a_manager_wake(
    tmp_db: Connection,
) -> None:
    ticket = tickets_data.create_ticket(
        tmp_db,
        title="Owner ticket",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Start",
        stated_ceiling="needs_success_condition",
    )
    tickets_data.file_current_proposal(
        tmp_db,
        ticket.id,
        body="Owner proposal",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=2,
    )
    assert _wake_rows(tmp_db) == []


def test_manager_routed_kickoff_proposal_creates_its_wake_with_ticket(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item = sprints_data.create_item(
        tmp_db, title="Managed outcome", project_id="project_vylo", clock=fake_clock
    )
    ticket = tickets_data.create_ticket(
        tmp_db,
        title="Kickoff waits here",
        principal=Principal(PrincipalKind.sprint_item, item.id),
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Review this kickoff",
        sprint_item_id=item.id,
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
    assert rows[0]["sprint_item_id"] == item_id
    assert rows[0]["source_kind"] == "worker_error"
    assert rows[0]["source_revision"] == 1


def test_batches_group_wakes_and_close_only_for_the_exact_prompt(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    for revision, kind in enumerate(WakeSourceKind, start=1):
        wake_data.create_wake(
            tmp_db,
            sprint_item_id=item_id,
            ticket_id=ticket_id,
            source_kind=kind,
            source_revision=revision,
            summary=f"Event {revision}",
            now=10 + revision,
        )
    batch = wake_data.claim_next_batch(tmp_db, process_token="process-a", now=20)
    assert batch is not None
    assert "Event 1" in batch.message and "Event 2" in batch.message
    assert batch.sender_message_id.startswith("supervisor_delivery_")

    tmp_db.execute(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,access,"
        "created_at) VALUES ('conv-manager','codex','model','/tmp','full',1)"
    )
    wake_data.record_batch_dispatching(
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


def test_definite_failure_waits_then_creates_a_new_attempt_id(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    wake_data.create_worker_error_wake(
        tmp_db,
        sprint_item_id=item_id,
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


def test_restart_requeues_accepted_but_preserves_interrupted_dispatch_as_uncertain(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    wake_data.create_worker_error_wake(
        tmp_db,
        sprint_item_id=item_id,
        ticket_id=ticket_id,
        claim_revision=1,
        ticket_title="Managed ticket",
        now=1,
    )
    batch = wake_data.claim_next_batch(tmp_db, process_token="old", now=2)
    assert batch is not None
    wake_data.record_batch_dispatching(
        tmp_db,
        batch.id,
        conversation_id="conv-intended",
        process_token="old",
        now=3,
    )
    assert (
        wake_data.preserve_interrupted_dispatches(tmp_db, process_token="new", now=4)
        == 1
    )
    stored = wake_data.batches_waiting_for_outcome(tmp_db)[0]
    assert stored.status is WakeBatchStatus.uncertain
    assert (
        wake_data.recover_accepted_batches_from_other_processes(
            tmp_db, process_token="new", now=5
        )
        == 0
    )

    second_ticket = tickets_data.create_ticket(
        tmp_db,
        title="Second managed ticket",
        principal=Principal(PrincipalKind.sprint_item, item_id),
        now=6,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Start",
        sprint_item_id=item_id,
        stated_ceiling="needs_success_condition",
    )
    wake_data.create_worker_error_wake(
        tmp_db,
        sprint_item_id=item_id,
        ticket_id=second_ticket.id,
        claim_revision=1,
        ticket_title=second_ticket.title,
        now=6,
    )
    queued = wake_data.claim_next_batch(tmp_db, process_token="old", now=6)
    assert queued is not None
    wake_data.record_batch_dispatching(
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
        wake_data.recover_accepted_batches_from_other_processes(
            tmp_db, process_token="new", now=7
        )
        == 1
    )
    assert wake_data.pending_batches(tmp_db)[0].sender_message_id == queued.sender_message_id


def test_late_uncertain_outcome_prevents_restart_replay(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    wake_data.create_worker_error_wake(
        tmp_db,
        sprint_item_id=item_id,
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
    wake_data.record_batch_dispatching(
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
        wake_data.recover_accepted_batches_from_other_processes(
            tmp_db, process_token="new", now=7
        )
        == 0
    )


def test_open_wake_survives_ticket_deletion(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    wake_data.create_worker_error_wake(
        tmp_db,
        sprint_item_id=item_id,
        ticket_id=ticket_id,
        claim_revision=1,
        ticket_title="Managed ticket",
        now=1,
    )
    tmp_db.execute("DELETE FROM tickets WHERE id=?", (ticket_id,))
    row = tmp_db.execute("SELECT ticket_id,closed_at FROM manager_wakes").fetchone()
    assert row is not None
    assert row["ticket_id"] is None
    assert row["closed_at"] is None


@pytest.mark.parametrize("backend_key", tuple(ConversationBackendKey))
def test_delivery_uses_the_supervisor_boundary_and_closes_on_its_prompt(
    tmp_db: Connection,
    fake_clock: FakeClock,
    backend_key: ConversationBackendKey,
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    sprints_data.update_supervisor_launch_configuration(
        tmp_db,
        item_id,
        SprintItemSupervisorLaunchConfiguration(
            employee_backend=backend_key,
            employee_launch_model="a-model",
            employee_launch_reasoning_effort=None,
        ),
        principal=OWNER_PRINCIPAL,
        clock=fake_clock,
    )
    wake_data.create_worker_error_wake(
        tmp_db,
        sprint_item_id=item_id,
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
