"""Whether a Ticket wakes its Sprint Item supervisor: the default, and who can say.

The wake's own reading of this switch lives in
``tests/unit/test_sprint_item_supervisor_wake.py``. This file is about the fact itself:
a Ticket starts unwatched, creation is where somebody says otherwise, and both the user
and the supervisor can change it afterwards on a Ticket that is already running.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core import db as db_module
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

PREVIOUS_REVISION = "one_approval_gate"

_INSERT_TICKET = (
    "INSERT INTO tickets (id,title,worker_type,employee_backend,stage,priority,"
    "recap,ceiling,at_cap,ticket_status,stage_ownership_overrides,"
    "default_stage_ownership_mode,conversation_id,fields,created_at,updated_at,"
    "ticket_status_changed_at,ticket_status_revision) VALUES "
    "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _app(tmp_path: Path) -> FastAPI:
    db_path = tmp_path / "data" / "planning.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    config = load_config(
        path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)}
    )
    return create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )


def _create_item(client: TestClient) -> str:
    response = client.post(
        "/api/items", json={"title": "Supervised", "project_id": "project_vylo"}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def _create_ticket(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post(
        "/api/tickets",
        json={"worker_type": "coding", "title": "A Ticket", **body},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def test_the_migration_leaves_every_existing_ticket_unwatched(tmp_path: Path) -> None:
    """Nothing is opted in by the change itself. There is no backfill to get wrong."""
    db_path = tmp_path / "wakes-supervisor.db"
    engine = db_module._migration_engine(str(db_path), 5000)
    with engine.connect() as connection:
        with connection.begin():
            command.upgrade(db_module._alembic_config(connection), PREVIOUS_REVISION)
    engine.dispose()

    conn = connect(str(db_path))
    conn.execute(
        _INSERT_TICKET,
        (
            "t_existing",
            "Already here",
            "coding",
            "claude",
            "needs_success",
            "P2",
            "recap",
            "needs_success",
            "propose",
            "awaiting_approval",
            "{}",
            "worker",
            None,
            "{}",
            10,
            20,
            30,
            7,
        ),
    )
    conn.close()

    upgraded = connect(str(db_path))
    create_schema(upgraded)
    row = upgraded.execute(
        "SELECT wakes_supervisor FROM tickets WHERE id = ?", ("t_existing",)
    ).fetchone()
    assert row is not None
    assert int(row["wakes_supervisor"]) == 0
    upgraded.close()


def test_a_created_ticket_is_unwatched_unless_its_creator_says_otherwise(
    tmp_path: Path,
) -> None:
    with TestClient(_app(tmp_path)) as client:
        assert _create_ticket(client)["wakes_supervisor"] is False
        assert _create_ticket(client, wakes_supervisor=True)["wakes_supervisor"] is True


def test_the_user_turns_the_watch_on_and_off_after_creation(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        ticket_id = str(_create_ticket(client)["id"])

        turned_on = client.patch(
            f"/api/tickets/{ticket_id}", json={"wakes_supervisor": True}
        )
        assert turned_on.status_code == 200, turned_on.text
        assert turned_on.json()["wakes_supervisor"] is True

        turned_off = client.patch(
            f"/api/tickets/{ticket_id}", json={"wakes_supervisor": False}
        )
        assert turned_off.status_code == 200, turned_off.text
        assert turned_off.json()["wakes_supervisor"] is False


def test_the_supervisor_turns_the_watch_on_through_its_own_door(tmp_path: Path) -> None:
    """A running Ticket that turns out to matter starts waking, without being recreated."""
    with TestClient(_app(tmp_path)) as client:
        item_id = _create_item(client)
        ticket_id = str(_create_ticket(client, sprint_item_id=item_id)["id"])

        response = client.patch(
            f"/api/items/{item_id}/supervisor/tickets/{ticket_id}",
            json={"wakes_supervisor": True},
            headers={
                "X-Plan-Actor": "sprint_item_supervisor",
                "X-Plan-Sprint-Item-ID": item_id,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["wakes_supervisor"] is True
