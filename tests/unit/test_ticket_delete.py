from __future__ import annotations

import asyncio
from pathlib import Path
from sqlite3 import Connection

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.cli import http as cli_http
from planner.cli.main import main as cli_main
from planner.conversation.contracts import ConversationStartRequest
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.message_content import text_message_content
from planner.core import change_signal
from planner.core import links as core_links
from planner.core.clock import TestClock as PlannerTestClock
from planner.core.clock import build_clock
from planner.core.config import Config, load_config
from planner.core.contracts import LinkKind
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.core.server import create_app
from planner.days import data as days_data
from planner.runtime import worker_step_readiness
from planner.tickets import data as tickets_data
from planner.tickets.contracts import NO_FURTHER, TITLE_MAX_CHARS, AtCap, Ticket


def _create(
    conn: Connection,
    cfg: Config,
    clock: PlannerTestClock,
    title: str,
    *,
    sprint_item_id: str | None = None,
) -> Ticket:
    ticket = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title=title,
        actor="human",
        now=clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        sprint_item_id=sprint_item_id,
    )
    return tickets_data.accept_proposal(
        conn,
        ticket.id,
        field="kickoff",
        actor="human",
        now=clock.now_unix(),
        next_ceiling=NO_FURTHER,
        at_cap=AtCap.propose,
    )


def test_ticket_delete_cli_requires_yes_and_forwards_confirmed_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    def send(method: str, path: str, **kwargs: object) -> dict[str, object]:
        calls.append((method, path, kwargs))
        return {"ok": True, "ticket_id": "t_delete"}

    monkeypatch.setattr(cli_http, "send", send)
    runner = CliRunner()
    refused = runner.invoke(cli_main, ["ticket", "delete", "t_delete", "--json"])
    assert refused.exit_code == 1
    assert "permanent deletion requires --yes" in refused.stderr
    assert calls == []

    deleted = runner.invoke(
        cli_main, ["ticket", "delete", "t_delete", "--yes", "--json"]
    )
    assert deleted.exit_code == 0, deleted.output
    assert calls == [
        (
            "DELETE",
            "/api/tickets/t_delete",
            {"as_json": True, "params": None, "request_actor": "ordinary"},
        )
    ]


def test_ticket_delete_cli_force_still_requires_yes_and_carries_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, object]]] = []

    def send(method: str, path: str, **kwargs: object) -> dict[str, object]:
        calls.append((method, path, kwargs))
        return {"ok": True, "ticket_id": "t_delete"}

    monkeypatch.setattr(cli_http, "send", send)
    runner = CliRunner()
    refused = runner.invoke(cli_main, ["ticket", "delete", "t_delete", "--force", "--json"])
    assert refused.exit_code == 1
    assert "permanent deletion requires --yes" in refused.stderr
    assert calls == []

    forced = runner.invoke(
        cli_main, ["ticket", "delete", "t_delete", "--yes", "--force", "--json"]
    )
    assert forced.exit_code == 0, forced.output
    assert calls == [
        (
            "DELETE",
            "/api/tickets/t_delete",
            {"as_json": True, "params": {"force": True}, "request_actor": "ordinary"},
        )
    ]


