"""Manager supervisor ownership, scope, and lazy conversation lifecycle."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation.backends.contracts import BackendChildFactory
from planner.conversation.contracts import (
    ConversationBackendKey,
    ConversationStartRequest,
)
from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationSystem,
)
from planner.conversation.message_content import text_message_content
from planner.core.clock import RealClock, build_clock, parse_fake_now
from planner.core.clock import TestClock as MutableClock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.errors import PlannerError
from planner.core.loops import BackgroundLoops, start_background_loops
from planner.core.server import create_app
from planner.managers import data as sprints_data
from planner.managers import service as sprints_service
from planner.runtime import conversation_start
from planner.tickets import data as tickets_data
from planner.tickets import revision_feedback

_OWNER_HOLDER = {"kind": "owner", "id": "owner"}


class _AdvanceClockOnOccupancyCheck:
    def __init__(self, system: InMemoryConversationSystem, clock: MutableClock) -> None:
        self._system = system
        self._clock = clock

    async def is_running(self, conversation_id: str) -> bool:
        self._clock.set(parse_fake_now("2026-09-22T05:01:00+02:00"))
        return await self._system.is_running(conversation_id)

    def __getattr__(self, name: str) -> object:
        return getattr(self._system, name)


def _app(
    tmp_path: Path,
    *,
    fake_now: str | None = None,
    backend_child_factories: Mapping[ConversationBackendKey, BackendChildFactory] | None = None,
) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "data" / "planning.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(str(db_path))
    create_schema(conn)

    conn.close()
    env = {
        "PLAN_TEST_MODE": "1",
        "PLAN_DB_PATH": str(db_path),
        "PLAN_LOGS_DIR": str(tmp_path / "logs"),
        "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "dispatcher.lock"),
    }
    if fake_now is not None:
        env["PLAN_FAKE_NOW"] = fake_now
    config = load_config(path=None, env=env)
    return (
        create_app(
            config,
            build_clock(config),
            lambda: connect(str(db_path)),
            conversation_system_for_test=(
                InMemoryConversationSystem() if backend_child_factories is None else None
            ),
            backend_child_factories_for_test=backend_child_factories,
        ),
        db_path,
    )


def _create_item(client: TestClient, title: str = "Supervised") -> dict[str, Any]:
    response = client.post("/api/managers", json={"title": title, "project_id": "project_vylo"})
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _park_a_proposal(client: TestClient, item_id: str) -> dict[str, Any]:
    created = client.post(
        "/api/tickets",
        json={
            "worker_type": "coding",
            "title": "Supervisor review",
            "brief": "Start here.",
            "manager": {"kind": "manager", "id": item_id},
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
        "X-Plan-Actor": "manager",
        "X-Plan-Manager-ID": item_id,
    }


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
                "brief": "Start.",
                "manager": {"kind": "manager", "id": own["id"]},
            },
        ).json()
        worker = {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": str(ticket["id"])}
        reply = client.post(
            "/api/messages/send",
            json={"target": {"kind": "manager", "id": own["id"]}, "message": "Answered."},
            headers=worker,
        )
        stranger = client.post(
            "/api/messages/send",
            json={"target": {"kind": "manager", "id": other["id"]}, "message": "Answered."},
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

    with closing(connect(str(db_path))) as conn:
        after = tickets_data.read_ticket(conn, str(ticket["id"]))
        feedback = revision_feedback.snapshot(conn, str(ticket["id"]))
    assert rejected.status_code == 200, rejected.text
    assert after.pending_proposal is None
    assert after.ticket_status.value == "empty"
    assert feedback is not None
    assert feedback.items[0].sender.kind.value == "manager"
    assert feedback.items[0].sender.id == str(item["id"])
    assert feedback.items[0].message == "State the verification evidence."
    assert system.backend_prompt_writes(conversation_id) == ()
    assert system.observations(conversation_id) == ()


def test_rejection_after_the_day_boundary_rearms_one_worker_step(tmp_path: Path) -> None:
    app, db_path = _app(tmp_path, fake_now="2026-09-22T04:59:00+02:00")
    conversation_id = "conv-rollover-revision"
    with TestClient(app) as client:
        item = _create_item(client)
        ticket = _park_a_proposal(client, str(item["id"]))
        with connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE tickets SET conversation_id = ? WHERE id = ?",
                (conversation_id, ticket["id"]),
            )
            before_claim_revision = tickets_data.read_ticket(
                conn, str(ticket["id"])
            ).worker_step_claim_revision
            conn.commit()
        system = cast(InMemoryConversationSystem, app.state.conversation_system)
        asyncio.run(
            system.start_conversation(
                ConversationStartRequest(conversation_id=conversation_id, model="test-model")
            )
        )
        assert client.portal is not None

        async def start_test_loops() -> BackgroundLoops:
            return start_background_loops(
                app.state.config,
                app.state.clock,
                conversation_system=system,
                asyncio_loop=asyncio.get_running_loop(),
            )

        loops = client.portal.call(start_test_loops)
        try:
            moved = client.post(
                "/api/test/set-now",
                json={"now": "2026-09-22T05:01:00+02:00"},
            )
            assert moved.status_code == 200, moved.text
            rejected = client.post(
                f"/api/tickets/{ticket['id']}/reject",
                json={"message": "Keep the rollover evidence exact."},
                headers=_supervisor_headers(str(item["id"])),
            )
            assert rejected.status_code == 200, rejected.text

            deadline = time.monotonic() + 5
            while (
                time.monotonic() < deadline
                and len(system.backend_prompt_writes(conversation_id)) < 1
            ):
                time.sleep(0.01)

            repeated = client.post(f"/api/test/run-step/{ticket['id']}")
            current = client.get(f"/api/tickets?detail=full&id={ticket['id']}")
        finally:
            client.portal.call(loops.stop)

    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["dispatched"] is False
    assert current.status_code == 200, current.text
    assert current.json()["ticket_status"] == "agent"
    assert current.json()["conversation_id"] == conversation_id
    writes = system.backend_prompt_writes(conversation_id)
    assert len(writes) == 1
    assert writes[0].sender_label == "loop"
    assert writes[0].text.count("Keep the rollover evidence exact.") == 1
    assert asyncio.run(system.is_running(conversation_id)) is True
    with closing(connect(str(db_path))) as conn:
        after = tickets_data.read_ticket(conn, str(ticket["id"]))
        assert revision_feedback.snapshot(conn, str(ticket["id"])) is None
    assert after.worker_step_claim.value == "out"
    assert after.worker_step_claim_revision == before_claim_revision + 1


def test_first_message_creates_the_conversation_and_reset_preserves_history(
    tmp_path: Path,
) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client)
        before = client.get("/api/managers", params={"detail": "full", "id": item["id"]}).json()
        sent = client.post(
            f"/api/managers/{item['id']}/conversation/send",
            json={
                "content": [{"piece": "text", "text": "Hello"}],
                "sender_label": "owner",
                "model": "gpt-5.6-terra",
                "reasoning_effort": "high",
            },
        )
        conversation_id = sent.json()["conversation_id"]
        after_send = client.get("/api/managers", params={"detail": "full", "id": item["id"]}).json()
        with connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO conversations(conversation_id,backend_key,model,"
                "workspace_folder,identity_environment_variables,access,created_at) VALUES "
                "(?, 'codex', 'gpt-5.6-sol', '/tmp', ?, 'full', 1)",
                (
                    conversation_id,
                    json.dumps(
                        [
                            ["PLAN_ACTOR", "manager"],
                            ["PLAN_MANAGER_ID", item["id"]],
                        ]
                    ),
                ),
            )
            conn.commit()
        reset = client.post(f"/api/managers/{item['id']}/conversation/reset")
        workspace = client.get(f"/api/managers/{item['id']}/workspace")

    assert before["manager"]["conversation_id"] is None
    assert sent.status_code == 200, sent.text
    assert conversation_id.startswith("conv_")
    launch = after_send["manager"]["launch_configuration"]
    assert launch["employee_launch_model"] == "gpt-5.6-terra"
    assert launch["employee_launch_reasoning_effort"] == "high"
    assert reset.json() == {"conversation_id": None}
    assert workspace.json()["conversation_history"] == [
        {"conversation_id": conversation_id, "created_at": 1}
    ]
    with closing(connect(str(db_path))) as conn:
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
                (item["manager"]["agent_key"],),
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
        item = sprints_data.create_manager(
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
                item.manager_agent_key,
                text_message_content("Hello"),
                conversation_start.manager_resolve(item),
                conversation_id=None,
                created_conversation_id=created_id,
                sender_label="owner",
                required_manager_id=item.id,
            )
        )
        await system.started.wait()
        await sprints_service.delete_manager(system, deleter, item.id, principal=OWNER_PRINCIPAL)
        system.release.set()
        with pytest.raises(PlannerError, match="no longer exists"):
            await send
        assert await system.is_running(created_id) is False
        assert (
            sender.execute(
                "SELECT 1 FROM agents WHERE agent_key=?", (item.manager_agent_key,)
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
            "brief": "Do the work.",
            "manager": {"kind": "manager", "id": item_id},
        },
    )
    assert created.status_code == 200, created.text
    return str(created.json()["id"])


# --- restarting a dead Worker -------------------------------------------------


def _stranded_child(
    client: TestClient,
    db_path: Path,
    item_id: str,
    *,
    conversation_id: str = "conv-dead-worker",
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
            "brief": "Start here.",
            "manager": {"kind": "manager", "id": item_id},
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
            "worker_step_claim_revision = worker_step_claim_revision + 1 WHERE id = ?",
            (conversation_id, ticket["id"]),
        )
        conn.commit()
    return str(ticket["id"])


def test_a_ticket_cannot_choose_who_stands_above_it(tmp_path: Path) -> None:
    """The stated exception, at the door that is re-parenting under another name.

    Outcome membership is the write that sets ``tickets.manager_id``. A Ticket that
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
                "brief": "Start.",
                "manager": {"kind": "manager", "id": own["id"]},
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
    assert after["manager_id"] == other["id"]


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
                "brief": "Start.",
                "manager": {"kind": "manager", "id": own["id"]},
            },
        ).json()
        stranger = client.post(
            "/api/tickets",
            json={"worker_type": "coding", "title": "Stranger", "brief": "Start."},
        ).json()
        itself = {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": str(mine["id"])}
        into_a_stranger = client.post(
            f"/api/tickets/{mine['id']}/request-help",
            json={"message": "Help.", "recipient": {"kind": "ticket", "id": stranger["id"]}},
            headers=itself,
        )
        up_to_its_outcome = client.post(
            f"/api/tickets/{mine['id']}/request-help",
            json={"message": "Help.", "recipient": {"kind": "manager", "id": own["id"]}},
            headers=itself,
        )

    assert into_a_stranger.json()["error"]["code"] == "agent_forbidden"
    # Asking its own Outcome for help is speaking up the chain, so it lands.
    assert up_to_its_outcome.status_code == 200, up_to_its_outcome.text
    assert up_to_its_outcome.json()["target"] == {"kind": "manager", "id": own["id"]}


def test_supervisor_peer_messages_do_not_expand_worker_or_mutation_reach(tmp_path: Path) -> None:
    app, _db_path = _app(tmp_path)
    with TestClient(app) as client:
        own = _create_item(client, "Own")
        other = _create_item(client, "Other")
        mine = _park_a_proposal(client, str(own["id"]))
        theirs = _park_a_proposal(client, str(other["id"]))
        before = client.get(f"/api/tickets?detail=full&id={theirs['id']}").json()
        own_headers = _supervisor_headers(str(own["id"]))
        worker = {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": str(mine["id"])}
        peer = client.post(
            "/api/messages/send",
            json={"target": {"kind": "manager", "id": other["id"]}, "message": "Coordinate"},
            headers=own_headers,
        )
        assert peer.status_code == 200, peer.text
        refused = [
            client.post(
                "/api/messages/send",
                json={"target": {"kind": "ticket", "id": theirs["id"]}, "message": "Act"},
                headers=own_headers,
            ),
            client.post(
                "/api/messages/send",
                json={"target": {"kind": "manager", "id": other["id"]}, "message": "Act"},
                headers=worker,
            ),
            client.post(
                f"/api/tickets/{mine['id']}/request-help",
                json={"recipient": {"kind": "manager", "id": other["id"]}, "message": "Help"},
                headers=worker,
            ),
            client.patch(
                f"/api/tickets/{theirs['id']}",
                json={"ceiling": "needs_plan"},
                headers=own_headers,
            ),
            client.post(
                f"/api/tickets/{theirs['id']}/accept/success_condition",
                json={"next_ceiling": "needs_what_changes", "next_holder": _OWNER_HOLDER},
                headers=own_headers,
            ),
            client.put(
                f"/api/collections/outcome_tickets/{own['id']}/{theirs['id']}",
                headers=own_headers,
            ),
            client.patch(
                f"/api/managers/{other['id']}",
                json={"recap": "Unauthorized change"},
                headers=own_headers,
            ),
        ]
        for response in refused:
            assert response.status_code == 400, response.text
            assert response.json()["error"]["code"] == "agent_forbidden", response.text
        assert client.get(f"/api/tickets?detail=full&id={theirs['id']}").json() == before
        assert client.get(f"/api/managers?detail=full&id={other['id']}").json()["recap"] == ""


def test_recap_public_patch_readback_preserves_archived_files(tmp_path: Path) -> None:
    """Recap transport and durable archives do not require a connected browser."""
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client, "Synthetic recap")
        item_id = item["id"]
        original = "Original synthetic goal: preserve the agreed scope."
        replacement = "The recap keeps this review easy to recover."
        notes = "Decision: retain the existing goal."
        files = db_path.parent / "files" / "sprint-items" / item_id / "artifacts"
        files.mkdir(parents=True)
        (files / "original-brief.md").write_text(original, encoding="utf-8")
        (files / "working-notes.md").write_text(notes, encoding="utf-8")
        saved = client.patch(f"/api/managers/{item_id}", json={"recap": replacement})
        assert saved.status_code == 200, saved.text
        assert client.get(f"/api/managers/{item_id}/workspace").json()["recap"] == replacement
        for name, content in (("original-brief.md", original), ("working-notes.md", notes)):
            archived = client.get(f"/files/managers/{item_id}/artifacts/{name}")
            assert archived.status_code == 200, archived.text
            assert archived.text == content


def test_workspace_history_keeps_exact_current_and_past_supervisor_identities(
    tmp_path: Path,
) -> None:
    """History selection consumes persisted ownership, not a second active conversation."""
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client, "Supervisor history")
        with connect(str(db_path)) as conn:
            identity = json.dumps(
                [
                    ["PLAN_ACTOR", "manager"],
                    ["PLAN_MANAGER_ID", item["id"]],
                ]
            )
            for name, created_at in (("past-supervisor", 10), ("current-supervisor", 20)):
                conn.execute(
                    "INSERT INTO conversations (conversation_id,backend_key,model,workspace_folder,"
                    "identity_environment_variables,access,latest_sequence,created_at) "
                    "VALUES (?,'codex','gpt-5.6-sol','/tmp',?,'full',0,?)",
                    (name, identity, created_at),
                )
            conn.execute(
                "UPDATE agents SET conversation_id=? WHERE agent_key=?",
                (
                    "current-supervisor",
                    item["manager"]["agent_key"],
                ),
            )
            conn.commit()
        response = client.get(f"/api/managers/{item['id']}/workspace")
        assert response.status_code == 200, response.text
        workspace = response.json()
        assert workspace["manager"]["conversation_id"] == "current-supervisor"
        assert workspace["conversation_history"] == [
            {"conversation_id": "past-supervisor", "created_at": 10},
            {"conversation_id": "current-supervisor", "created_at": 20},
        ]
        assert workspace["id"] == item["id"]
