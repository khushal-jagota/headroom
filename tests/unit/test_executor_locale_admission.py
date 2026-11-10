"""Mac admission needs a known compatible process; readiness also needs it present.

Admission (creation and sends) reads the presence row: a compatible incarnation that is
known and not refused. A Mac that is away still admits sends, which wait in the mailbox,
while worker-step readiness answers "Waiting for the Mac" until a fresh lease.
"""

from __future__ import annotations

import asyncio
from sqlite3 import Connection

import pytest
from tests.support.executor_presence import mac_identity, make_current
from tests.support.in_memory_conversation_system import InMemoryConversationSystem

from planner.conversation.contracts import ConversationBackendKey, ConversationStartRequest
from planner.conversation.message_content import text_message_content
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect
from planner.core.errors import PlannerError
from planner.execution_configuration import PROTOCOL_VERSION, SPOOL_FORMAT_VERSION
from planner.execution_identity import MAC_NEEDS_UPDATE, ExecutorIdentity
from planner.executor.protocol import ExecutorRefusal
from planner.executor.vps_presence_store import record_refusal
from planner.runtime.conversation_start import send_to_ticket_conversation
from planner.runtime.worker_step_readiness import is_ready_for_worker_step, worker_step_blocker
from planner.tickets import data, locale_configuration
from planner.tickets.contracts import Ticket, TicketLocale
from planner.worker_types.configuration import configured_worker_type_registry

IDENTITY = mac_identity()


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


def database_path(connection: Connection) -> str:
    return str(connection.execute("PRAGMA database_list").fetchone()[2])


def refuse_old_mac(db_path: str) -> None:
    """An old Mac's WebSocket, refused and recorded as route 10 does."""
    server = ExecutorIdentity(PROTOCOL_VERSION, "b" * 40, SPOOL_FORMAT_VERSION, "", 0)
    connection = connect(db_path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        record_refusal(connection, ExecutorRefusal(MAC_NEEDS_UPDATE, None, server, 2))
        connection.commit()
    finally:
        connection.close()


@pytest.mark.parametrize(
    ("state", "admitted", "blocker"),
    [
        ("absent", False, "no compatible Mac executor has leased yet"),
        ("present", True, None),
        ("away", True, locale_configuration.WAITING_FOR_THE_MAC),
        ("replacement_present", True, None),
        ("refused", False, MAC_NEEDS_UPDATE),
        ("refused_then_leased", True, None),
    ],
)
def test_both_runtime_guards_use_current_presence(
    tmp_db: Connection,
    state: str,
    admitted: bool,
    blocker: str | None,
) -> None:
    ticket = mac_ticket(tmp_db)
    tmp_db.commit()
    db_path = database_path(tmp_db)
    if state == "away":
        make_current(db_path, IDENTITY, at_ms=1)
    if state in ("present", "replacement_present"):
        make_current(db_path, IDENTITY)
    if state == "replacement_present":
        make_current(db_path, mac_identity())
    if state.startswith("refused"):
        refuse_old_mac(db_path)
    if state == "refused_then_leased":
        # A lease of the new runtime clears the old runtime's refusal.
        make_current(db_path, IDENTITY)
    definition = configured_worker_type_registry().require("coding")
    assert locale_configuration.can_execute_locale(tmp_db, TicketLocale.macos) is admitted
    reason = worker_step_blocker(tmp_db, ticket, worker_type_definition=definition)
    if blocker is None:
        assert reason is None
    else:
        assert reason is not None and blocker in reason
    assert is_ready_for_worker_step(tmp_db, ticket, worker_type_definition=definition) is (
        blocker is None
    )
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
        # Away included: the send is stored and waits in the mailbox for the Mac.
        asyncio.run(send())
        assert system.starts == ["c_remote"]
    else:
        before = data.read_ticket(tmp_db, ticket.id)
        assert blocker is not None
        with pytest.raises(PlannerError, match=blocker):
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
    tmp_db.commit()
    refuse_old_mac(database_path(tmp_db))
    assert locale_configuration.can_execute_locale(tmp_db, TicketLocale.vps)