def test_delete_ticket_removes_full_footprint_and_keeps_one_minimal_audit(
    tmp_db: Connection, cfg: Config, fake_clock: PlannerTestClock
) -> None:
    now = fake_clock.now_unix()
    tmp_db.execute(
        "INSERT INTO sprint_items (id, title, project_id, created_at, updated_at) "
        "VALUES ('si_delete_parent', 'Delete parent', 'project_vylo', ?, ?)",
        (now, now),
    )
    before = _create(tmp_db, cfg, fake_clock, "Before")
    blocked_item = _create(
        tmp_db,
        cfg,
        fake_clock,
        "Blocked item source",
    )
    target = _create(
        tmp_db,
        cfg,
        fake_clock,
        "Mistaken ticket",
        sprint_item_id="si_delete_parent",
    )
    after = _create(tmp_db, cfg, fake_clock, "After")

    day_id = "day_2026-07-04"
    for ticket in (before, target, after):
        days_data.add_day_ticket(tmp_db, day_id, ticket.id, now)
    core_links.add_link(tmp_db, target.id, after.id, LinkKind.blocks, now)
    core_links.add_link(tmp_db, before.id, target.id, LinkKind.blocks, now)
    core_links.add_link(tmp_db, blocked_item.id, "si_delete_parent", LinkKind.blocks, now)
    core_links.add_link(tmp_db, target.id, "si_delete_parent", LinkKind.blocks, now)

    deleted = tickets_data.delete_ticket(tmp_db, target.id, actor="human", now=now)

    assert deleted.ticket_id == target.id
    assert deleted.title == "Mistaken ticket"
    assert deleted.day_ids == (day_id,)
    assert deleted.sprint_item_ids == ("si_delete_parent",)
    assert deleted.linked_entity_ids == tuple(sorted((before.id, after.id, "si_delete_parent")))

    with pytest.raises(PlannerError) as exc:
        tickets_data.read_ticket(tmp_db, target.id)
    assert exc.value.code is ErrorCode.not_found
    assert [
        tuple(row)
        for row in tmp_db.execute(
            "SELECT ticket_id, position FROM day_tickets WHERE day_id = ? ORDER BY position",
            (day_id,),
        )
    ] == [(before.id, 0), (after.id, 1)]
    assert (
        tmp_db.execute(
            "SELECT 1 FROM links WHERE from_id = ? OR to_id = ?", (target.id, target.id)
        ).fetchone()
        is None
    )
    # Nothing anywhere still refers to the deleted Ticket.
    assert tmp_db.execute(
        "SELECT 1 FROM day_tickets WHERE ticket_id = ?", (target.id,)
    ).fetchone() is None
    assert tmp_db.execute(
        "SELECT 1 FROM pending_worker_context WHERE worker_entity_id = ?", (target.id,)
    ).fetchone() is None
    assert tickets_data.read_ticket(tmp_db, before.id).ticket_status is not None
    assert tickets_data.read_ticket(tmp_db, after.id).ticket_status is not None


