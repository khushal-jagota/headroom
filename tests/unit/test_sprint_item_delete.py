from __future__ import annotations

import json
from sqlite3 import Connection

import pytest

from planner.core import links as core_links
from planner.core.clock import TestClock as PlannerTestClock
from planner.core.contracts import EventKind, LinkKind
from planner.core.errors import ErrorCode, PlannerError
from planner.sprints import data as sprints_data
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS


def _item(
    conn: Connection,
    clock: PlannerTestClock,
    title: str,
    *,
    sprint_id: str | None = None,
):
    return sprints_data.create_item(
        conn,
        title=title,
        project_id="project_vylo",
        sprint_id=sprint_id,
        clock=clock,
    )


@pytest.mark.parametrize("in_sprint", [False, True])
def test_delete_item_removes_sprint_or_backlog_item_and_keeps_minimal_audit(
    tmp_db: Connection,
    fake_clock: PlannerTestClock,
    in_sprint: bool,
) -> None:
    now = fake_clock.now_unix()
    sprint_id: str | None = None
    if in_sprint:
        sprint_id = sprints_data.create_sprint(
            tmp_db,
            name="Delete item sprint",
            date_start="2026-08-01",
            date_end="2026-08-14",
            clock=fake_clock,
        ).id
    item = _item(tmp_db, fake_clock, "Redundant item", sprint_id=sprint_id)
    blocker = tickets_data.create_ticket(
        tmp_db,
        worker_type="coding",
        title="Surviving blocker",
        actor="human",
        now=now,
        title_max_chars=TITLE_MAX_CHARS,
    )
    core_links.add_link(tmp_db, blocker.id, item.id, LinkKind.blocks, now)

    deleted = sprints_data.delete_item(
        tmp_db,
        item.id,
        actor="human",
        clock=fake_clock,
    )

    assert deleted.sprint_item_id == item.id
    assert deleted.title == "Redundant item"
    assert deleted.sprint_ids == ((sprint_id,) if sprint_id is not None else ())
    assert deleted.linked_entity_ids == (blocker.id,)
    with pytest.raises(PlannerError) as exc:
        sprints_data.read_item(tmp_db, item.id)
    assert exc.value.code is ErrorCode.not_found
    assert tmp_db.execute(
        "SELECT 1 FROM links WHERE from_id = ? OR to_id = ?", (item.id, item.id)
    ).fetchone() is None

    item_events = tmp_db.execute(
        "SELECT kind, payload, created_at FROM events WHERE entity_id = ? ORDER BY id",
        (item.id,),
    ).fetchall()
    assert len(item_events) == 1
    assert item_events[0]["kind"] == EventKind.sprint_item_deleted.value
    assert json.loads(item_events[0]["payload"]) == {
        "sprint_item_id": item.id,
        "title": "Redundant item",
        "actor": "human",
        "sprint_id": sprint_id,
    }
    assert item_events[0]["created_at"] == now
    assert all(
        item.id not in json.loads(row["payload"]).values()
        for row in tmp_db.execute(
            "SELECT payload FROM events WHERE kind = 'link_added'"
        ).fetchall()
    )
    survivor_event = tmp_db.execute(
        "SELECT payload FROM events WHERE entity_id = ? AND kind = 'link_removed' "
        "ORDER BY id DESC LIMIT 1",
        (blocker.id,),
    ).fetchone()
    assert survivor_event is not None
    assert json.loads(survivor_event["payload"]) == {
        "from_id": blocker.id,
        "to_id": item.id,
        "kind": "blocks",
    }


def test_delete_item_refuses_ordered_child_tickets_without_changes(
    tmp_db: Connection,
    fake_clock: PlannerTestClock,
) -> None:
    item = _item(tmp_db, fake_clock, "Item with work")
    child_ids = []
    for title in ("Zulu child", "Alpha child"):
        child_ids.append(
            tickets_data.create_ticket(
                tmp_db,
                worker_type="coding",
                title=title,
                actor="human",
                now=fake_clock.now_unix(),
                title_max_chars=TITLE_MAX_CHARS,
                sprint_item_id=item.id,
            ).id
        )
    before_events = tuple(
        tuple(row)
        for row in tmp_db.execute(
            "SELECT entity_id, kind, payload, created_at FROM events ORDER BY id"
        ).fetchall()
    )

    with pytest.raises(PlannerError) as exc:
        sprints_data.delete_item(
            tmp_db,
            item.id,
            actor="human",
            clock=fake_clock,
        )

    assert exc.value.code is ErrorCode.validation
    assert exc.value.message == "sprint item has child tickets"
    assert exc.value.detail == {
        "sprint_item_id": item.id,
        "ticket_ids": sorted(child_ids),
    }
    assert sprints_data.read_item(tmp_db, item.id).item.id == item.id
    assert tuple(
        tuple(row)
        for row in tmp_db.execute(
            "SELECT entity_id, kind, payload, created_at FROM events ORDER BY id"
        ).fetchall()
    ) == before_events


def test_delete_item_rejects_agent_and_missing_item(
    tmp_db: Connection,
    fake_clock: PlannerTestClock,
) -> None:
    item = _item(tmp_db, fake_clock, "Direct only")
    with pytest.raises(PlannerError) as agent_exc:
        sprints_data.delete_item(
            tmp_db,
            item.id,
            actor="agent",
            clock=fake_clock,
        )
    assert agent_exc.value.code is ErrorCode.agent_forbidden
    assert sprints_data.read_item(tmp_db, item.id).item.id == item.id

    with pytest.raises(PlannerError) as missing_exc:
        sprints_data.delete_item(
            tmp_db,
            "si_missing",
            actor="human",
            clock=fake_clock,
        )
    assert missing_exc.value.code is ErrorCode.not_found
    assert missing_exc.value.detail == {"id": "si_missing"}


def test_delete_item_rolls_back_when_audit_append_fails(
    tmp_db: Connection,
    fake_clock: PlannerTestClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _item(tmp_db, fake_clock, "Rollback item")
    before_events = tuple(
        tuple(row)
        for row in tmp_db.execute(
            "SELECT entity_id, kind, payload, created_at FROM events ORDER BY id"
        ).fetchall()
    )
    original_append_event = sprints_data.append_event

    def fail_deletion_audit(conn, entity_id, kind, payload, created_at):
        if kind is EventKind.sprint_item_deleted:
            raise RuntimeError("audit unavailable")
        return original_append_event(conn, entity_id, kind, payload, created_at)

    monkeypatch.setattr(sprints_data, "append_event", fail_deletion_audit)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        sprints_data.delete_item(
            tmp_db,
            item.id,
            actor="human",
            clock=fake_clock,
        )

    assert sprints_data.read_item(tmp_db, item.id).item.id == item.id
    assert tuple(
        tuple(row)
        for row in tmp_db.execute(
            "SELECT entity_id, kind, payload, created_at FROM events ORDER BY id"
        ).fetchall()
    ) == before_events
