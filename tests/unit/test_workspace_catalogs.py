"Bounded catalogs preserve source ordering, discovery, and hidden-history access."

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Request
from tests.support.principals import OWNER_PRINCIPAL

from planner.conversation.history import data as history_data
from planner.conversation.history.api import _read as history_page
from planner.core.db import connect
from planner.files.catalog import links
from planner.files.catalog.api import _cache
from planner.files.catalog.api import _read as artifact_page
from planner.files.catalog.config import PAGE_MAX_BYTES
from planner.goals.actions import create_goal
from planner.tickets.data import create_ticket


def _request(conn: sqlite3.Connection) -> Request:
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    app = FastAPI()
    app.state.config = SimpleNamespace(db_path=path)
    app.state.conn_factory = lambda: connect(path)
    return Request(
        {
            "type": "http",
            "scheme": "http",
            "server": ("testserver", 80),
            "headers": [],
            "path": "/",
            "app": app,
        }
    )


def _install(conn: sqlite3.Connection) -> None:
    if (
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='ticket_artifact_links'").fetchone()
        is None
    ):
        links.install(conn)
    if (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='conversation_history_catalog'"
        ).fetchone()
        is None
    ):
        history_data.install(conn)


def _ticket(conn: sqlite3.Connection) -> str:
    return create_ticket(
        conn,
        title="Catalog",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    ).id


def test_link_index_parses_images_references_previews_and_excludes_code(
    tmp_db: sqlite3.Connection,
) -> None:
    ticket = _ticket(tmp_db)
    _install(tmp_db)
    source = f"""[inline](/files/tickets/{ticket}/a.md)
![image](/files/tickets/{ticket}/b.png)
[reference][r]

[r]: /files/tickets/{ticket}/c.md

[preview](#/preview?source=ticket&ticket={ticket}&path=d.md)
http://testserver/files/tickets/{ticket}/e.md
`[code](/files/tickets/{ticket}/wrong.md)`
"""
    tmp_db.execute("BEGIN")
    tmp_db.execute(
        "UPDATE tickets SET field_values=json_set(field_values,'$.brief',?) WHERE id=?",
        (source, ticket),
    )
    links.finalize(tmp_db)
    tmp_db.commit()
    targets = [
        row[0]
        for row in tmp_db.execute(
            "SELECT target FROM ticket_artifact_links WHERE ticket_id=? ORDER BY ordinal", (ticket,)
        )
    ]
    assert targets == [
        f"/files/tickets/{ticket}/{name}" for name in ("a.md", "b.png", "c.md", "d.md", "e.md")
    ]
    assert links.managed_href(f"/files/tickets/{ticket}/../escape.md", "http://testserver") is None
    assert links.managed_href(
        f"http://TESTSERVER:80/files/tickets/{ticket}/a.md", "http://testserver"
    ) == f"/files/tickets/{ticket}/a.md"
    for retained in (
        "/files/sprint-items/si_existing/plan.md",
        "#/preview?source=sprint-item&item=si_existing&path=plan.md",
        "#/preview?source=manager&manager=si_existing&path=plan.md",
    ):
        assert links.managed_href(retained, "http://testserver") == (
            "/files/managers/si_existing/plan.md"
        )
    assert links.managed_href(f"/files/tickets/{ticket}/%FF.md", "http://testserver") is None
    assert links.managed_href(f"/files/tickets/{ticket}/%ZZ.md", "http://testserver") is None
    assert (
        links.managed_href(f"https://foreign/files/tickets/{ticket}/a.md", "http://testserver")
        is None
    )


