from __future__ import annotations

from sqlite3 import Connection

import pytest
from tests.support.principals import OWNER_PRINCIPAL

from planner.tickets import data as tickets_data
from planner.tickets.contracts import Ticket


@pytest.fixture
def ticket(tmp_db: Connection) -> Ticket:
    return tickets_data.create_ticket(
        tmp_db,
        title="History",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )


def _conversation(conn: Connection, conversation_id: str, created_at: int) -> None:
    conn.execute(
        "INSERT INTO conversations (conversation_id, backend_key, model, workspace_folder, "
        "access, created_at) VALUES (?, 'codex', 'model', '/work', 'full', ?)",
        (conversation_id, created_at),
    )
