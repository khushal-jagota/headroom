"""Deleting a Sprint Item.

Permanent, direct-only, and refused while any Ticket still hangs off it. What the caller
gets back is the footprint that just changed — the item, the sprints it sat in, and every
entity that was linked to it — because those are the screens the removal is visible on.

Nothing is written down about the removal itself. The commit announces itself, so there is
no audit row to assert and none of these tests looks for one.
"""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

import pytest

from planner.core import links as core_links
from planner.core.clock import TestClock as PlannerTestClock
from planner.core.contracts import LinkKind
from planner.core.errors import ErrorCode, PlannerError
from planner.sprints import data as sprints_data
from planner.sprints.contracts import SprintItem
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS


def _item(
    conn: Connection,
    clock: PlannerTestClock,
    title: str,
    *,
    sprint_id: str | None = None,
) -> SprintItem:
    return sprints_data.create_item(
        conn,
        title=title,
        project_id="project_vylo",
        sprint_id=sprint_id,
        clock=clock,
    )


@pytest.mark.parametrize("in_sprint", [False, True])
def test_delete_item_removes_the_item_and_its_links_and_names_what_changed(
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

    deleted = sprints_data.delete_item(tmp_db, item.id, actor="human")

    assert deleted.sprint_item_id == item.id
    assert deleted.title == "Redundant item"
    assert deleted.sprint_ids == ((sprint_id,) if sprint_id is not None else ())
    assert deleted.linked_entity_ids == (blocker.id,)

    with pytest.raises(PlannerError) as exc:
        sprints_data.read_item(tmp_db, item.id)
    assert exc.value.code is ErrorCode.not_found
    # The link went with it, so nothing left points at an item that is gone.
    assert (
        tmp_db.execute(
            "SELECT 1 FROM links WHERE from_id = ? OR to_id = ?", (item.id, item.id)
        ).fetchone()
        is None
    )
    # And the thing on the other end of that link is untouched.
    assert tickets_data.read_ticket(tmp_db, blocker.id).id == blocker.id


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

    with pytest.raises(PlannerError) as exc:
        sprints_data.delete_item(tmp_db, item.id, actor="human")

    assert exc.value.code is ErrorCode.validation
    assert exc.value.message == "sprint item has child tickets"
    assert exc.value.detail == {
        "sprint_item_id": item.id,
        "ticket_ids": sorted(child_ids),
    }
    # Refused means nothing moved: the item is still there and so is every child.
    assert sprints_data.read_item(tmp_db, item.id).item.id == item.id
    for child_id in child_ids:
        assert tickets_data.read_ticket(tmp_db, child_id).sprint_item_id == item.id


def test_delete_item_rejects_agent_and_missing_item(
    tmp_db: Connection,
    fake_clock: PlannerTestClock,
) -> None:
    item = _item(tmp_db, fake_clock, "Direct only")
    with pytest.raises(PlannerError) as agent_exc:
        sprints_data.delete_item(tmp_db, item.id, actor="agent")
    assert agent_exc.value.code is ErrorCode.agent_forbidden
    assert sprints_data.read_item(tmp_db, item.id).item.id == item.id

    with pytest.raises(PlannerError) as missing_exc:
        sprints_data.delete_item(tmp_db, "si_missing", actor="human")
    assert missing_exc.value.code is ErrorCode.not_found
    assert missing_exc.value.detail == {"id": "si_missing"}


def test_delete_item_removes_agent_and_files_but_keeps_conversation_history(
    tmp_db: Connection,
    fake_clock: PlannerTestClock,
    tmp_path: Path,
) -> None:
    item = _item(tmp_db, fake_clock, "With files")
    conversation_id = "conv_retained"
    tmp_db.execute(
        "INSERT INTO conversations(conversation_id,backend_key,model,workspace_folder,"
        "access,created_at) VALUES (?, 'codex', 'model', '/tmp', 'full', 1)",
        (conversation_id,),
    )
    tmp_db.execute(
        "UPDATE agents SET conversation_id=? WHERE agent_key=?",
        (conversation_id, item.supervisor_agent_key),
    )
    db_path = Path(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    managed = db_path.parent / "files" / "sprint-items" / item.id / "brief.md"
    managed.parent.mkdir(parents=True)
    managed.write_text("brief", encoding="utf-8")

    sprints_data.delete_item(tmp_db, item.id, actor="human")

    assert not managed.exists()
    assert (
        tmp_db.execute(
            "SELECT 1 FROM agents WHERE agent_key=?", (item.supervisor_agent_key,)
        ).fetchone()
        is None
    )
    assert (
        tmp_db.execute(
            "SELECT 1 FROM conversations WHERE conversation_id=?", (conversation_id,)
        ).fetchone()
        is not None
    )


def test_delete_item_restores_quarantined_files_when_the_database_refuses(
    tmp_db: Connection,
    fake_clock: PlannerTestClock,
) -> None:
    item = _item(tmp_db, fake_clock, "Restore files")
    db_path = Path(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    managed = db_path.parent / "files" / "sprint-items" / item.id / "brief.md"
    managed.parent.mkdir(parents=True)
    managed.write_text("brief", encoding="utf-8")
    tmp_db.execute(
        "CREATE TRIGGER refuse_item_delete BEFORE DELETE ON sprint_items "
        "BEGIN SELECT RAISE(ABORT, 'refused for test'); END"
    )

    with pytest.raises(Exception, match="refused for test"):
        sprints_data.delete_item(tmp_db, item.id, actor="human")

    assert managed.read_text(encoding="utf-8") == "brief"
    assert sprints_data.read_item(tmp_db, item.id).item.id == item.id
