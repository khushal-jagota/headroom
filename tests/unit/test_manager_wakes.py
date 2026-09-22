"""Durable source and delivery rules for Sprint Item manager wakes."""

from __future__ import annotations

import asyncio
import json
import threading
import time
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
from planner.core import change_signal
from planner.core.clock import TestClock as FakeClock
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
from planner.core.errors import ErrorCode, PlannerError
from planner.manager_wakes import data as wake_data
from planner.manager_wakes.contracts import WakeBatchStatus, WakeSourceKind
from planner.manager_wakes.runtime import (
    ManagerWakeLoop,
    deliver_batch,
    reconcile_batch_outcomes,
)
from planner.sprints import data as sprints_data
from planner.sprints.contracts import SprintItemSupervisorLaunchConfiguration
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS
from planner.tickets.logic import proposal_routing


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
    return [dict(row) for row in conn.execute("SELECT * FROM manager_wakes ORDER BY id")]


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


def test_selected_auto_accept_route_controls_persistence(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    ticket = tickets_data.create_ticket(
        tmp_db,
        title="Classifier route",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Start",
        stated_ceiling="needs_success_condition",
    )
    monkeypatch.setattr(
        proposal_routing,
        "route_proposal",
        lambda *_args, **_kwargs: proposal_routing.ProposalRoute(
            proposal_routing.ProposalRouteKind.auto_accept
        ),
    )
    updated = tickets_data.file_current_proposal(
        tmp_db,
        ticket.id,
        body="Settle this",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=2,
    )
    assert updated.pending_proposal is None
    assert updated.field_values["success_condition"] == "Settle this"
    assert _wake_rows(tmp_db) == []


def test_selected_owner_route_readdresses_an_item_held_proposal_before_persistence(
    tmp_db: Connection,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    monkeypatch.setattr(
        proposal_routing,
        "route_proposal",
        lambda *_args, **_kwargs: proposal_routing.ProposalRoute(
            proposal_routing.ProposalRouteKind.owner
        ),
    )
    updated = tickets_data.file_current_proposal(
        tmp_db,
        ticket_id,
        body="Owner reviews this",
        principal=Principal(PrincipalKind.ticket, ticket_id),
        now=2,
    )
    assert updated.pending_proposal is not None
    assert updated.ceiling_holder == OWNER_PRINCIPAL
    assert _wake_rows(tmp_db) == []


def test_selected_item_manager_route_readdresses_before_persistence(
    tmp_db: Connection,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = sprints_data.create_item(
        tmp_db, title="Selected manager", project_id="project_vylo", clock=fake_clock
    )
    ticket = tickets_data.create_ticket(
        tmp_db,
        title="Owner ticket",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Start",
        sprint_item_id=item.id,
        stated_ceiling="needs_success_condition",
    )
    monkeypatch.setattr(
        proposal_routing,
        "route_proposal",
        lambda **_kwargs: proposal_routing.ProposalRoute(
            proposal_routing.ProposalRouteKind.item_manager, item.id
        ),
    )
    updated = tickets_data.file_current_proposal(
        tmp_db,
        ticket.id,
        body="Manager reviews this",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=2,
    )
    assert updated.pending_proposal is not None
    assert updated.ceiling_holder == Principal(PrincipalKind.sprint_item, item.id)
    assert [(row["sprint_item_id"], row["source_revision"]) for row in _wake_rows(tmp_db)] == [
        (item.id, 1)
    ]


def test_readdressing_a_parked_proposal_to_the_item_manager_creates_one_new_wake(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    manager = Principal(PrincipalKind.sprint_item, item_id)
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
    assert [(row["sprint_item_id"], row["source_revision"]) for row in _wake_rows(tmp_db)] == [
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


def test_readdressing_to_a_non_manager_item_does_not_create_a_wake(
    tmp_db: Connection,
) -> None:
    tmp_db.execute(
        "INSERT INTO sprint_items(id,title,project_id,kind,created_at,updated_at) "
        "VALUES ('si_other','Not managed','project_vylo','other',1,1)"
    )
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
        body="Parked",
        principal=Principal(PrincipalKind.ticket, ticket.id),
        now=2,
    )
    tickets_data.edit_ticket(
        tmp_db,
        ticket.id,
        edit={"ceiling_holder": Principal(PrincipalKind.sprint_item, "si_other")},
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=3,
    )
    assert _wake_rows(tmp_db) == []


def test_moving_a_parked_proposal_wakes_the_new_manager_and_removes_old_authority(
    tmp_db: Connection, fake_clock: FakeClock
) -> None:
    old_item_id, ticket_id = _item_and_ticket(tmp_db, fake_clock)
    old_manager = Principal(PrincipalKind.sprint_item, old_item_id)
    tickets_data.file_current_proposal(
        tmp_db,
        ticket_id,
        body="Move this review",
        principal=Principal(PrincipalKind.ticket, ticket_id),
        now=2,
    )
    new_item = sprints_data.create_item(
        tmp_db, title="New managed outcome", project_id="project_vylo", clock=fake_clock
    )
    new_manager = Principal(PrincipalKind.sprint_item, new_item.id)
    tickets_data.edit_ticket(
        tmp_db,
        ticket_id,
        edit={"sprint_item_id": new_item.id, "ceiling_holder": new_manager},
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=3,
    )
    rows = _wake_rows(tmp_db)
    assert [(row["sprint_item_id"], row["source_revision"]) for row in rows] == [
        (old_item_id, 1),
        (new_item.id, 2),
    ]
    with pytest.raises(PlannerError) as forbidden:
        tickets_data.accept_proposal(
            tmp_db,
            ticket_id,
            field="success_condition",
            principal=old_manager,
            now=4,
            next_ceiling="needs_what_changes",
            next_holder=old_manager,
        )
    assert forbidden.value.code is ErrorCode.agent_forbidden


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


def test_selected_kickoff_route_controls_the_created_ticket_destination(
    tmp_db: Connection,
    fake_clock: FakeClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = sprints_data.create_item(
        tmp_db, title="Managed outcome", project_id="project_vylo", clock=fake_clock
    )
    monkeypatch.setattr(
        proposal_routing,
        "route_proposal",
        lambda **_kwargs: proposal_routing.ProposalRoute(proposal_routing.ProposalRouteKind.owner),
    )
    ticket = tickets_data.create_ticket(
        tmp_db,
        title="Kickoff routed to owner",
        principal=Principal(PrincipalKind.sprint_item, item.id),
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        worker_type="coding",
        kickoff_note="Review this kickoff",
        sprint_item_id=item.id,
        stated_ceiling="needs_brief",
    )
    assert ticket.pending_proposal is not None
    assert ticket.ceiling_holder == OWNER_PRINCIPAL
    assert _wake_rows(tmp_db) == []


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
        sprint_item_id=item_id,
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
        sprint_item_id=item_id,
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
    wake_data.create_proposal_wake(
        tmp_db,
        sprint_item_id=item_id,
        ticket_id=ticket_id,
        proposal_revision=1,
        ticket_title="Managed ticket",
        proposal_field="success_condition",
        now=53,
    )
    combined = wake_data.claim_next_batch(tmp_db, process_token="process", now=54)
    assert combined is not None
    assert "explicit worker-error" in combined.message
    assert "filed a proposal" in combined.message


def test_wake_loop_active_deferral_does_not_signal_itself_after_release(
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
    agent_key = tmp_db.execute(
        "SELECT supervisor_agent_key FROM sprint_items WHERE id=?", (item_id,)
    ).fetchone()[0]
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
    emissions = 0

    def signal_received() -> None:
        nonlocal emissions
        emissions += 1
        wake_loop.wake()

    unsubscribe = change_signal.subscribe(signal_received)
    try:
        assert len(wake_loop.poll_once()) == 1
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if (
                tmp_db.execute("SELECT count(*) FROM manager_wake_batches").fetchone()[0]
                == 0
            ):
                break
            time.sleep(0.01)
        assert emissions == 1  # The claim signals. The internal release stays quiet.
        wake_loop._wake.clear()
        time.sleep(0.05)
        assert not wake_loop._wake.is_set()
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


def test_restart_preserves_dispatching_as_uncertain_and_recovers_known_held_batch(
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
        sprint_item_id=item_id,
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


def test_open_wake_survives_ticket_deletion(tmp_db: Connection, fake_clock: FakeClock) -> None:
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
