from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection

import pytest

from planner.conversation.contracts import (
    ConversationAccess,
    ConversationBackendKey,
    ConversationRoleMaterials,
    ConversationStartRequest,
    PromptDeliveryMode,
    ResolvedConversationStart,
)
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.message_content import MessageContent, text_message_content
from planner.conversation.storage import ensure_started_conversation_record
from planner.core.errors import ErrorCode, PlannerError
from planner.runtime.conversation_start import send_to_ticket_conversation
from planner.tickets import data as tickets_data
from planner.tickets import views as ticket_views
from planner.tickets.contracts import Ticket


@pytest.fixture
def ticket(tmp_db: Connection) -> Ticket:
    return tickets_data.create_ticket(
        tmp_db,
        title="History",
        worker_type="coding",
        actor="human",
        now=1,
        title_max_chars=200,
    )


def _conversation(conn: Connection, conversation_id: str, created_at: int) -> None:
    conn.execute(
        "INSERT INTO conversations (conversation_id, backend_key, model, workspace_folder, "
        "access, created_at) VALUES (?, 'codex', 'model', '/work', 'full', ?)",
        (conversation_id, created_at),
    )


def _resolved(conversation_id: str) -> ResolvedConversationStart:
    return ResolvedConversationStart(
        conversation_id=conversation_id,
        backend_key=ConversationBackendKey.claude,
        model="opus",
        reasoning_effort="high",
        role_materials=ConversationRoleMaterials(
            role_text="Ticket worker",
            identity_environment_variables=(("PLAN_ACTOR", "worker"), ("PLAN_TICKET_ID", "t")),
        ),
        workspace_folder=Path("/worktree"),
        access=ConversationAccess.full,
    )


def test_durable_record_fallback_inserts_every_started_value(tmp_db: Connection) -> None:
    ensure_started_conversation_record(tmp_db, _resolved("conv_inserted"), created_at=10)

    row = tmp_db.execute(
        "SELECT backend_key, model, reasoning_effort, workspace_folder, role_text, "
        "identity_environment_variables, access, created_at FROM conversations "
        "WHERE conversation_id = 'conv_inserted'"
    ).fetchone()
    assert row is not None
    assert tuple(row) == (
        "claude",
        "opus",
        "high",
        "/worktree",
        "Ticket worker",
        '[["PLAN_ACTOR","worker"],["PLAN_TICKET_ID","t"]]',
        "full",
        10,
    )


def test_durable_record_fallback_accepts_matching_immutable_start_values(
    tmp_db: Connection,
) -> None:
    tmp_db.execute(
        "INSERT INTO conversations (conversation_id, backend_key, model, reasoning_effort, "
        "workspace_folder, role_text, identity_environment_variables, access, "
        "vendor_session_cursor, latest_sequence, composer_catalog, created_at) "
        "VALUES ('conv_existing', 'claude', 'new-model', 'low', '/worktree', "
        "'Ticket worker', '[[\"PLAN_ACTOR\",\"worker\"],[\"PLAN_TICKET_ID\",\"t\"]]', "
        "'full', 'session-now', 7, '[{\"kind\":\"command\","
        "\"display_text\":\"/review\",\"insertion_text\":\"/review \","
        "\"description\":\"Review\",\"argument_hint\":null}]', 1)"
    )

    ensure_started_conversation_record(tmp_db, _resolved("conv_existing"), created_at=99)

    row = tmp_db.execute(
        "SELECT model, reasoning_effort, vendor_session_cursor, latest_sequence, created_at "
        "FROM conversations WHERE conversation_id = 'conv_existing'"
    ).fetchone()
    assert row is not None
    assert tuple(row) == ("new-model", "low", "session-now", 7, 1)


def test_detail_orders_history_and_owner_lookup_resolves_past_conversations(
    tmp_db: Connection, ticket: Ticket
) -> None:
    for conversation_id, created_at in (
        ("conv_z", 20),
        ("conv_b", 10),
        ("conv_a", 10),
    ):
        _conversation(tmp_db, conversation_id, created_at)
        tmp_db.execute(
            "INSERT INTO ticket_conversations (conversation_id, ticket_id) VALUES (?, ?)",
            (conversation_id, ticket.id),
        )
    tmp_db.execute(
        "UPDATE tickets SET conversation_id = 'conv_z' WHERE id = ?", (ticket.id,)
    )

    detail = ticket_views.ticket_detail(tmp_db, ticket.id, 30)

    assert detail["conversation_id"] == "conv_z"
    assert detail["conversation_history"] == [
        {"conversation_id": "conv_a", "created_at": 10},
        {"conversation_id": "conv_b", "created_at": 10},
        {"conversation_id": "conv_z", "created_at": 20},
    ]
    assert tickets_data.read_ticket_by_conversation_id(tmp_db, "conv_a").id == ticket.id


def test_ticket_deletion_cascades_only_its_history_associations(
    tmp_db: Connection, ticket: Ticket
) -> None:
    _conversation(tmp_db, "conv_kept_record", 10)
    tmp_db.execute(
        "INSERT INTO ticket_conversations (conversation_id, ticket_id) VALUES (?, ?)",
        ("conv_kept_record", ticket.id),
    )

    tickets_data.delete_ticket(tmp_db, ticket.id, actor="human", now=20)

    assert tmp_db.execute("SELECT * FROM ticket_conversations").fetchone() is None
    assert (
        tmp_db.execute(
            "SELECT 1 FROM conversations WHERE conversation_id = 'conv_kept_record'"
        ).fetchone()
        is not None
    )


class _FirstSendRaises:
    def __init__(self, system: InMemoryConversationSystem) -> None:
        self.system = system

    async def start_conversation(self, request: ConversationStartRequest) -> None:
        await self.system.start_conversation(request)

    async def send(
        self,
        conversation_id: str,
        content: MessageContent,
        *,
        sender_label: str,
        mode: PromptDeliveryMode = PromptDeliveryMode.queue,
        model_change: str | None = None,
        reasoning_effort_change: str | None = None,
        sender_message_id: str | None = None,
        sent_at_unix_milliseconds: int | None = None,
    ) -> object:
        raise RuntimeError("first send failed")

    async def kill(self, conversation_id: str) -> None:
        await self.system.kill(conversation_id)


def test_first_send_exception_removes_the_exact_provisional_association(
    tmp_db: Connection, ticket: Ticket
) -> None:
    async def exercise() -> None:
        system = InMemoryConversationSystem()
        with pytest.raises(RuntimeError, match="first send failed"):
            await send_to_ticket_conversation(
                _FirstSendRaises(system),  # type: ignore[arg-type]
                tmp_db,
                ticket.id,
                text_message_content("first words"),
                conversation_id=None,
                created_conversation_id="conv_exception",
                sender_label="owner",
                now=20,
            )

        assert tickets_data.read_ticket(tmp_db, ticket.id).conversation_id is None
        assert tmp_db.execute("SELECT * FROM ticket_conversations").fetchone() is None
        with pytest.raises(PlannerError) as raised:
            tickets_data.read_ticket_by_conversation_id(tmp_db, "conv_exception")
        assert raised.value.code is ErrorCode.not_found

    asyncio.run(exercise())
