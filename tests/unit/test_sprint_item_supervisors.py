"""Sprint Item supervisor ownership, scope, and lazy conversation lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation.contracts import ConversationStartRequest
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.message_content import text_message_content
from planner.core import db as db_module
from planner.core.clock import RealClock, build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.core.server import create_app
from planner.runtime import conversation_start
from planner.sprints import data as sprints_data
from planner.sprints import service as sprints_service
from planner.tickets import data as tickets_data


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


def _park_agent_review_ticket(client: TestClient, item_id: str) -> dict[str, Any]:
    created = client.post(
        "/api/tickets",
        json={
            "worker_type": "coding",
            "title": "Supervisor review",
            "kickoff_note": "Start here.",
            "sprint_item_id": item_id,
        },
    )
    assert created.status_code == 200, created.text
    ticket_id = str(created.json()["id"])
    kickoff = client.post(
        f"/api/tickets/{ticket_id}/accept/kickoff",
        json={"next_ceiling": "needs_success", "at_cap": "agent_review"},
    )
    assert kickoff.status_code == 200, kickoff.text
    proposed = client.post(
        f"/api/tickets/{ticket_id}/propose/success",
        json={"body": "The result is verified."},
        headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket_id},
    )
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["ticket_status"] == "awaiting_agent_review"
    return cast(dict[str, Any], proposed.json())


def _supervisor_headers(item_id: str) -> dict[str, str]:
    return {
        "X-Plan-Actor": "sprint_item_supervisor",
        "X-Plan-Sprint-Item-ID": item_id,
    }


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


def test_supervisor_approves_only_an_exact_child_agent_review(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        first = _create_item(client, "First")
        second = _create_item(client, "Second")
        ticket = _park_agent_review_ticket(client, str(first["id"]))
        path = f"/api/items/{first['id']}/supervisor/tickets/{ticket['id']}/approve"
        cross = client.post(
            path,
            json={"next_ceiling": "needs_approach", "at_cap": "agent_review"},
            headers=_supervisor_headers(str(second["id"])),
        )
        approved = client.post(
            path,
            json={"next_ceiling": "needs_approach", "at_cap": "agent_review"},
            headers=_supervisor_headers(str(first["id"])),
        )

    assert cross.status_code == 403
    assert cross.json()["error"]["code"] == "agent_forbidden"
    assert approved.status_code == 200, approved.text
    assert approved.json()["stage"] == "needs_approach"
    assert approved.json()["fields"]["success"]["value"] == "The result is verified."


def test_supervisor_transfer_changes_only_the_parked_review_route(tmp_path: Path) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        ticket = _park_agent_review_ticket(client, str(item["id"]))
        before_review = client.get("/api/review")
        transferred = client.post(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket['id']}/transfer-to-user-review",
            json={},
            headers=_supervisor_headers(str(item["id"])),
        )
        supervisor_approve_after_transfer = client.post(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket['id']}/approve",
            json={"next_ceiling": "needs_approach", "at_cap": "user_review"},
            headers=_supervisor_headers(str(item["id"])),
        )
        after_review = client.get("/api/review")

    assert before_review.json()["items"] == []
    assert transferred.status_code == 200, transferred.text
    assert transferred.json()["at_cap"] == "agent_review"
    assert transferred.json()["ticket_status"] == "awaiting_user_review"
    assert supervisor_approve_after_transfer.json()["error"]["code"] == "agent_forbidden"
    assert supervisor_approve_after_transfer.status_code == 400
    proposal = transferred.json()["fields"]["success"]["proposal"]
    assert proposal["review_route"] == "user_review"
    assert [item["ticket_id"] for item in after_review.json()["items"]] == [ticket["id"]]
    with connect(str(db_path)) as conn:
        stored = tickets_data.read_ticket(conn, str(ticket["id"]))
    assert stored.at_cap.value == "agent_review"


def test_direct_user_can_approve_an_agent_review_proposal(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        ticket = _park_agent_review_ticket(client, str(item["id"]))
        approved = client.post(
            f"/api/tickets/{ticket['id']}/accept/success",
            json={"next_ceiling": "needs_approach", "at_cap": "agent_review"},
        )

    assert approved.status_code == 200, approved.text
    assert approved.json()["stage"] == "needs_approach"


def test_supervisor_rejection_delivers_before_it_mutates(tmp_path: Path) -> None:
    app, db_path = _app(tmp_path)
    conversation_id = "conv-supervisor-reject"
    with TestClient(app) as client:
        item = _create_item(client)
        ticket = _park_agent_review_ticket(client, str(item["id"]))
        with connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE tickets SET conversation_id = ? WHERE id = ?",
                (conversation_id, ticket["id"]),
            )
            conn.commit()
        asyncio.run(
            app.state.conversation_system.start_conversation(
                ConversationStartRequest(
                    conversation_id=conversation_id,
                    model="test-model",
                )
            )
        )
        system = cast(InMemoryConversationSystem, app.state.conversation_system)
        system.arm_backend_write_failure(conversation_id)
        rejected = client.post(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket['id']}/reject",
            json={"message": "State the verification evidence."},
            headers=_supervisor_headers(str(item["id"])),
        )

    assert rejected.status_code == 503
    assert rejected.json()["error"]["code"] == "gateway_offline"
    with connect(str(db_path)) as conn:
        unchanged = tickets_data.read_ticket(conn, str(ticket["id"]))
    assert unchanged.ticket_status.value == "awaiting_agent_review"
    assert unchanged.fields.slots["success"].proposal is not None


def test_supervisor_rejection_delivers_focused_guidance_then_returns_work(
    tmp_path: Path,
) -> None:
    app, db_path = _app(tmp_path)
    conversation_id = "conv-supervisor-reject-success"
    with TestClient(app) as client:
        item = _create_item(client)
        ticket = _park_agent_review_ticket(client, str(item["id"]))
        with connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE tickets SET conversation_id = ? WHERE id = ?",
                (conversation_id, ticket["id"]),
            )
            conn.commit()
        asyncio.run(
            app.state.conversation_system.start_conversation(
                ConversationStartRequest(
                    conversation_id=conversation_id,
                    model="test-model",
                )
            )
        )
        rejected = client.post(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket['id']}/reject",
            json={"message": "State the verification evidence."},
            headers=_supervisor_headers(str(item["id"])),
        )

    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["ticket_status"] == "agent"
    assert rejected.json()["fields"]["success"]["proposal"] is None
    system = cast(InMemoryConversationSystem, app.state.conversation_system)
    writes = system.backend_prompt_writes(conversation_id)
    assert len(writes) == 1
    assert "State the verification evidence." in writes[0].text


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


def test_deleting_an_item_stops_its_active_supervisor(tmp_path: Path) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        sent = client.post(
            f"/api/items/{item['id']}/supervisor/conversation/send",
            json={
                "content": [{"piece": "text", "text": "Working"}],
                "sender_label": "owner",
            },
        ).json()
        conversation_id = sent["conversation_id"]
        system = cast(InMemoryConversationSystem, app.state.conversation_system)
        assert asyncio.run(system.is_running(conversation_id)) is True
        deleted = client.delete(f"/api/items/{item['id']}")

    assert deleted.status_code == 200, deleted.text
    assert asyncio.run(system.is_running(conversation_id)) is False
    with connect(str(db_path)) as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM agents WHERE agent_key=?",
                (item["supervisor"]["agent_key"],),
            ).fetchone()
            is None
        )


def test_queued_override_does_not_replace_the_saved_launch_choice(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        first = client.post(
            f"/api/items/{item['id']}/supervisor/conversation/send",
            json={
                "content": [{"piece": "text", "text": "First"}],
                "sender_label": "owner",
            },
        ).json()
        queued = client.post(
            f"/api/items/{item['id']}/supervisor/conversation/send",
            json={
                "conversation_id": first["conversation_id"],
                "model": "gpt-5.6-terra",
                "reasoning_effort": "high",
                "content": [{"piece": "text", "text": "Later"}],
                "sender_label": "owner",
            },
        )
        shown = client.get(f"/api/items/{item['id']}/supervisor").json()
        client.post(f"/api/items/{item['id']}/supervisor/conversation/reset")

    assert queued.json()["fate"] == "queued"
    assert shown["launch_configuration"] == {
        "employee_backend": "codex",
        "employee_launch_model": "gpt-5.6-sol",
        "employee_launch_reasoning_effort": "medium",
    }


def test_existing_conversation_rejects_a_backend_override(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        first = client.post(
            f"/api/items/{item['id']}/supervisor/conversation/send",
            json={
                "content": [{"piece": "text", "text": "First"}],
                "sender_label": "owner",
            },
        ).json()
        changed = client.post(
            f"/api/items/{item['id']}/supervisor/conversation/send",
            json={
                "conversation_id": first["conversation_id"],
                "backend_key": "hermes",
                "model": "openai-codex:gpt-5.6-sol",
                "content": [{"piece": "text", "text": "Move"}],
                "sender_label": "owner",
            },
        )

    assert changed.json()["error"]["code"] == "validation"


class _PausedStartSystem(InMemoryConversationSystem):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def start_conversation(self, request: ConversationStartRequest) -> None:
        await super().start_conversation(request)
        self.started.set()
        await self.release.wait()


def test_delete_between_backend_start_and_link_cannot_recreate_the_agent(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        db_path = tmp_path / "race.db"
        sender = connect(str(db_path))
        create_schema(sender)
        clock = RealClock()
        item = sprints_data.create_item(
            sender,
            title="Racing",
            project_id="project_vylo",
            clock=clock,
        )
        deleter = connect(str(db_path))
        system = _PausedStartSystem()
        created_id = conversation_start.new_conversation_id()
        send = asyncio.create_task(
            conversation_start.send_to_agent_conversation(
                system,
                sender,
                item.supervisor_agent_key,
                text_message_content("Hello"),
                conversation_start.sprint_item_supervisor_resolve(item),
                conversation_id=None,
                created_conversation_id=created_id,
                sender_label="owner",
                required_sprint_item_id=item.id,
            )
        )
        await system.started.wait()
        await sprints_service.delete_item(system, deleter, item.id, actor="human")
        system.release.set()
        with pytest.raises(PlannerError, match="no longer exists"):
            await send
        assert await system.is_running(created_id) is False
        assert (
            sender.execute(
                "SELECT 1 FROM agents WHERE agent_key=?", (item.supervisor_agent_key,)
            ).fetchone()
            is None
        )
        sender.close()
        deleter.close()

    asyncio.run(scenario())


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
