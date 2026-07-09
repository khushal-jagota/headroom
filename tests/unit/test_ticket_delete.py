from __future__ import annotations

import json
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.chat import data as chat_data
from planner.core import links as core_links
from planner.core.adapters.registry import build_adapters
from planner.core.clock import TestClock as PlannerTestClock
from planner.core.clock import build_clock
from planner.core.config import Config, load_config
from planner.core.contracts import EventKind, LinkKind
from planner.core.db import connect, create_schema
from planner.core.errors import ErrorCode, PlannerError
from planner.core.server import create_app
from planner.days import data as days_data
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS


def _create(
    conn: Connection,
    cfg: Config,
    clock: PlannerTestClock,
    title: str,
    *,
    sprint_item_id: str | None = None,
):
    return tickets_data.create_ticket(
        conn,
        title=title,
        actor="human",
        now=clock.now_unix(),
        title_max_chars=TITLE_MAX_CHARS,
        sprint_item_id=sprint_item_id,
    )


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
    core_links.add_link(tmp_db, before.id, target.id, LinkKind.relates, now)

    turn = chat_data.start_turn(
        tmp_db,
        target.id,
        origin="human",
        mode="message",
        visible_role="human",
        visible_text="mistaken chat",
        output_role="assistant",
        phase="thinking",
        activity_label="Thinking",
        now=now,
    )
    chat_data.finish_turn(
        tmp_db,
        turn.id,
        entity_id=target.id,
        reply_text="old reply",
        output_role="assistant",
        now=now,
    )

    deleted = tickets_data.delete_ticket(tmp_db, target.id, actor="human", now=now)

    assert deleted.ticket_id == target.id
    assert deleted.title == "Mistaken ticket"
    assert deleted.day_ids == (day_id,)
    assert deleted.sprint_item_ids == ("si_delete_parent",)
    assert deleted.linked_entity_ids == tuple(sorted((before.id, after.id)))

    with pytest.raises(PlannerError) as exc:
        tickets_data.read_ticket(tmp_db, target.id)
    assert exc.value.code is ErrorCode.not_found
    assert [tuple(row) for row in tmp_db.execute(
        "SELECT ticket_id, position FROM day_tickets WHERE day_id = ? ORDER BY position",
        (day_id,),
    )] == [(before.id, 0), (after.id, 1)]
    assert tmp_db.execute(
        "SELECT 1 FROM links WHERE from_id = ? OR to_id = ?", (target.id, target.id)
    ).fetchone() is None
    assert tmp_db.execute(
        "SELECT 1 FROM chat_messages WHERE entity_id = ?", (target.id,)
    ).fetchone() is None
    assert tmp_db.execute(
        "SELECT 1 FROM chat_turns WHERE entity_id = ?", (target.id,)
    ).fetchone() is None

    target_events = tmp_db.execute(
        "SELECT kind, payload, created_at FROM events WHERE entity_id = ? ORDER BY id",
        (target.id,),
    ).fetchall()
    assert len(target_events) == 1
    assert target_events[0]["kind"] == EventKind.ticket_deleted.value
    assert json.loads(target_events[0]["payload"]) == {
        "ticket_id": target.id,
        "title": "Mistaken ticket",
        "actor": "human",
    }
    assert target_events[0]["created_at"] == now

    prior_reference_events = tmp_db.execute(
        "SELECT kind, payload FROM events WHERE kind IN ('day_ticket_added', 'link_added')"
    ).fetchall()
    for event in prior_reference_events:
        assert target.id not in json.loads(event["payload"]).values()

    day_event = tmp_db.execute(
        "SELECT payload FROM events WHERE entity_id = ? AND kind = 'day_ticket_removed' "
        "ORDER BY id DESC LIMIT 1",
        (day_id,),
    ).fetchone()
    assert json.loads(day_event["payload"]) == {"ticket_id": target.id}
    item_event = tmp_db.execute(
        "SELECT payload FROM events WHERE entity_id = 'si_delete_parent' "
        "AND kind = 'item_children_changed' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert json.loads(item_event["payload"]) == {"ticket_id": target.id, "reason": "deleted"}
    for survivor_id in (before.id, after.id):
        event = tmp_db.execute(
            "SELECT payload FROM events WHERE entity_id = ? AND kind = 'link_removed' "
            "ORDER BY id DESC LIMIT 1",
            (survivor_id,),
        ).fetchone()
        assert event is not None
        assert target.id in json.loads(event["payload"]).values()


def test_delete_ticket_rejects_agent_and_each_active_worker_invariant(
    tmp_db: Connection, cfg: Config, fake_clock: PlannerTestClock
) -> None:
    now = fake_clock.now_unix()
    agent_target = _create(tmp_db, cfg, fake_clock, "Agent cannot delete")
    with pytest.raises(PlannerError) as agent_exc:
        tickets_data.delete_ticket(tmp_db, agent_target.id, actor="agent", now=now)
    assert agent_exc.value.code is ErrorCode.agent_forbidden

    controlled = _create(tmp_db, cfg, fake_clock, "Controlled running")
    assert tickets_data.start_run_if_runnable(tmp_db, controlled.id, guard=None, now=now)
    with pytest.raises(PlannerError) as controlled_exc:
        tickets_data.delete_ticket(tmp_db, controlled.id, actor="human", now=now)
    assert controlled_exc.value.code is ErrorCode.already_running
    assert tickets_data.read_ticket(tmp_db, controlled.id).id == controlled.id

    turn_target = _create(tmp_db, cfg, fake_clock, "Human chat running")
    chat_data.start_turn(
        tmp_db,
        turn_target.id,
        origin="human",
        mode="message",
        visible_role="human",
        visible_text="still chatting",
        output_role="assistant",
        phase="thinking",
        activity_label="Thinking",
        now=now,
    )
    with pytest.raises(PlannerError) as turn_exc:
        tickets_data.delete_ticket(tmp_db, turn_target.id, actor="human", now=now)
    assert turn_exc.value.code is ErrorCode.already_running
    assert tickets_data.read_ticket(tmp_db, turn_target.id).id == turn_target.id


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "delete-api.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_GATEWAY_ADAPTER": "fake",
            "PLAN_DB_PATH": str(db_path),
        },
    )
    clock = build_clock(config)
    adapters = build_adapters(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    return create_app(config, clock, adapters, conn_factory), db_path


class _PokeSpy:
    def __init__(self) -> None:
        self.calls = 0

    def poke(self) -> None:
        self.calls += 1


def test_delete_ticket_api_is_human_only_and_returns_affected_resources(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    conn = connect(str(db_path))
    target = tickets_data.create_ticket(
        conn,
        title="Delete through API",
        actor="human",
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    days_data.add_day_ticket(conn, "day_2026-07-04", target.id, 1)
    conn.close()

    with TestClient(app) as client:
        poke_spy = _PokeSpy()
        app.state.system_a = poke_spy
        forbidden = client.delete(
            f"/api/tickets/{target.id}", headers={"X-Plan-Actor": "agent"}
        )
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
        assert poke_spy.calls == 1
