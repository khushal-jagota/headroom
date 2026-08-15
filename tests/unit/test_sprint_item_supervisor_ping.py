"""The one deliberate act a Sprint Item supervisor takes when it wants the user."""

from __future__ import annotations

import json
from pathlib import Path
from sqlite3 import Connection
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.core.errors import PlannerError
from planner.notifications import data as notifications_data
from planner.sprints import data as sprints_data
from planner.tickets import views as tickets_views


def _app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "data" / "planning.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
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


def _supervisor_headers(item_id: str) -> dict[str, str]:
    return {"X-Plan-Actor": "sprint_item_supervisor", "X-Plan-Sprint-Item-ID": item_id}


def _create_item(client: TestClient, title: str = "Fix Sprint Item issues") -> dict[str, Any]:
    response = client.post("/api/items", json={"title": title, "project_id": "project_vylo"})
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _give_the_supervisor_a_conversation(
    conn: Connection, item_id: str, *, latest_sequence: int
) -> str:
    conversation_id = f"conv-supervisor-{item_id}"
    conn.execute(
        "INSERT INTO conversations"
        "(conversation_id, backend_key, workspace_folder, access, latest_sequence, created_at) "
        "VALUES (?, 'codex', '/tmp/workspace', 'full', ?, 1)",
        (conversation_id, latest_sequence),
    )
    conn.execute(
        "UPDATE agents SET conversation_id = ? WHERE agent_key = ?",
        (conversation_id, sprints_data.supervisor_agent_key(item_id)),
    )
    conn.commit()
    return conversation_id


def _record_turn_ending(conn: Connection, conversation_id: str, sequence: int, ending: str) -> None:
    conn.execute(
        "INSERT INTO conversation_events"
        "(conversation_id, sequence, kind, payload, created_at) "
        "VALUES (?, ?, 'turn_ended', ?, 5)",
        (conversation_id, sequence, json.dumps({"ending": ending, "error_summary": None})),
    )
    conn.execute(
        "UPDATE conversations SET latest_sequence = ? WHERE conversation_id = ?",
        (sequence, conversation_id),
    )
    conn.commit()


def _intents(conn: Connection) -> dict[str, dict[str, str]]:
    return {
        str(row["fact_id"]): {
            "title": str(row["title"]),
            "body": str(row["body"]),
            "route": str(row["route"]),
            "tag": str(row["tag"]),
        }
        for row in conn.execute("SELECT fact_id, title, body, route, tag FROM notification_intents")
    }


def test_a_ping_is_stored_past_the_last_row_so_a_current_reader_still_sees_it(
    tmp_path: Path,
) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item_id = str(_create_item(client)["id"])
        conn = connect(str(db_path))
        _give_the_supervisor_a_conversation(conn, item_id, latest_sequence=7)

        pinged = client.post(
            f"/api/items/{item_id}/supervisor/ping", headers=_supervisor_headers(item_id)
        )
        assert pinged.status_code == 200, pinged.text

        # A reader who is up to date sits on row 7. A ping stored there would be born
        # read, so it is stored at the position the next row will take.
        assert pinged.json()["ping_sequence"] == 8
        assert _stored_ping(conn, item_id) == 8

        # A second ping replaces the first: one Item asking twice is still one Item.
        _record_turn_ending(conn, f"conv-supervisor-{item_id}", 12, "completed")
        again = client.post(
            f"/api/items/{item_id}/supervisor/ping", headers=_supervisor_headers(item_id)
        )
        assert again.status_code == 200, again.text
        assert again.json()["ping_sequence"] == 13
        assert _stored_ping(conn, item_id) == 13
        conn.close()


def test_the_board_row_carries_the_ping_and_a_supervisor_without_one_reads_zero(
    tmp_path: Path,
) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item_id = str(_create_item(client)["id"])
        created = client.post(
            "/api/tickets",
            json={
                "worker_type": "coding",
                "title": "Its ticket",
                "kickoff_note": "Start here.",
                "sprint_item_id": item_id,
            },
        )
        assert created.status_code == 200, created.text
        conn = connect(str(db_path))
        _give_the_supervisor_a_conversation(conn, item_id, latest_sequence=3)

        day_id = str(client.get("/api/day/today").json()["id"])
        before = tickets_views.board_view(conn, day_id=day_id)["sprint_items"]
        assert [row["latest_ping_sequence"] for row in before] == [0]

        client.post(f"/api/items/{item_id}/supervisor/ping", headers=_supervisor_headers(item_id))

        after = tickets_views.board_view(conn, day_id=day_id)["sprint_items"]
        assert [row["latest_ping_sequence"] for row in after] == [4]
        conn.close()


