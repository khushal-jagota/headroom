"""Sprint Item supervisor ownership, scope, and lazy conversation lifecycle."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any, cast

import pytest
from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation.contracts import ConversationStartRequest
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.message_content import text_message_content
from planner.core import change_signal
from planner.core import db as db_module
from planner.core.clock import RealClock, build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.core.server import create_app
from planner.runtime import conversation_start
from planner.sprints import data as sprints_data
from planner.sprints import service as sprints_service
from planner.sprints import supervisor_service
from planner.tickets import data as tickets_data
from planner.tickets import views as tickets_views


def _app(tmp_path: Path, *, fake_now: str | None = None) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "data" / "planning.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(db_path))
    create_schema(conn)
    conn.close()
    env = {"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)}
    if fake_now is not None:
        env["PLAN_FAKE_NOW"] = fake_now
    config = load_config(path=None, env=env)
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


def test_workspace_read_joins_today_artifacts_and_supervisor_attention(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client, "Workspace outcome")
        ticket = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Today work",
                "kickoff_note": "Do the work.",
                "sprint_item_id": item["id"],
            },
        ).json()
        headers = _supervisor_headers(str(item["id"]))
        placed = client.post(
            f"/api/items/{item['id']}/supervisor/days/today/tickets/{ticket['id']}",
            headers=headers,
        )
        artifact = client.put(
            f"/api/items/{item['id']}/supervisor/artifacts/proof.md",
            json={"content": "# Proof"},
            headers=headers,
        )
        workspace = client.get(f"/api/items/{item['id']}/workspace")

    assert placed.status_code == 200, placed.text
    assert artifact.status_code == 200, artifact.text
    assert workspace.status_code == 200, workspace.text
    body = workspace.json()
    assert body["title"] == "Workspace outcome"
    assert body["today_ticket_ids"] == [ticket["id"]]
    assert body["tickets"][0]["worker_type"] == "coding"
    assert body["tickets"][0]["day_ids"] == [body["planning_day_id"]]
    assert body["artifacts"] == ["artifacts/proof.md"]
    assert body["conversation_history"] == []


def test_supervisor_item_routes_refuse_a_cross_item_actor(tmp_path: Path) -> None:
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
        write = client.patch(
            f"/api/items/{first['id']}", json={"body": "not allowed"}, headers=headers
        )

    assert own.status_code == 200
    assert own.json()["sprint_item"]["body"] == ""
    assert own.json()["tickets"] == []
    assert cross.status_code == 400
    assert cross.json()["error"]["code"] == "agent_forbidden"
    assert write.status_code == 400
    assert write.json()["error"]["code"] == "agent_forbidden"


def test_supervisor_context_and_history_use_only_the_current_child_conversation(
    tmp_path: Path,
) -> None:
    app, db_path = _app(tmp_path)
    conversation_id = "conv-current-worker-history"
    with TestClient(app) as client:
        item = _create_item(client)
        ticket = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Current child",
                "kickoff_note": "Start.",
                "sprint_item_id": item["id"],
            },
        ).json()
        with connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE tickets SET conversation_id = ? WHERE id = ?",
                (conversation_id, ticket["id"]),
            )
            conn.execute(
                "INSERT INTO conversations(conversation_id,backend_key,model,"
                "workspace_folder,access,latest_sequence,created_at) "
                "VALUES (?, 'codex', 'test', '/tmp', 'full', 2, 1)",
                (conversation_id,),
            )
            conn.executemany(
                "INSERT INTO conversation_events "
                "(conversation_id,sequence,kind,payload,created_at) VALUES (?,?,?,?,?)",
                (
                    (
                        conversation_id,
                        1,
                        "prompt",
                        json.dumps({"text": "Start", "sender_label": "automatic-loop"}),
                        1,
                    ),
                    (
                        conversation_id,
                        2,
                        "agent_message",
                        json.dumps({"text": "Exact Worker update"}),
                        2,
                    ),
                ),
            )
            conn.commit()
        context = client.get(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket['id']}/context",
            params={"triggering_message_sequence": 2},
            headers=_supervisor_headers(str(item["id"])),
        )
        context_without_trigger = client.get(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket['id']}/context",
            headers=_supervisor_headers(str(item["id"])),
        )
        history = client.get(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket['id']}/history",
            params={"limit": 1},
            headers=_supervisor_headers(str(item["id"])),
        )
        signals: list[None] = []
        unsubscribe = change_signal.subscribe(lambda: signals.append(None))
        try:
            quiet_context = client.get(
                f"/api/items/{item['id']}/supervisor/tickets/{ticket['id']}/context",
                headers=_supervisor_headers(str(item["id"])),
            )
        finally:
            unsubscribe()

    assert context.status_code == 200, context.text
    assert context.json()["triggering_worker_message"]["payload"]["text"] == (
        "Exact Worker update"
    )
    assert context.json()["conversation_id"] == conversation_id
    assert context_without_trigger.json()["triggering_worker_message"] is None
    assert quiet_context.status_code == 200, quiet_context.text
    assert signals == []
    assert history.json()["events"][0]["sequence"] == 2
    assert history.json()["has_more"] is True


def test_ticket_context_serializes_a_concurrent_child_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, db_path = _app(tmp_path)
    move_attempted = threading.Event()
    move_finished = threading.Event()
    move_thread: threading.Thread | None = None
    original_ticket_detail = tickets_views.ticket_detail

    with TestClient(app) as client:
        first = _create_item(client, "First")
        second = _create_item(client, "Second")
        ticket = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Moving child",
                "kickoff_note": "Start.",
                "sprint_item_id": first["id"],
            },
        ).json()

        def move_child() -> None:
            with connect(str(db_path)) as moving:
                move_attempted.set()
                moving.execute(
                    "UPDATE tickets SET sprint_item_id = ? WHERE id = ?",
                    (second["id"], ticket["id"]),
                )
                moving.commit()
            move_finished.set()

        def ticket_detail_during_move(
            conn: Any, ticket_id: str, now: int
        ) -> dict[str, Any]:
            nonlocal move_thread
            move_thread = threading.Thread(target=move_child)
            move_thread.start()
            assert move_attempted.wait(1)
            assert move_finished.wait(1)
            return original_ticket_detail(conn, ticket_id, now)

        monkeypatch.setattr(tickets_views, "ticket_detail", ticket_detail_during_move)
        context = client.get(
            f"/api/items/{first['id']}/supervisor/tickets/{ticket['id']}/context",
            headers=_supervisor_headers(str(first["id"])),
        )

    assert move_thread is not None
    move_thread.join(timeout=2)
    assert move_finished.is_set()
    assert context.status_code == 200, context.text
    assert context.json()["ticket"]["sprint_item_id"] == first["id"]
    with connect(str(db_path)) as conn:
        assert tickets_data.read_ticket(conn, str(ticket["id"])).sprint_item_id == second["id"]


def test_history_serializes_a_concurrent_conversation_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, db_path = _app(tmp_path)
    conversation_id = "conv-history-before-reset"
    replacement_id = "conv-history-after-reset"
    reset_attempted = threading.Event()
    reset_finished = threading.Event()
    reset_thread: threading.Thread | None = None
    original_require_current_child = supervisor_service.require_current_child

    with TestClient(app) as client:
        item = _create_item(client)
        ticket = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Reset child",
                "kickoff_note": "Start.",
                "sprint_item_id": item["id"],
            },
        ).json()
        with connect(str(db_path)) as conn:
            conn.executemany(
                "INSERT INTO conversations(conversation_id,backend_key,model,"
                "workspace_folder,access,latest_sequence,created_at) "
                "VALUES (?, 'codex', 'test', '/tmp', 'full', ?, 1)",
                ((conversation_id, 1), (replacement_id, 0)),
            )
            conn.execute(
                "UPDATE tickets SET conversation_id = ? WHERE id = ?",
                (conversation_id, ticket["id"]),
            )
            conn.execute(
                "INSERT INTO conversation_events "
                "(conversation_id,sequence,kind,payload,created_at) VALUES (?,?,?,?,?)",
                (
                    conversation_id,
                    1,
                    "agent_message",
                    json.dumps({"text": "History before reset"}),
                    1,
                ),
            )
            conn.commit()

        def reset_conversation() -> None:
            with connect(str(db_path)) as resetting:
                reset_attempted.set()
                resetting.execute(
                    "UPDATE tickets SET conversation_id = ? WHERE id = ?",
                    (replacement_id, ticket["id"]),
                )
                resetting.commit()
            reset_finished.set()

        def require_child_during_reset(*args: Any, **kwargs: Any) -> Any:
            nonlocal reset_thread
            ticket_result = original_require_current_child(*args, **kwargs)
            reset_thread = threading.Thread(target=reset_conversation)
            reset_thread.start()
            assert reset_attempted.wait(1)
            assert reset_finished.wait(1)
            return ticket_result

        monkeypatch.setattr(
            supervisor_service, "require_current_child", require_child_during_reset
        )
        history = client.get(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket['id']}/history",
            headers=_supervisor_headers(str(item["id"])),
        )

    assert reset_thread is not None
    reset_thread.join(timeout=2)
    assert reset_finished.is_set()
    assert history.status_code == 200, history.text
    assert history.json()["conversation_id"] == conversation_id
    assert history.json()["events"][0]["payload"]["text"] == "History before reset"
    with connect(str(db_path)) as conn:
        assert tickets_data.read_ticket(conn, str(ticket["id"])).conversation_id == (
            replacement_id
        )


def test_targeted_worker_message_is_attributed_and_preserves_ticket_facts(
    tmp_path: Path,
) -> None:
    app, db_path = _app(tmp_path)
    conversation_id = "conv-current-worker-message"
    with TestClient(app) as client:
        item = _create_item(client)
        ticket = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Current child",
                "kickoff_note": "Start.",
                "sprint_item_id": item["id"],
            },
        ).json()
        with connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE tickets SET conversation_id = ? WHERE id = ?",
                (conversation_id, ticket["id"]),
            )
            conn.commit()
        asyncio.run(
            app.state.conversation_system.start_conversation(
                ConversationStartRequest(conversation_id=conversation_id, model="test-model")
            )
        )
        before = client.get(f"/api/tickets/{ticket['id']}").json()
        sent = client.post(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket['id']}/message",
            json={
                "conversation_id": conversation_id,
                "message": "Check the acceptance evidence.",
            },
            headers=_supervisor_headers(str(item["id"])),
        )
        after = client.get(f"/api/tickets/{ticket['id']}").json()

    assert sent.status_code == 200, sent.text
    assert sent.json()["sender"] == item["supervisor"]["agent_key"]
    for field in ("stage", "ceiling", "at_cap", "ticket_status", "day_ids"):
        assert after[field] == before[field]
    write = cast(
        InMemoryConversationSystem, app.state.conversation_system
    ).backend_prompt_writes(conversation_id)[0]
    assert write.sender_label == item["supervisor"]["agent_key"]
    assert write.text == "Check the acceptance evidence."


def test_targeted_worker_message_refuses_missing_stale_and_cross_item_targets(
    tmp_path: Path,
) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        first = _create_item(client, "First")
        second = _create_item(client, "Second")
        ticket = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Current child",
                "kickoff_note": "Start.",
                "sprint_item_id": first["id"],
            },
        ).json()
        path = f"/api/items/{first['id']}/supervisor/tickets/{ticket['id']}/message"
        missing = client.post(
            path,
            json={"conversation_id": "conv_missing", "message": "Hello"},
            headers=_supervisor_headers(str(first["id"])),
        )
        with connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE tickets SET conversation_id='conv_current' WHERE id=?",
                (ticket["id"],),
            )
            conn.commit()
        stale = client.post(
            path,
            json={"conversation_id": "conv_stale", "message": "Hello"},
            headers=_supervisor_headers(str(first["id"])),
        )
        cross = client.post(
            path,
            json={"conversation_id": "conv_current", "message": "Hello"},
            headers=_supervisor_headers(str(second["id"])),
        )

    assert missing.json()["error"]["code"] == "not_found"
    assert stale.json()["error"]["code"] == "not_found"
    assert cross.json()["error"]["code"] == "agent_forbidden"


def test_supervisor_artifacts_stay_under_the_owned_item(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        headers = _supervisor_headers(str(item["id"]))
        written = client.put(
            f"/api/items/{item['id']}/supervisor/artifacts/notes/proof.md",
            json={"content": "Verified."},
            headers=headers,
        )
        listed = client.get(
            f"/api/items/{item['id']}/supervisor/artifacts", headers=headers
        )
        traversal = client.put(
            f"/api/items/{item['id']}/supervisor/artifacts/%2e%2e/escape.md",
            json={"content": "No."},
            headers=headers,
        )

    assert written.status_code == 200, written.text
    assert written.json()["url"].endswith("/artifacts/notes/proof.md")
    assert listed.json()["artifacts"] == ["notes/proof.md"]
    assert traversal.status_code in {400, 404}


def test_supervisor_management_actions_use_current_child_scope(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        other = _create_item(client, "Other")
        first = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "First",
                "kickoff_note": "Start.",
                "sprint_item_id": item["id"],
            },
        ).json()
        second = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Second",
                "kickoff_note": "Start.",
                "sprint_item_id": item["id"],
            },
        ).json()
        foreign = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Foreign",
                "kickoff_note": "Start.",
                "sprint_item_id": other["id"],
            },
        ).json()
        headers = _supervisor_headers(str(item["id"]))
        item_edit = client.patch(
            f"/api/items/{item['id']}/supervisor/item",
            json={"body": "Updated brief."},
            headers=headers,
        )
        ticket_edit = client.patch(
            f"/api/items/{item['id']}/supervisor/tickets/{first['id']}",
            json={"priority": "P1"},
            headers=headers,
        )
        scoped = client.post(
            f"/api/items/{item['id']}/supervisor/tickets/{first['id']}/scope",
            json={"ceiling": "needs_approach", "at_cap": "agent_review"},
            headers=headers,
        )
        day = client.post(
            f"/api/items/{item['id']}/supervisor/days/2026-08-13/tickets/{first['id']}",
            headers=headers,
        )
        blocked = client.post(
            f"/api/items/{item['id']}/supervisor/blocks",
            json={"from_id": second["id"], "to_id": first["id"]},
            headers=headers,
        )
        cross = client.patch(
            f"/api/items/{item['id']}/supervisor/tickets/{foreign['id']}",
            json={"priority": "P0"},
            headers=headers,
        )

    assert item_edit.json()["body"] == "Updated brief."
    assert ticket_edit.json()["priority"] == "P1"
    assert scoped.json()["ceiling"] == "needs_approach"
    assert scoped.json()["at_cap"] == "agent_review"
    assert day.json()["day_id"] == "day_2026-08-13"
    assert blocked.json()["kind"] == "blocks"
    assert cross.json()["error"]["code"] == "agent_forbidden"


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

    assert cross.status_code == 400
    assert cross.json()["error"]["code"] == "agent_forbidden"
    assert approved.status_code == 200, approved.text
    assert approved.json()["stage"] == "needs_approach"
    assert approved.json()["fields"]["success"]["value"] == "The result is verified."


def test_scope_change_hands_the_waiting_proposal_to_the_user(tmp_path: Path) -> None:
    """The supervisor hands review over by scope, and the proposal already parked moves."""
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        ticket = _park_agent_review_ticket(client, str(item["id"]))
        before_review = client.get("/api/review")
        scoped = client.post(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket['id']}/scope",
            json={"ceiling": "needs_success", "at_cap": "user_review"},
            headers=_supervisor_headers(str(item["id"])),
        )
        supervisor_approve_after_handover = client.post(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket['id']}/approve",
            json={"next_ceiling": "needs_approach", "at_cap": "user_review"},
            headers=_supervisor_headers(str(item["id"])),
        )
        after_review = client.get("/api/review")

    assert before_review.json()["items"] == []
    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["at_cap"] == "user_review"
    assert scoped.json()["ticket_status"] == "awaiting_user_review"
    assert supervisor_approve_after_handover.status_code == 400
    assert supervisor_approve_after_handover.json()["error"]["code"] == "agent_forbidden"
    assert [row["ticket_id"] for row in after_review.json()["items"]] == [ticket["id"]]
    with connect(str(db_path)) as conn:
        stored = tickets_data.read_ticket(conn, str(ticket["id"]))
    assert stored.at_cap.value == "user_review"


def test_handover_by_scope_survives_a_rejection_and_the_next_proposal(
    tmp_path: Path,
) -> None:
    """The reported bug: a revision after a user rejection went back to the supervisor."""
    app, db_path = _app(tmp_path)
    conversation_id = "conv-handover-revision"
    with TestClient(app) as client:
        item = _create_item(client)
        ticket = _park_agent_review_ticket(client, str(item["id"]))
        ticket_id = str(ticket["id"])
        with connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE tickets SET conversation_id = ? WHERE id = ?",
                (conversation_id, ticket_id),
            )
            conn.commit()
        asyncio.run(
            app.state.conversation_system.start_conversation(
                ConversationStartRequest(conversation_id=conversation_id, model="test-model")
            )
        )
        client.post(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket_id}/scope",
            json={"ceiling": "needs_success", "at_cap": "user_review"},
            headers=_supervisor_headers(str(item["id"])),
        )
        supervisor_reject_after_handover = client.post(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket_id}/reject",
            json={"message": "Not mine to send back."},
            headers=_supervisor_headers(str(item["id"])),
        )
        rejected = client.post(
            f"/api/tickets/{ticket_id}/return-for-revision",
            json={"message": "State the verification evidence."},
        )
        revised = client.post(
            f"/api/tickets/{ticket_id}/propose/success",
            json={"body": "The result is verified against the log."},
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket_id},
        )
        supervisor_approve_the_revision = client.post(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket_id}/approve",
            json={"next_ceiling": "needs_approach", "at_cap": "user_review"},
            headers=_supervisor_headers(str(item["id"])),
        )

    assert supervisor_reject_after_handover.status_code == 400
    assert supervisor_reject_after_handover.json()["error"]["code"] == "agent_forbidden"
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["fields"]["success"]["proposal"] is None
    assert revised.status_code == 200, revised.text
    assert revised.json()["ticket_status"] == "awaiting_user_review"
    assert supervisor_approve_the_revision.status_code == 400
    assert supervisor_approve_the_revision.json()["error"]["code"] == "agent_forbidden"


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
                "workspace_folder,identity_environment_variables,access,created_at) VALUES "
                "(?, 'codex', 'gpt-5.6-sol', '/tmp', ?, 'full', 1)",
                (
                    conversation_id,
                    json.dumps(
                        [
                            ["PLAN_ACTOR", "sprint_item_supervisor"],
                            ["PLAN_SPRINT_ITEM_ID", item["id"]],
                        ]
                    ),
                ),
            )
            conn.commit()
        reset = client.post(f"/api/items/{item['id']}/supervisor/conversation/reset")
        workspace = client.get(f"/api/items/{item['id']}/workspace")

    assert before["conversation_id"] is None
    assert sent.status_code == 200, sent.text
    assert conversation_id.startswith("conv_")
    assert after_send["launch_configuration"]["employee_launch_model"] == ("gpt-5.6-terra")
    assert after_send["launch_configuration"]["employee_launch_reasoning_effort"] == "high"
    assert reset.json() == {"conversation_id": None}
    assert workspace.json()["conversation_history"] == [
        {"conversation_id": conversation_id, "created_at": 1}
    ]
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


def test_supervisor_creates_and_reviews_a_ticket_under_its_own_item(tmp_path: Path) -> None:
    """The supervisor is the creating authority, so its own kickoff routes back to it."""
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client, "Owned")
        headers = _supervisor_headers(str(item["id"]))
        created = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Child of the Item",
                "kickoff_note": "Do the work.",
                "sprint_item_id": str(item["id"]),
            },
            headers=headers,
        )
        assert created.status_code == 200, created.text
        ticket_id = str(created.json()["id"])
        approved = client.post(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket_id}/approve",
            json={"next_ceiling": "needs_approach", "at_cap": "agent_review"},
            headers=headers,
        )

    assert created.json()["at_cap"] == "agent_review"
    assert created.json()["ticket_status"] == "awaiting_agent_review"
    assert approved.status_code == 200, approved.text
    assert approved.json()["stage"] == "needs_success"
    assert approved.json()["fields"]["kickoff"]["value"] == "Do the work."


def test_supervisor_created_ticket_outside_its_item_stays_user_reviewed(
    tmp_path: Path,
) -> None:
    """The surviving rule is the only rule: a supervisor reviews inside its own Item."""
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        first = _create_item(client, "First")
        second = _create_item(client, "Second")
        created = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Child of another Item",
                "kickoff_note": "Do the work.",
                "sprint_item_id": str(second["id"]),
            },
            headers=_supervisor_headers(str(first["id"])),
        )
        assert created.status_code == 200, created.text
        refused = client.post(
            f"/api/items/{first['id']}/supervisor/tickets/{created.json()['id']}/approve",
            json={"next_ceiling": "needs_approach", "at_cap": "agent_review"},
            headers=_supervisor_headers(str(first["id"])),
        )

    assert created.json()["at_cap"] == "user_review"
    assert created.json()["ticket_status"] == "awaiting_user_review"
    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "agent_forbidden"


def test_a_ceiling_accepts_the_plain_stage_name(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client, "Scoped")
        ticket = _park_agent_review_ticket(client, str(item["id"]))
        scoped = client.post(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket['id']}/scope",
            json={"ceiling": "closeout", "at_cap": "agent_review"},
            headers=_supervisor_headers(str(item["id"])),
        )
        unknown = client.post(
            f"/api/items/{item['id']}/supervisor/tickets/{ticket['id']}/scope",
            json={"ceiling": "nonsense", "at_cap": "agent_review"},
            headers=_supervisor_headers(str(item["id"])),
        )

    assert scoped.status_code == 200, scoped.text
    assert scoped.json()["ceiling"] == "needs_closeout"
    assert unknown.json()["error"]["code"] == "scope_invalid"


def test_scope_stated_at_creation_settles_the_kickoff(tmp_path: Path) -> None:
    """A creator that states scope creates a Ticket already scoped, with nothing parked."""
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client, "Stated")
        stated = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Go through it completely",
                "kickoff_note": "Do the whole thing.",
                "sprint_item_id": str(item["id"]),
                "ceiling": "closeout",
                "at_cap": "agent_review",
            },
            headers=_supervisor_headers(str(item["id"])),
        )
        default = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Ordinary intake",
                "kickoff_note": "Look at this.",
            },
        )

    assert stated.status_code == 200, stated.text
    assert stated.json()["ceiling"] == "needs_closeout"
    assert stated.json()["at_cap"] == "agent_review"
    assert stated.json()["stage"] == "needs_success"
    assert stated.json()["ticket_status"] == "empty"
    assert stated.json()["fields"]["kickoff"]["value"] == "Do the whole thing."
    assert stated.json()["fields"]["kickoff"]["proposal"] is None
    # No explicit scope keeps the default leash.
    assert default.json()["at_cap"] == "user_review"
    assert default.json()["ticket_status"] == "awaiting_user_review"


def test_stated_agent_review_needs_a_normal_sprint_item(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        refused = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Nowhere to review it",
                "kickoff_note": "Do the whole thing.",
                "ceiling": "closeout",
                "at_cap": "agent_review",
            },
        )

    assert refused.json()["error"]["code"] == "scope_invalid"
