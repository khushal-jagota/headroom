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
from planner.executor.store import ExecutorStore
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
        next_ceiling="needs_success_condition",
        next_holder=OWNER_PRINCIPAL,
    )


def executor_store(connection: Connection) -> ExecutorStore:
    return ExecutorStore(str(connection.execute("PRAGMA database_list").fetchone()[2]))


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
        store.begin_connection(IDENTITY, 100)
    if state not in ("absent", "reconciling"):
        store.mark_ready(IDENTITY, 101)
    if state == "lost":
        store.mark_lost(IDENTITY, 102)
    if state.startswith("replacement") or state == "old_socket_lost":
        replacement = replace(IDENTITY, connection_generation=2)
        store.begin_connection(replacement, 102)
        if state != "replacement_reconciling":
            store.mark_ready(replacement, 103)
        if state == "old_socket_lost":
            store.mark_lost(IDENTITY, 104)
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
    store.begin_connection(IDENTITY, 100)
    store.mark_lost(IDENTITY, 101)
    assert locale_configuration.can_execute_locale(tmp_db, TicketLocale.vps)


@pytest.mark.parametrize("backend", list(ConversationBackendKey))
def test_native_provider_creation_is_allowed_offline(
    tmp_db: Connection,
    backend: ConversationBackendKey,
) -> None:
    ticket = mac_ticket(tmp_db, backend.value)
    assert ticket.employee_backend == backend.value
    assert not locale_configuration.can_execute_locale(tmp_db, TicketLocale.macos)


def test_unknown_native_provider_is_invalid(tmp_db: Connection) -> None:
    before = tuple(tmp_db.iterdump())
    with pytest.raises(PlannerError):
        mac_ticket(tmp_db, "unknown-provider")
    assert tuple(tmp_db.iterdump()) == before


@pytest.mark.parametrize("backend", list(ConversationBackendKey))
def test_mac_backend_picker_resolves_each_implemented_provider(
    tmp_db: Connection,
    backend: ConversationBackendKey,
) -> None:
    from planner.conversation.backend_state import write_model_enablement
    from planner.runtime.logic.conversation_start_resolution import ConversationStartOverrides

    ticket = mac_ticket(tmp_db)
    store = executor_store(tmp_db)
    store.begin_connection(IDENTITY, 100)
    store.mark_ready(IDENTITY, 101)
    write_model_enablement(tmp_db, backend, "scripted-model", True)
    system = StartObservingSystem()

    async def send() -> None:
        await send_to_ticket_conversation(
            system,
            tmp_db,
            ticket.id,
            text_message_content("Begin work"),
            conversation_id=None,
            created_conversation_id="c_native",
            runs_under=ConversationStartOverrides(
                backend_key=backend,
                model="scripted-model",
            ),
            sender_label="Test",
            now=3,
        )

    asyncio.run(send())
    assert system.starts == ["c_native"]
    assert system.start_backends == [backend]
    configured = data.read_ticket(tmp_db, ticket.id)
    assert configured.conversation_id == "c_native"
    # This start-observing fake does not prove contextual sender-intent departure.
    # Hermes retains its preexisting configuration path outside this integration.
    assert configured.employee_backend == (
        backend.value if backend is ConversationBackendKey.hermes else ticket.employee_backend
    )
