"""A final uncertain delivery is not a still-running workspace operation."""

import asyncio
from sqlite3 import Connection

from tests.support.in_memory_conversation_system import InMemoryConversationSystem
from tests.support.principals import OWNER_PRINCIPAL

from planner.runtime.conversation_start import start_ticket_conversation, worker_resolve
from planner.ticket_workspaces.data import has_workspace_activity
from planner.tickets.data import create_ticket


def test_unknown_attempt_does_not_hide_real_workspace_activity(tmp_db: Connection) -> None:
    ticket = create_ticket(
        tmp_db,
        title="Workspace with uncertain past delivery",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
    )
    asyncio.run(
        start_ticket_conversation(
            InMemoryConversationSystem(),
            tmp_db,
            ticket,
            worker_resolve(tmp_db, ticket),
            conversation_id="workspace_unknown_attempt",
            now=2,
        )
    )
    tmp_db.execute(
        "INSERT INTO conversation_lifecycle_facts "
        "(conversation_id,kind,identity,turn_number,operation,status) "
        "VALUES('workspace_unknown_attempt','attempt','uncertain',1,'prompt','unknown')"
    )
    assert not has_workspace_activity(tmp_db, ticket.id)

    tmp_db.execute(
        "INSERT INTO conversation_lifecycle_facts "
        "(conversation_id,kind,identity,turn_number,operation,status) "
        "VALUES('workspace_unknown_attempt','turn','active',1,'turn','running')"
    )
    assert has_workspace_activity(tmp_db, ticket.id)
    tmp_db.execute("DELETE FROM conversation_lifecycle_facts WHERE identity='active'")
    tmp_db.execute(
        "UPDATE conversation_lifecycle_facts SET status='intent' WHERE identity='uncertain'"
    )
    assert has_workspace_activity(tmp_db, ticket.id)
