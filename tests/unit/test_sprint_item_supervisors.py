"""Sprint Item supervisor ownership, scope, and lazy conversation lifecycle."""

from __future__ import annotations

import asyncio
import json
import os
import time
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


def test_every_route_names_a_proposal_parked_on_the_item_the_same_way(
    tmp_path: Path,
) -> None:
    """The Workspace, the Board, and the Outcome's own reader get one answer.

    Before, the supervisor's own route asked the projection to treat the Item as the
    approver, so the identical word `awaiting_approval` meant "Khushal's" on two screens
    and "the supervisor's" on the third. That route is gone and the Outcome reads the
    ordinary Workspace, so there is no longer a third place for the word to mean
    something else — and nothing chooses per reader either.
    """
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        ticket = _park_a_proposal(client, str(item["id"]))
        page = client.get(f"/api/items/{item['id']}/workspace")
        board = client.get("/api/board")
        supervisor = client.get(
            f"/api/items/{item['id']}/workspace",
            headers=_supervisor_headers(str(item["id"])),
        )

    assert page.status_code == 200, page.text
    assert board.status_code == 200, board.text
    assert supervisor.status_code == 200, supervisor.text
    rows = [
        next(row for row in page.json()["tickets"] if row["id"] == ticket["id"]),
        next(row for row in supervisor.json()["tickets"] if row["id"] == ticket["id"]),
    ]
    cards = [
        card
        for column in board.json()["columns"]
        for card in column["cards"]
        if card["id"] == ticket["id"]
    ]
    for row in [*rows, *cards]:
        assert row["awaiting_approval"] is False
        assert row["awaiting_agent_approval"] is True


def test_workspace_artifacts_fold_each_folder_and_carry_its_newest_time(
    tmp_path: Path,
) -> None:
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
    assert workspace.json()["artifacts"] == [
        {
            "name": "new",
            "opens": None,
            "modified_at": 20.0,
            "children": [
                {
                    "name": "proof.png",
                    "opens": "artifacts/new/proof.png",
                    "modified_at": 20.0,
                    "children": [],
                }
            ],
        },
        {
            "name": "old",
            "opens": None,
            "modified_at": 10.0,
            "children": [
                {
                    "name": "proof.md",
                    "opens": "artifacts/old/proof.md",
                    "modified_at": 10.0,
                    "children": [],
                }
            ],
        },
    ]


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


