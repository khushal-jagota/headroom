"""Remote admission requires reconciled current evidence at both runtime doors."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from sqlite3 import Connection

import pytest

from planner.conversation.contracts import ConversationBackendKey, ConversationStartRequest
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.message_content import text_message_content
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.errors import PlannerError
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import ExecutorIdentity
from planner.executor.vps_inbox import InboxStore
from planner.runtime.conversation_start import send_to_ticket_conversation
from planner.runtime.worker_step_readiness import is_ready_for_worker_step
from planner.tickets import data, locale_configuration
from planner.tickets.contracts import Ticket, TicketLocale
from planner.worker_types.configuration import configured_worker_type_registry

IDENTITY = ExecutorIdentity(PROTOCOL_VERSION, "a" * 40, SPOOL_FORMAT_VERSION, "incarnation", 1)


class StartObservingSystem(InMemoryConversationSystem):
    def __init__(self) -> None:
        super().__init__()
        self.starts: list[str] = []
        self.start_backends: list[ConversationBackendKey | None] = []

    async def start_conversation(self, request: ConversationStartRequest) -> None:
        self.starts.append(request.conversation_id)
        self.start_backends.append(request.backend_key)
        await super().start_conversation(request)


def mac_ticket(connection: Connection, backend: str = "codex") -> Ticket:
    ticket = data.create_ticket(
        connection,
        title="Mac worker",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        locale=TicketLocale.macos,
        employee_backend=backend,
        employee_launch_model="scripted-model",
        brief="Brief.",
    )
    return data.accept_proposal(
        connection,
        ticket.id,
        field="brief",
        principal=OWNER_PRINCIPAL,
        now=2,
        next_ceiling="needs_what_changes",
        next_holder=OWNER_PRINCIPAL,
    )


def executor_store(connection: Connection) -> InboxStore:
    return InboxStore(str(connection.execute("PRAGMA database_list").fetchone()[2]))


@pytest.mark.parametrize(
    ("state", "admitted"),
    [
        ("absent", False),
        ("reconciling", False),
        ("ready", True),
        ("lost", False),
        ("replacement_reconciling", False),
        ("replacement_ready", True),
        ("old_socket_lost", True),
        ("inactive_ready_history", False),
    ],
)
def test_both_runtime_guards_use_current_reconciled_connection(
    tmp_db: Connection,
    state: str,
    admitted: bool,
) -> None:
    ticket = mac_ticket(tmp_db)
    store = executor_store(tmp_db)
    if state != "absent":
        store.runs.begin_connection(IDENTITY)
    if state not in ("absent", "reconciling"):
        store.runs.mark_ready(IDENTITY)
    if state == "lost":
        store.runs.mark_lost(IDENTITY)
    if state.startswith("replacement") or state == "old_socket_lost":
        replacement = replace(IDENTITY, connection_generation=2)
        store.runs.begin_connection(replacement)
        if state != "replacement_reconciling":
            store.runs.mark_ready(replacement)
        if state == "old_socket_lost":
            store.runs.mark_lost(IDENTITY)
    if state == "inactive_ready_history":
        # A superseded process's persisted ready row is historical evidence.
        tmp_db.execute("UPDATE executor_connections SET active=0")
    definition = configured_worker_type_registry().require("coding")
    assert locale_configuration.can_execute_locale(tmp_db, TicketLocale.macos) is admitted
    assert is_ready_for_worker_step(tmp_db, ticket, worker_type_definition=definition) is admitted
    system = StartObservingSystem()

    async def send() -> None:
        await send_to_ticket_conversation(
            system,
            tmp_db,
            ticket.id,
            text_message_content("Begin work"),
            conversation_id=None,
            created_conversation_id="c_remote",
            sender_label="Test",
            now=3,
        )

    if admitted:
        asyncio.run(send())
        assert system.starts == ["c_remote"]
    else:
        before = data.read_ticket(tmp_db, ticket.id)
        with pytest.raises(PlannerError, match="cannot start"):
            asyncio.run(send())
        assert system.starts == []
        assert data.read_ticket(tmp_db, ticket.id).conversation_id == before.conversation_id
        assert (
            tmp_db.execute(
                "SELECT 1 FROM conversations WHERE conversation_id='c_remote'"
            ).fetchone()
            is None
        )


def test_local_locale_does_not_depend_on_remote_connection(tmp_db: Connection) -> None:
    assert locale_configuration.can_execute_locale(tmp_db, TicketLocale.vps)
    store = executor_store(tmp_db)
    store.runs.begin_connection(IDENTITY)
    store.runs.mark_lost(IDENTITY)
    assert locale_configuration.can_execute_locale(tmp_db, TicketLocale.vps)
