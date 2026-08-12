"""Sprint Item supervisor ownership, scope, and lazy conversation lifecycle."""

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


def _app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "data" / "planning.db"
    db_path.parent.mkdir(parents=True)
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    config = load_config(path=None, env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)})
    return (
        create_app(
            config,
            build_clock(config),
            lambda: connect(str(db_path)),
            conversation_system_for_test=InMemoryConversationSystem(),
        ),
        db_path,
    )


def _create_item(client: TestClient, title: str = "Supervised") -> dict[str, Any]:
    response = client.post("/api/items", json={"title": title, "project_id": "project_vylo"})
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def test_creation_owns_one_agent_and_detail_exposes_the_launch_snapshot(
    tmp_path: Path,
) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)

    supervisor = cast(dict[str, Any], item["supervisor"])
    assert supervisor == {
        "agent_key": f"sprint_item_supervisor_{item['id']}",
        "conversation_id": None,
        "launch_configuration": {
            "employee_backend": "codex",
            "employee_launch_model": "gpt-5.6-sol",
            "employee_launch_reasoning_effort": "medium",
        },
    }
    with connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT conversation_id FROM agents WHERE agent_key=?",
            (supervisor["agent_key"],),
        ).fetchone()
        assert row is not None and row[0] is None


def test_supervisor_reads_only_its_item_and_current_children(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        first = _create_item(client, "First")
        second = _create_item(client, "Second")
        headers = {
            "X-Plan-Actor": "sprint_item_supervisor",
            "X-Plan-Sprint-Item-ID": str(first["id"]),
        }
        own = client.get(f"/api/items/{first['id']}/supervisor/context", headers=headers)
        cross = client.get(f"/api/items/{second['id']}/supervisor/context", headers=headers)
        broad_read = client.get(f"/api/items/{second['id']}", headers=headers)
        write = client.patch(
            f"/api/items/{first['id']}", json={"body": "not allowed"}, headers=headers
        )

    assert own.status_code == 200
    assert own.json()["sprint_item"]["body"] == ""
    assert own.json()["tickets"] == []
    assert cross.json()["error"]["code"] == "agent_forbidden"
    assert broad_read.json()["error"]["code"] == "agent_forbidden"
    assert write.json()["error"]["code"] == "agent_forbidden"


def test_first_message_creates_the_conversation_and_reset_preserves_history(
    tmp_path: Path,
) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        before = client.get(f"/api/items/{item['id']}/supervisor").json()
        sent = client.post(
            f"/api/items/{item['id']}/supervisor/conversation/send",
            json={
                "content": [{"piece": "text", "text": "Hello"}],
                "sender_label": "owner",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "high",
            },
        )
        conversation_id = sent.json()["conversation_id"]
        after_send = client.get(f"/api/items/{item['id']}/supervisor").json()
        with connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO conversations(conversation_id,backend_key,model,"
                "workspace_folder,access,created_at) VALUES "
                "(?, 'codex', 'gpt-5.6-sol', '/tmp', 'full', 1)",
                (conversation_id,),
            )
            conn.commit()
        reset = client.post(f"/api/items/{item['id']}/supervisor/conversation/reset")

    assert before["conversation_id"] is None
    assert sent.status_code == 200, sent.text
    assert conversation_id.startswith("conv_")
    assert after_send["launch_configuration"]["employee_launch_model"] == ("gpt-5.6-terra")
    assert after_send["launch_configuration"]["employee_launch_reasoning_effort"] == "high"
    assert reset.json() == {"conversation_id": None}
    with connect(str(db_path)) as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM conversations WHERE conversation_id=?", (conversation_id,)
            ).fetchone()
            is not None
        )
        assert (
            conn.execute(
                "SELECT conversation_id FROM agents WHERE agent_key=?",
                (item["supervisor"]["agent_key"],),
            ).fetchone()[0]
            is None
        )


def test_deleted_item_refuses_its_stale_supervisor_identity(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        client.delete(f"/api/items/{item['id']}")
        response = client.get(
            f"/api/items/{item['id']}/supervisor/context",
            headers={
                "X-Plan-Actor": "sprint_item_supervisor",
                "X-Plan-Sprint-Item-ID": str(item["id"]),
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "agent_forbidden"


def test_migration_backfills_normal_items_without_starting_conversations(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "migration.db"
    engine = db_module._migration_engine(str(db_path), 5000)
    try:
        with engine.begin() as connection:
            command.upgrade(db_module._alembic_config(connection), "direct_ticket_sprint_placement")
            connection.exec_driver_sql(
                "INSERT OR IGNORE INTO projects(id,name,summary,created_at,updated_at) "
                "VALUES ('project_vylo','Vylo','',1,1)"
            )
            connection.exec_driver_sql(
                "INSERT INTO sprint_items(id,title,project_id,created_at,updated_at) "
                "VALUES ('si_old','Old','project_vylo',1,1)"
            )
            command.upgrade(db_module._alembic_config(connection), "head")
    finally:
        engine.dispose()

    with connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT supervisor_agent_key, supervisor_backend, supervisor_model, "
            "supervisor_reasoning_effort FROM sprint_items WHERE id='si_old'"
        ).fetchone()
        assert tuple(row) == (
            "sprint_item_supervisor_si_old",
            "codex",
            "gpt-5.6-sol",
            "medium",
        )
        assert (
            conn.execute(
                "SELECT conversation_id FROM agents WHERE agent_key=?", (row[0],)
            ).fetchone()[0]
            is None
        )