def test_a_worker_can_answer_the_outcome_that_asked_it_something(tmp_path: Path) -> None:
    """The reply the message above demands must be sendable.

    Messaging down the chain is acting on the recipient, and the rule decides it.
    Messaging up is only speaking, so a Worker answering its own Outcome is admitted and
    a Worker reaching a different Outcome is not.
    """
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        own = _create_item(client, "Own")
        other = _create_item(client, "Other")
        ticket = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Current child",
                "kickoff_note": "Start.",
                "sprint_item_id": own["id"],
            },
        ).json()
        worker = {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": str(ticket["id"])}
        reply = client.post(
            "/api/messages/send",
            json={"target": {"kind": "sprint_item", "id": own["id"]}, "message": "Answered."},
            headers=worker,
        )
        stranger = client.post(
            "/api/messages/send",
            json={"target": {"kind": "sprint_item", "id": other["id"]}, "message": "Answered."},
            headers=worker,
        )
        to_owner = client.post(
            "/api/messages/send",
            json={"target": {"kind": "owner", "id": "owner"}, "message": "Answered."},
            headers=worker,
        )

    assert reply.status_code == 200, reply.text
    assert stranger.json()["error"]["code"] == "agent_forbidden"
    # Nobody stands above Khushal, so this one asks no authority question at all. It
    # fails later, on the Ticket having no conversation to send from.
    assert to_owner.json()["error"]["code"] == "not_found"


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
        ticket_id = _stranded_child(
            client,
            db_path,
            str(item["id"]),
            worker_step_claim_changed_at=int(time.time()) - 1,
        )
        configuration = {
            "employee_backend": "claude",
            "employee_launch_model": "claude-model",
            "employee_launch_reasoning_effort": "medium",
        }
        direct_configuration = client.put(
            f"/api/tickets/{ticket_id}/employee-configuration",
            json=configuration,
            headers=_supervisor_headers(str(item["id"])),
        )
        response = client.post(
            f"/api/tickets/{ticket_id}/restart-worker",
            json=configuration,
            headers=_supervisor_headers(str(item["id"])),
        )

    assert direct_configuration.status_code == 409, direct_configuration.text
    assert direct_configuration.json()["error"] == {
        "code": "already_running",
        "message": (
            "Employee configuration is frozen while a conversation or a worker step holds it"
        ),
        "detail": {"ticket_id": ticket_id},
    }
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["started"] is True
    assert body["not_started_because"] is None
    assert body["killed_conversation_id"] == "conv-dead-worker"
    assert body["ticket_status"] == "agent"
    assert body["conversation_id"] is not None
    assert body["conversation_id"] != "conv-dead-worker"
    assert body["employee_configuration"] == configuration


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


def test_a_ticket_cannot_choose_who_stands_above_it(tmp_path: Path) -> None:
    """The stated exception, at the door that is re-parenting under another name.

    Outcome membership is the write that sets ``tickets.sprint_item_id``. A Ticket that
    could set its own could move to another Outcome, or leave every Outcome, and pick who
    is allowed to accept, reject, delete or restart it. Being a thing does not include
    choosing who is above you.
    """
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        own = _create_item(client, "Own")
        other = _create_item(client, "Other")
        ticket = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Child",
                "kickoff_note": "Start.",
                "sprint_item_id": own["id"],
            },
        ).json()
        itself = {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": str(ticket["id"])}
        moved = client.put(
            f"/api/collections/outcome_tickets/{other['id']}/{ticket['id']}", headers=itself
        )
        orphaned = client.delete(
            f"/api/collections/outcome_tickets/{own['id']}/{ticket['id']}", headers=itself
        )
        parent_moves_it = client.put(
            f"/api/collections/outcome_tickets/{other['id']}/{ticket['id']}",
            headers=_supervisor_headers(str(own["id"])),
        )
        khushal_moves_it = client.put(
            f"/api/collections/outcome_tickets/{other['id']}/{ticket['id']}"
        )
        after = client.get(f"/api/tickets?detail=full&id={ticket['id']}").json()

    assert moved.json()["error"]["code"] == "agent_forbidden"
    assert orphaned.json()["error"]["code"] == "agent_forbidden"
    assert parent_moves_it.json()["error"]["code"] == "agent_forbidden"
    assert khushal_moves_it.status_code == 200, khushal_moves_it.text
    assert after["sprint_item_id"] == other["id"]


def test_a_help_request_reaches_no_further_than_a_sent_message(tmp_path: Path) -> None:
    """The second door that takes a recipient. It asks the same question as the first."""
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        own = _create_item(client, "Own")
        mine = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Mine",
                "kickoff_note": "Start.",
                "sprint_item_id": own["id"],
            },
        ).json()
        stranger = client.post(
            "/api/tickets",
            json={"worker_type": "coding", "title": "Stranger", "kickoff_note": "Start."},
        ).json()
        itself = {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": str(mine["id"])}
        into_a_stranger = client.post(
            f"/api/tickets/{mine['id']}/request-help",
            json={"message": "Help.", "recipient": {"kind": "ticket", "id": stranger["id"]}},
            headers=itself,
        )
        up_to_its_outcome = client.post(
            f"/api/tickets/{mine['id']}/request-help",
            json={"message": "Help.", "recipient": {"kind": "sprint_item", "id": own["id"]}},
            headers=itself,
        )

    assert into_a_stranger.json()["error"]["code"] == "agent_forbidden"
    # Asking its own Outcome for help is speaking up the chain, so it lands.
    assert up_to_its_outcome.status_code == 200, up_to_its_outcome.text
    assert up_to_its_outcome.json()["target"] == {"kind": "sprint_item", "id": own["id"]}


def test_a_conversation_pages_in_the_direction_it_was_asked_for(tmp_path: Path) -> None:
    """Five events, so a page of two cannot accidentally be the whole record.

    The read that replaced the Outcome-scoped history has to do both directions. A
    caller catching up reads on from where it got to; one that has just arrived reads
    the end, then the page before it. `has_more` means the same thing in both: there is
    record left in the direction you were reading.
    """
    app, db_path = _app(tmp_path)
    conversation_id = "conv-paging"
    with TestClient(app) as client:
        with connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO conversations(conversation_id,backend_key,model,"
                "workspace_folder,access,latest_sequence,created_at) "
                "VALUES (?, 'codex', 'test', '/tmp', 'full', 5, 1)",
                (conversation_id,),
            )
            conn.executemany(
                "INSERT INTO conversation_events "
                "(conversation_id,sequence,kind,payload,created_at) VALUES (?,?,?,?,?)",
                (
                    (conversation_id, n, "agent_message", json.dumps({"text": f"m{n}"}), n)
                    for n in range(1, 6)
                ),
            )
            conn.commit()

        def read(**params: Any) -> dict[str, Any]:
            response = client.get(
                f"/api/conversation/conversations/{conversation_id}/events", params=params
            )
            assert response.status_code == 200, response.text
            return cast(dict[str, Any], response.json())

        def sequences(page: dict[str, Any]) -> list[int]:
            return [event["sequence"] for event in page["events"]]

        last_page = read(limit=2)
        page_before_it = read(limit=2, before=4)
        oldest_page = read(limit=2, before=2)
        forwards = read(after=1, limit=2)
        forwards_to_the_end = read(after=3, limit=2)
        everything = read(after=0)
        one_direction = client.get(
            f"/api/conversation/conversations/{conversation_id}/events",
            params={"after": 1, "before": 4, "limit": 2},
        )
        too_large = client.get(
            f"/api/conversation/conversations/{conversation_id}/events", params={"limit": 101}
        )

    assert sequences(last_page) == [4, 5] and last_page["has_more"] is True
    assert sequences(page_before_it) == [2, 3] and page_before_it["has_more"] is True
    assert sequences(oldest_page) == [1] and oldest_page["has_more"] is False
    # `after` is honoured alongside `limit`. Reading it as "the last two" would answer
    # [4, 5] here, which is the opposite end of the record from the one asked for.
    assert sequences(forwards) == [2, 3] and forwards["has_more"] is True
    assert sequences(forwards_to_the_end) == [4, 5] and forwards_to_the_end["has_more"] is False
    assert sequences(everything) == [1, 2, 3, 4, 5] and everything["has_more"] is False
    assert one_direction.json()["error"]["code"] == "validation"
    assert too_large.json()["error"]["code"] == "validation"