def test_artifact_pages_preserve_filesystem_priority_links_and_direct_write_discovery(
    tmp_db: sqlite3.Connection,
) -> None:
    ticket = _ticket(tmp_db)
    _install(tmp_db)
    req = _request(tmp_db)
    root = Path(req.app.state.config.db_path).parent / "files" / "tickets" / ticket
    root.mkdir(parents=True)
    for index in range(75):
        (root / f"{index:03}.md").write_text("artifact")
    (root / "empty").mkdir()
    (root / "site").mkdir()
    (root / "site" / "index.html").write_text("page")
    (root / "site" / "hidden.js").write_text("asset")
    source = (
        f"[duplicate](/files/tickets/{ticket}/000.md) [linked](/files/tickets/{ticket}/linked.md)"
    )
    tmp_db.execute("BEGIN")
    tmp_db.execute(
        "UPDATE tickets SET field_values=json_set(field_values,'$.brief',?) WHERE id=?",
        (source, ticket),
    )
    links.finalize(tmp_db)
    tmp_db.commit()
    _cache.clear()
    first = artifact_page(req, "ticket", ticket, "", 0, 60)
    second = artifact_page(req, "ticket", ticket, "", 60, 60)
    assert first["total"] == 77
    assert len(first["entries"]) == 60
    assert second["entries"][-1]["path"] == "linked.md"
    assert all(entry["path"] != "empty" for entry in first["entries"] + second["entries"])
    assert len(json.dumps(first).encode()) <= PAGE_MAX_BYTES + 1024
    revision = first["revision"]
    (root / "direct.md").write_text("written outside the API")
    _cache.clear()
    refreshed = artifact_page(req, "ticket", ticket, "", 0, 60)
    assert refreshed["total"] == 78 and refreshed["revision"] != revision


def test_history_pages_keep_selected_position_and_locale_typeahead_without_full_lists(
    tmp_db: sqlite3.Connection,
) -> None:
    ticket = _ticket(tmp_db)
    _install(tmp_db)
    tmp_db.execute("BEGIN")
    for index in range(135):
        identity = json.dumps(
            [["PLAN_ACTOR", "manager"], ["PLAN_MANAGER_ID", "si_example"]]
        )
        tmp_db.execute(
            (
                "INSERT INTO conversations(conversation_id,backend_key,model,workspace_"
                "folder,access,created_at,identity_environment_variables) VALUES "
                "(?,'codex','model','/work','full',?,?)"
            ),
            (f"conversation{index:03}", 1790000000 + index * 86400, identity),
        )
        tmp_db.execute(
            "INSERT INTO ticket_conversations(conversation_id,ticket_id) VALUES (?,?)",
            (f"conversation{index:03}", ticket),
        )
    tmp_db.execute("UPDATE tickets SET conversation_id=? WHERE id=?", ("conversation134", ticket))
    tmp_db.commit()
    req = _request(tmp_db)
    first = history_page(req, "ticket", ticket, 0, 60, None, None, -1, "en-US", "UTC")
    selected = history_page(
        req, "ticket", ticket, 0, 60, "conversation123", None, -1, "en-US", "UTC"
    )
    assert first["total"] == 134 and len(first["entries"]) == 60
    assert selected["offset"] == 120 and selected["active_index"] == 123
    assert selected["entries"][3]["conversation_id"] == "conversation123"
    prefix = selected["entries"][3]["label"].split(" · ")[0]
    sought = history_page(req, "ticket", ticket, 0, 60, None, prefix, 0, "en-US", "UTC")
    assert sought["active_index"] == 123 and len(sought["entries"]) <= 60
    assert (
        tmp_db.execute(
            "SELECT count(*) FROM conversation_history_catalog WHERE "
            "owner_kind='manager' AND owner_id='si_example'"
        ).fetchone()[0]
        == 135
    )


def test_goal_catalog_discovers_direct_files_without_a_goal_link_index(
    tmp_db: sqlite3.Connection,
) -> None:
    goal = create_goal(
        tmp_db, {"title": "Context", "project_id": "project_vylo"},
        principal=OWNER_PRINCIPAL, now=1,
    )
    req = _request(tmp_db)
    assert artifact_page(req, "goal", goal.id, "", 0, 60)["entries"] == []
    root = Path(req.app.state.config.db_path).parent / "files" / "goals" / goal.id
    root.mkdir(parents=True)
    (root / "direct.md").write_text("outside the API")
    _cache.clear()
    page = artifact_page(req, "goal", goal.id, "", 0, 60)
    assert page["total"] == 1
    href = f"/files/goals/{goal.id}/direct.md"
    assert page["entries"][0]["href"] == href
    assert links.managed_href(href, "http://testserver") == href
    assert links.managed_href(
        f"#/preview?source=goal&goal={goal.id}&path=direct.md", "http://testserver"
    ) == href
    assert links.managed_href(
        f"/files/goals/{goal.id}/../outside.md", "http://testserver"
    ) is None
