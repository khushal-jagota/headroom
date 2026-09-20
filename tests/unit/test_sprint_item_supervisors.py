"""Sprint Item supervisor ownership, scope, and lazy conversation lifecycle."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation.contracts import ConversationStartRequest
from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationSystem,
)
from planner.conversation.message_content import text_message_content
from planner.core import change_signal
from planner.core.clock import RealClock, build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.core.server import create_app
from planner.runtime import conversation_start
from planner.sprints import data as sprints_data
from planner.sprints import service as sprints_service
from planner.tickets import data as tickets_data
from planner.tickets import revision_feedback
from planner.tickets import views as tickets_views
from planner.tickets.contracts import TicketStatus

_OWNER_HOLDER = {"kind": "owner", "id": "owner"}


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


def _park_a_proposal(client: TestClient, item_id: str) -> dict[str, Any]:
    created = client.post(
        "/api/tickets",
        json={
            "worker_type": "coding",
            "title": "Supervisor review",
            "kickoff_note": "Start here.",
            "sprint_item_id": item_id,
            "ceiling": "needs_success_condition",
        },
        headers=_supervisor_headers(item_id),
    )
    assert created.status_code == 200, created.text
    ticket_id = str(created.json()["id"])
    proposed = client.post(
        f"/api/tickets/{ticket_id}/propose",
        json={"body": "The result is verified.", "recap": "Ready for review"},
        headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket_id},
    )
    assert proposed.status_code == 200, proposed.text
    assert proposed.json()["ticket_status"] == "awaiting_approval"
    return cast(dict[str, Any], proposed.json())


def _supervisor_headers(item_id: str) -> dict[str, str]:
    return {
        "X-Plan-Actor": "sprint_item_supervisor",
        "X-Plan-Sprint-Item-ID": item_id,
    }


def test_the_workspace_marks_a_proposal_addressed_to_its_reader(
    tmp_path: Path,
) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        ticket = _park_a_proposal(client, str(item["id"]))
        response = client.get(
            f"/api/items/{item['id']}/workspace",
            headers=_supervisor_headers(str(item["id"])),
        )

    assert response.status_code == 200, response.text
    row = next(entry for entry in response.json()["tickets"] if entry["id"] == ticket["id"])
    assert row["ticket_status"] == "awaiting_approval"
    assert row["awaiting_approval"] is True


def test_workspace_artifacts_include_each_file_modified_time(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        headers = _supervisor_headers(str(item["id"]))
        first = client.put(
            f"/files/sprint-items/{item['id']}/artifacts/old/proof.md",
            headers=headers,
            json={"content": "old"},
        )
        second = client.put(
            f"/files/sprint-items/{item['id']}/artifacts/new/proof.png",
            headers=headers,
            json={"content": "new"},
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        files_root = tmp_path / "data" / "files" / "sprint-items" / str(item["id"])
        os.utime(
            files_root / "artifacts" / "old" / "proof.md",
            ns=(10_000_000_000, 10_000_000_000),
        )
        os.utime(
            files_root / "artifacts" / "new" / "proof.png",
            ns=(20_000_000_000, 20_000_000_000),
        )
        workspace = client.get(f"/api/items/{item['id']}/workspace")

    assert workspace.status_code == 200, workspace.text
    assert sorted(workspace.json()["artifacts"], key=lambda artifact: artifact["path"]) == [
        {"path": "artifacts/new/proof.png", "modified_at": 20.0},
        {"path": "artifacts/old/proof.md", "modified_at": 10.0},
    ]


def test_supervisor_item_routes_refuse_a_cross_item_actor(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        first = _create_item(client, "First")
        second = _create_item(client, "Second")
        headers = {
            "X-Plan-Actor": "sprint_item_supervisor",
            "X-Plan-Sprint-Item-ID": str(first["id"]),
        }
        own = client.get(f"/api/items/{first['id']}/workspace", headers=headers)
        cross = client.get(f"/api/items/{second['id']}/workspace", headers=headers)
        own_write = client.patch(
            f"/api/items/{first['id']}", json={"body": "its own record"}, headers=headers
        )
        cross_write = client.patch(
            f"/api/items/{second['id']}", json={"body": "not allowed"}, headers=headers
        )

    assert own.status_code == 200
    assert own.json()["body"] == ""
    assert own.json()["tickets"] == []
    assert cross.status_code == 400
    assert cross.json()["error"]["code"] == "agent_forbidden"
    # Changed deliberately by the one-rule Ticket. A supervisor is its own Outcome, so
    # editing that Outcome's body is its own record and is admitted; it used to be refused
    # here and allowed only through a second address. Another Outcome is still refused,
    # which is what this test is for.
    assert own_write.status_code == 200, own_write.text
    assert own_write.json()["body"] == "its own record"
    assert cross_write.status_code == 400
    assert cross_write.json()["error"]["code"] == "agent_forbidden"


def test_a_ticket_and_its_conversation_read_at_their_own_addresses(
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
            "/api/tickets",
            params={"detail": "full", "id": ticket["id"]},
            headers=_supervisor_headers(str(item["id"])),
        )
        # The triggering Worker message is one event of the Ticket's conversation, so it
        # is read where the conversation is read rather than through a second door.
        triggering = client.get(
            f"/api/conversation/conversations/{conversation_id}/events",
            params={"after": 1, "limit": 1},
            headers=_supervisor_headers(str(item["id"])),
        )
        history = client.get(
            f"/api/conversation/conversations/{conversation_id}/events",
            params={"limit": 1},
            headers=_supervisor_headers(str(item["id"])),
        )
        signals: list[None] = []
        unsubscribe = change_signal.subscribe(lambda: signals.append(None))
        try:
            quiet_context = client.get(
                "/api/tickets",
                params={"detail": "full", "id": ticket["id"]},
                headers=_supervisor_headers(str(item["id"])),
            )
        finally:
            unsubscribe()

    assert context.status_code == 200, context.text
    assert context.json()["conversation_id"] == conversation_id
    assert triggering.json()["events"][0]["payload"]["text"] == "Exact Worker update"
    assert quiet_context.status_code == 200, quiet_context.text
    assert signals == []
    assert history.json()["events"][0]["sequence"] == 2
    assert history.json()["has_more"] is True


def test_reading_one_ticket_serializes_a_concurrent_child_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, db_path = _app(tmp_path)
    move_attempted = threading.Event()
    move_finished = threading.Event()
    move_thread: threading.Thread | None = None
    original_ticket_detail = tickets_views._ticket_detail_rows

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

        def ticket_detail_during_move(conn: Any, read: Any, now: int) -> dict[str, Any]:
            nonlocal move_thread
            move_thread = threading.Thread(target=move_child)
            move_thread.start()
            assert move_attempted.wait(1)
            assert move_finished.wait(1)
            return original_ticket_detail(conn, read, now)

        # Patched inside the snapshot, as the Outcome-scoped read used to be: the move
        # lands after BEGIN, so the read must still answer from before it.
        monkeypatch.setattr(tickets_views, "_ticket_detail_rows", ticket_detail_during_move)
        context = client.get(
            "/api/tickets",
            params={"detail": "full", "id": ticket["id"]},
            headers=_supervisor_headers(str(first["id"])),
        )

    assert move_thread is not None
    move_thread.join(timeout=2)
    assert move_finished.is_set()
    assert context.status_code == 200, context.text
    assert context.json()["sprint_item_id"] == first["id"]
    with connect(str(db_path)) as conn:
        assert tickets_data.read_ticket(conn, str(ticket["id"])).sprint_item_id == second["id"]


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
        before = client.get(f"/api/tickets?detail=full&id={ticket['id']}").json()
        sent = client.post(
            "/api/messages/send",
            json={
                "target": {"kind": "ticket", "id": ticket["id"]},
                "message": "Check the acceptance evidence.",
            },
            headers=_supervisor_headers(str(item["id"])),
        )
        after = client.get(f"/api/tickets?detail=full&id={ticket['id']}").json()

    assert sent.status_code == 200, sent.text
    assert sent.json()["target"] == {"kind": "ticket", "id": ticket["id"]}
    for field in ("stage", "ceiling", "ticket_status", "day_ids"):
        assert after[field] == before[field]
    write = cast(InMemoryConversationSystem, app.state.conversation_system).backend_prompt_writes(
        conversation_id
    )[0]
    assert write.sender_label == f"Sprint Item {item['id']}"
    assert write.text.startswith("[Authenticated Panels reply requirement]")
    assert f"panels send-message --sprint-item {item['id']}" in write.text
    assert write.text.endswith(f"Sprint Item {item['id']}:\nCheck the acceptance evidence.")


def test_supervisor_approves_only_an_exact_child_proposal(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        first = _create_item(client, "First")
        second = _create_item(client, "Second")
        ticket = _park_a_proposal(client, str(first["id"]))
        path = f"/api/tickets/{ticket['id']}/accept/success_condition"
        cross = client.post(
            path,
            json={
                "next_ceiling": "needs_what_changes",
                "next_holder": _OWNER_HOLDER,
            },
            headers=_supervisor_headers(str(second["id"])),
        )
        approved = client.post(
            path,
            json={
                "next_ceiling": "needs_what_changes",
                "next_holder": _OWNER_HOLDER,
            },
            headers=_supervisor_headers(str(first["id"])),
        )

    assert cross.status_code == 400
    assert cross.json()["error"]["code"] == "agent_forbidden"
    assert approved.status_code == 200, approved.text
    assert approved.json()["stage"] == "needs_what_changes"
    assert approved.json()["field_values"].get("success_condition") == "The result is verified."


def test_supervisor_rejection_stores_attributed_feedback_without_backend_io(
    tmp_path: Path,
) -> None:
    app, db_path = _app(tmp_path)
    conversation_id = "conv-supervisor-reject"
    with TestClient(app) as client:
        item = _create_item(client)
        other = _create_item(client, "Other")
        ticket = _park_a_proposal(client, str(item["id"]))
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
        system = cast(InMemoryConversationSystem, app.state.conversation_system)
        path = f"/api/tickets/{ticket['id']}/reject"
        cross = client.post(
            path,
            json={"message": "Not your Ticket."},
            headers=_supervisor_headers(str(other["id"])),
        )
        assert cross.status_code == 400
        assert cross.json()["error"]["code"] == "agent_forbidden"
        assert system.backend_prompt_writes(conversation_id) == ()
        rejected = client.post(
            path,
            json={"message": "State the verification evidence."},
            headers=_supervisor_headers(str(item["id"])),
        )

    with connect(str(db_path)) as conn:
        after = tickets_data.read_ticket(conn, str(ticket["id"]))
        feedback = revision_feedback.snapshot(conn, str(ticket["id"]))
    assert rejected.status_code == 200, rejected.text
    assert after.pending_proposal is None
    assert after.ticket_status.value == "empty"
    assert after.guidance == ""
    assert feedback is not None
    assert feedback.items[0].sender.kind.value == "sprint_item"
    assert feedback.items[0].sender.id == str(item["id"])
    assert feedback.items[0].message == "State the verification evidence."
    assert system.backend_prompt_writes(conversation_id) == ()
    assert system.observations(conversation_id) == ()


def test_first_message_creates_the_conversation_and_reset_preserves_history(
    tmp_path: Path,
) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        before = client.get("/api/items", params={"detail": "full", "id": item["id"]}).json()
        sent = client.post(
            f"/api/items/{item['id']}/conversation/send",
            json={
                "content": [{"piece": "text", "text": "Hello"}],
                "sender_label": "owner",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "high",
            },
        )
        conversation_id = sent.json()["conversation_id"]
        after_send = client.get(
            "/api/items", params={"detail": "full", "id": item["id"]}
        ).json()
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
        reset = client.post(f"/api/items/{item['id']}/conversation/reset")
        workspace = client.get(f"/api/items/{item['id']}/workspace")

    assert before["supervisor"]["conversation_id"] is None
    assert sent.status_code == 200, sent.text
    assert conversation_id.startswith("conv_")
    launch = after_send["supervisor"]["launch_configuration"]
    assert launch["employee_launch_model"] == "gpt-5.6-terra"
    assert launch["employee_launch_reasoning_effort"] == "high"
    assert reset.json() == {"conversation_id": None}
    assert workspace.json()["conversation_history"] == [
        {"conversation_id": conversation_id, "created_at": 1}
    ]
    with connect(str(db_path)) as conn:
        assert (
            conn.execute(
                "SELECT 1 FROM conversations WHERE conversation_id=?",
                (conversation_id,),
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
        await sprints_service.delete_item(system, deleter, item.id, principal=OWNER_PRINCIPAL)
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


def test_supervisor_creates_and_approves_a_ticket_under_its_own_item(
    tmp_path: Path,
) -> None:
    """A supervisor-created Ticket gets the same scope as anyone else's.

    Its kickoff parks for approval like every other kickoff, and the supervisor may
    approve it because it is a current child of the supervisor's own Item.
    """
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
            f"/api/tickets/{ticket_id}/accept/brief",
            json={
                "next_ceiling": "needs_what_changes",
                "next_holder": _OWNER_HOLDER,
            },
            headers=headers,
        )

    assert created.json()["ticket_status"] == "awaiting_approval"
    assert approved.status_code == 200, approved.text
    assert approved.json()["stage"] == "needs_success_condition"
    assert approved.json()["field_values"].get("brief") == "Do the work."


def _child_ticket(client: TestClient, item_id: str, title: str = "Child of the Item") -> str:
    created = client.post(
        "/api/tickets",
        json={
            "worker_type": "coding",
            "title": title,
            "kickoff_note": "Do the work.",
            "sprint_item_id": item_id,
        },
    )
    assert created.status_code == 200, created.text
    return str(created.json()["id"])


def test_supervisor_ticket_blocks_stay_inside_its_child_tickets(tmp_path: Path) -> None:
    """The supervisor writes blocks through the one membership call, with its own authority."""
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client, "Owned")
        other_item = _create_item(client, "Other")
        blocker_id = _child_ticket(client, str(item["id"]), "Blocker")
        blocked_id = _child_ticket(client, str(item["id"]), "Blocked")
        outsider_id = _child_ticket(client, str(other_item["id"]), "Outsider")
        headers = _supervisor_headers(str(item["id"]))

        added = client.put(f"/api/collections/blockers/{blocked_id}/{blocker_id}", headers=headers)
        cross_item = client.put(
            f"/api/collections/blockers/{outsider_id}/{blocker_id}", headers=headers
        )
        removed = client.delete(
            f"/api/collections/blockers/{blocked_id}/{blocker_id}", headers=headers
        )

    assert added.status_code == 200, added.text
    assert added.json() == {
        "collection": "blockers",
        "container_id": blocked_id,
        "member_id": blocker_id,
        "ok": True,
    }
    assert cross_item.status_code == 400
    assert cross_item.json()["error"]["code"] == "agent_forbidden"
    assert removed.status_code == 200, removed.text
    with connect(str(db_path)) as conn:
        assert conn.execute("SELECT count(*) FROM ticket_blocks").fetchone()[0] == 0


def test_generic_link_api_does_not_exist(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        assert client.post("/api/links", json={}).status_code == 404
        assert client.delete("/api/links").status_code == 404


# --- restarting a dead Worker -------------------------------------------------


def _stranded_child(
    client: TestClient,
    db_path: Path,
    item_id: str,
    *,
    conversation_id: str = "conv-dead-worker",
    worker_step_claim_changed_at: int = 1,
    worker_type: str = "coding",
) -> str:
    """A child Ticket exactly as a dead Worker leaves one: at `agent`, holding nothing.

    The claim is the status, so this is what the incident looked like — a Ticket that
    reads as claimed, pointing at a conversation nobody is coming back to.
    """
    ticket = client.post(
        "/api/tickets",
        json={
            "worker_type": worker_type,
            "title": "Stranded child",
            "kickoff_note": "Start here.",
            "sprint_item_id": item_id,
        },
    ).json()
    accepted = client.post(
        f"/api/tickets/{ticket['id']}/accept/brief",
        json={
            "next_ceiling": (
                "needs_purpose_and_boundaries"
                if worker_type == "new_worker"
                else "needs_success_condition"
            ),
            "next_holder": _OWNER_HOLDER,
        },
    )
    assert accepted.status_code == 200, accepted.text
    with connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO conversations(conversation_id,backend_key,model,"
            "workspace_folder,access,latest_sequence,created_at) "
            "VALUES (?, 'codex', 'test', '/tmp', 'full', 1, 1)",
            (conversation_id,),
        )
        conn.execute(
            "UPDATE tickets SET conversation_id = ?, worker_step_claim = 'out', "
            "worker_step_claim_changed_at = ? WHERE id = ?",
            (conversation_id, worker_step_claim_changed_at, ticket["id"]),
        )
        conn.commit()
    return str(ticket["id"])


def test_restart_gives_the_claim_back_and_starts_a_new_conversation(
    tmp_path: Path,
) -> None:
    """The whole point: a reset alone would leave this Ticket claimed and unreachable."""
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        ticket_id = _stranded_child(client, db_path, str(item["id"]))
        response = client.post(
            f"/api/tickets/{ticket_id}/restart-worker",
            json={},
            headers=_supervisor_headers(str(item["id"])),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["started"] is True
    assert body["not_started_because"] is None
    assert body["killed_conversation_id"] == "conv-dead-worker"
    assert body["ticket_status"] == "agent"
    assert body["conversation_id"] is not None
    assert body["conversation_id"] != "conv-dead-worker"
    assert body["employee_configuration"]["employee_backend"] == "codex"


def test_restart_clears_an_explicit_error_and_starts_again(tmp_path: Path) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        ticket_id = _stranded_child(client, db_path, str(item["id"]))
        with connect(str(db_path)) as conn:
            tickets_data.mark_ticket_errored(
                conn,
                ticket_id,
                now=1,
            )
        response = client.post(
            f"/api/tickets/{ticket_id}/restart-worker",
            json={},
            headers=_supervisor_headers(str(item["id"])),
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["started"] is True
    assert body["ticket_status"] == "agent"
    assert body["killed_conversation_id"] == "conv-dead-worker"
    with connect(str(db_path)) as conn:
        restarted = tickets_data.read_ticket(conn, ticket_id)
    assert restarted.ticket_status is TicketStatus.agent


def test_restart_refuses_a_stage_the_worker_does_not_own(tmp_path: Path) -> None:
    """A user-owned Stage has a collaborative discussion that restart must preserve."""
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        ticket_id = _stranded_child(
            client,
            db_path,
            str(item["id"]),
            worker_type="new_worker",
        )
        response = client.post(
            f"/api/tickets/{ticket_id}/restart-worker",
            json={},
            headers=_supervisor_headers(str(item["id"])),
        )
        after = client.get(f"/api/tickets?detail=full&id={ticket_id}")

    assert response.status_code == 400, response.text
    assert response.json()["error"]["message"] == (
        "only a Worker-owned Stage has a worker step to restart"
    )
    assert after.json()["conversation_id"] == "conv-dead-worker"