def test_delete_ticket_rejects_agent_and_each_active_worker_invariant(
    tmp_db: Connection, cfg: Config, fake_clock: PlannerTestClock
) -> None:
    now = fake_clock.now_unix()
    agent_target = _create(tmp_db, cfg, fake_clock, "Agent cannot delete")
    with pytest.raises(PlannerError) as agent_exc:
        tickets_data.delete_ticket(tmp_db, agent_target.id, actor="agent", now=now)
    assert agent_exc.value.code is ErrorCode.agent_forbidden

    controlled = _create(tmp_db, cfg, fake_clock, "Controlled running")
    planning_day_id = "day_2099-01-01"
    days_data.add_day_ticket(tmp_db, planning_day_id, controlled.id, now)
    assert tickets_data.claim_ticket_for_worker_step(
        tmp_db,
        controlled.id,
        planning_day_id_resolver=lambda: planning_day_id,
        readiness_check=worker_step_readiness.is_ready_for_worker_step,
        now=now,
    )
    with pytest.raises(PlannerError) as controlled_exc:
        tickets_data.delete_ticket(tmp_db, controlled.id, actor="human", now=now)
    assert controlled_exc.value.code is ErrorCode.already_running
    assert tickets_data.read_ticket(tmp_db, controlled.id).id == controlled.id

    # Force skips the status guard only. An agent is still refused.
    with pytest.raises(PlannerError) as forced_agent_exc:
        tickets_data.delete_ticket(
            tmp_db, controlled.id, actor="agent", now=now, force=True
        )
    assert forced_agent_exc.value.code is ErrorCode.agent_forbidden

    deleted = tickets_data.delete_ticket(
        tmp_db, controlled.id, actor="human", now=now, force=True
    )
    assert deleted.ticket_id == controlled.id
    assert tmp_db.execute(
        "SELECT 1 FROM tickets WHERE id = ?", (controlled.id,)
    ).fetchone() is None


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "delete-api.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
        },
    )
    clock = build_clock(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return (
        create_app(
            config,
            clock,
            conn_factory,
            # The delete route asks the conversation system whether a turn is running, so
            # this file needs one whose running turn it can start and end by hand.
            conversation_system_for_test=InMemoryConversationSystem(),
        ),
        db_path,
    )


def test_delete_route_refuses_a_ticket_whose_conversation_is_running(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    conn = connect(str(db_path))
    target = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Live worker",
        actor="human",
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    conn.execute(
        "UPDATE tickets SET conversation_id = ? WHERE id = ?",
        ("conv-live", target.id),
    )
    conn.commit()
    conn.close()

    with TestClient(app) as client:
        asyncio.run(
            app.state.conversation_system.start_conversation(
                ConversationStartRequest(conversation_id="conv-live", model="a-model")
            )
        )
        asyncio.run(
            app.state.conversation_system.send(
                "conv-live",
                text_message_content("working"),
                sender_label="loop",
            )
        )
        blocked = client.delete(f"/api/tickets/{target.id}")
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "already_running"

        # The turn ends; the same Ticket deletes cleanly.
        app.state.conversation_system.complete_running_turn("conv-live")
        deleted = client.delete(f"/api/tickets/{target.id}")
        assert deleted.status_code == 200, deleted.text


def test_delete_route_force_deletes_a_stuck_ticket_that_still_looks_running(
    tmp_path: Path,
) -> None:
    """The reported incident: status stranded at `agent` and a conversation that still
    reports a running turn. Both guards refuse; force deletes it anyway."""
    app, db_path = _make_app(tmp_path)
    conn = connect(str(db_path))
    target = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Stuck worker",
        actor="human",
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    conn.execute(
        "UPDATE tickets SET conversation_id = ?, ticket_status = ? WHERE id = ?",
        ("conv-stuck", "agent", target.id),
    )
    conn.commit()
    conn.close()

    with TestClient(app) as client:
        asyncio.run(
            app.state.conversation_system.start_conversation(
                ConversationStartRequest(conversation_id="conv-stuck", model="a-model")
            )
        )
        asyncio.run(
            app.state.conversation_system.send(
                "conv-stuck",
                text_message_content("working"),
                sender_label="loop",
            )
        )
        blocked = client.delete(f"/api/tickets/{target.id}")
        assert blocked.status_code == 409
        assert blocked.json()["error"]["code"] == "already_running"

        # Force is still human-only.
        forbidden = client.delete(
            f"/api/tickets/{target.id}?force=true", headers={"X-Plan-Actor": "agent"}
        )
        assert forbidden.status_code == 400
        assert forbidden.json()["error"]["code"] == "agent_forbidden"

        forced = client.delete(f"/api/tickets/{target.id}?force=true")
        assert forced.status_code == 200, forced.text
        assert client.get(f"/api/tickets/{target.id}").status_code == 404


def test_delete_ticket_api_is_human_only_and_returns_affected_resources(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    conn = connect(str(db_path))
    target = tickets_data.create_ticket(
        conn,
        worker_type="coding",
        title="Delete through API",
        actor="human",
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    days_data.add_day_ticket(conn, "day_2026-07-04", target.id, 1)
    conn.close()

    signals = 0

    def record() -> None:
        nonlocal signals
        signals += 1

    unsubscribe = change_signal.subscribe(record)
    with TestClient(app) as client:
        forbidden = client.delete(f"/api/tickets/{target.id}", headers={"X-Plan-Actor": "agent"})
        assert forbidden.status_code == 400
        assert forbidden.json()["error"]["code"] == "agent_forbidden"

        response = client.delete(f"/api/tickets/{target.id}")
        assert response.status_code == 200, response.text
        assert response.json() == {
            "ok": True,
            "ticket_id": target.id,
            "title": "Delete through API",
            "day_ids": ["day_2026-07-04"],
            "sprint_item_ids": [],
            "sprint_ids": [],
            "linked_entity_ids": [],
        }
        assert client.get(f"/api/tickets/{target.id}").status_code == 404
    unsubscribe()
    assert signals == 1
