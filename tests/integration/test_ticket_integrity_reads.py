"""A faulty Ticket must not prevent startup or healthy HTTP reads."""

import sqlite3
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient
from tests.unit.test_sprint_item_supervisors import _app

from planner.core.db import connect


def test_startup_keeps_broken_ticket_visible_and_healthy_ticket_usable(tmp_path: Path) -> None:
    app, database = _app(tmp_path)
    with closing(connect(str(database))) as conn:
        conn.executemany(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,stage,ceiling,"
            "field_values,created_at,updated_at) VALUES (?,?,?,'codex',"
            "'needs_brief','needs_brief','{}',1,1)",
            (("broken", "Broken Ticket", "coding"), ("healthy", "Healthy Ticket", "coding")),
        )
        conn.execute("BEGIN")
        conn.execute("UPDATE tickets SET worker_type='missing_type' WHERE id='broken'")
        sqlite3.Connection.commit(conn)
    with TestClient(app) as client:
        for path in (
            "/api/tickets?detail=full",
            "/api/tickets?detail=summary",
            "/api/board",
            "/api/review",
            "/api/tickets/broken/workspace",
            "/api/tickets/healthy/workspace",
            "/api/tickets?id=broken&detail=full",
        ):
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
        broken = client.get("/api/tickets/broken/workspace").json()
        assert broken["ticket_status"] == "errored" and broken["integrity_error"]["reason"]
        assert "raw_values" not in broken["integrity_error"]
        refused = client.patch("/api/tickets/broken", json={"title": "Must not change"})
        assert refused.status_code == 400, refused.text
        healthy = client.patch("/api/tickets/healthy", json={"title": "Still usable"})
        assert healthy.status_code == 200, healthy.text