def test_a_ping_becomes_one_push_that_names_the_item_and_opens_it(tmp_path: Path) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item = _create_item(client, title="Fix Sprint Item issues")
        item_id = str(item["id"])
        conn = connect(str(db_path))
        _give_the_supervisor_a_conversation(conn, item_id, latest_sequence=7)
        client.post(f"/api/items/{item_id}/supervisor/ping", headers=_supervisor_headers(item_id))

        notifications_data.project_facts(conn)
        assert notifications_data.apply_policy(conn, 9) == 1

        fact_id = f"sprint_item_ping:{item_id}:8"
        assert _intents(conn)[fact_id] == {
            "title": "Panels",
            "body": "Fix Sprint Item issues wants you.",
            "route": f"/#/workspace/item/{item_id}",
            "tag": f"panels-sprint_item-{item_id}",
        }

        # The same ping is the same fact, however often the projection runs.
        notifications_data.project_facts(conn)
        assert notifications_data.apply_policy(conn, 10) == 0
        conn.close()


def test_supervisor_turns_do_not_reach_the_user_unless_they_failed_and_the_switch_is_on(
    tmp_path: Path,
) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item_id = str(_create_item(client, title="Fix Sprint Item issues")["id"])
        conn = connect(str(db_path))
        conversation_id = _give_the_supervisor_a_conversation(conn, item_id, latest_sequence=0)
        notifications_data.project_facts(conn)
        notifications_data.apply_policy(conn, 1)

        _record_turn_ending(conn, conversation_id, 1, "completed")
        _record_turn_ending(conn, conversation_id, 2, "failed")
        notifications_data.project_facts(conn)
        assert notifications_data.apply_policy(conn, 3) == 2

        # A completed turn has no switch to be on, and failures arrive off.
        assert _intents(conn) == {}

        notifications_data.set_preference(conn, "sprint_item_supervisors", "worker_failed", True, 4)
        _record_turn_ending(conn, conversation_id, 3, "completed")
        _record_turn_ending(conn, conversation_id, 4, "failed")
        notifications_data.project_facts(conn)
        assert notifications_data.apply_policy(conn, 5) == 2

        # The failure that got through is the Item's, not the agent's: same label, same
        # destination, same tag as its ping.
        assert _intents(conn) == {
            f"conversation:{conversation_id}:4": {
                "title": "Panels",
                "body": "Fix Sprint Item issues has a failed worker reply.",
                "route": f"/#/workspace/item/{item_id}",
                "tag": f"panels-sprint_item-{item_id}",
            }
        }
        conn.close()


def test_a_supervisor_nobody_has_spoken_to_has_no_conversation_to_ping_from(
    tmp_path: Path,
) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        item_id = str(_create_item(client)["id"])
        refused = client.post(
            f"/api/items/{item_id}/supervisor/ping", headers=_supervisor_headers(item_id)
        )
        assert refused.status_code == 404, refused.text

        conn = connect(str(db_path))
        assert _stored_ping(conn, item_id) is None
        conn.close()


def test_only_this_items_supervisor_can_ping_it(tmp_path: Path) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app) as client:
        mine = str(_create_item(client, title="Mine")["id"])
        other = str(_create_item(client, title="Not mine")["id"])
        conn = connect(str(db_path))
        _give_the_supervisor_a_conversation(conn, mine, latest_sequence=1)

        refused = client.post(
            f"/api/items/{mine}/supervisor/ping", headers=_supervisor_headers(other)
        )
        assert refused.status_code == 400, refused.text
        assert refused.json()["error"]["code"] == "agent_forbidden"
        assert _stored_ping(conn, mine) is None
        conn.close()


def test_the_ping_service_rejects_an_item_that_does_not_exist(tmp_path: Path) -> None:
    app, db_path = _app(tmp_path)
    with TestClient(app):
        pass
    conn = connect(str(db_path))
    from planner.core.authctx import RequestContext
    from planner.core.clock import RealClock
    from planner.sprints import supervisor_service

    ctx = RequestContext(
        actor="sprint_item_supervisor",
        is_attributed=True,
        is_chief=False,
        sprint_item_id="si_missing",
    )
    with pytest.raises(PlannerError):
        supervisor_service.ping(conn, ctx, "si_missing", clock=RealClock())
    conn.close()


def _stored_ping(conn: Connection, item_id: str) -> int | None:
    row = conn.execute(
        "SELECT supervisor_ping_sequence FROM sprint_items WHERE id = ?", (item_id,)
    ).fetchone()
    assert row is not None
    value = row["supervisor_ping_sequence"]
    return None if value is None else int(value)
