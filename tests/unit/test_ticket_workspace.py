"""Closed field omission must preserve control admission and exact field revisions."""

import json
import sqlite3
from collections.abc import Iterator
from sqlite3 import Connection

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request
from tests.support.principals import OWNER_PRINCIPAL

from planner.core.errors import PlannerError
from planner.core.server import handle_planner_error
from planner.tickets import data, views
from planner.tickets.api import db_conn
from planner.tickets.workspace.api import router
from planner.tickets.workspace.resource_versions import TicketWorkspaceResources
from planner.tickets.workspace.views import workspace_field, workspace_ticket


def test_selected_ticket_preserves_controls_without_closed_bodies(tmp_db: Connection) -> None:
    ticket = data.create_ticket(
        tmp_db,
        title="Compact controls",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )
    values = {"brief": "current", "plan": "historical body " * 20000}
    tmp_db.execute("UPDATE tickets SET field_values=? WHERE id=?", (json.dumps(values), ticket.id))
    tmp_db.commit()
    full = views.ticket_detail(tmp_db, ticket.id, 1)
    compact = workspace_ticket(tmp_db, ticket.id)
    for key in (
        "id",
        "worker_type",
        "stage",
        "ticket_status",
        "ceiling",
        "ceiling_holder",
        "is_backlogged",
        "blocked",
        "manager_id",
        "conversation_id",
        "pending_proposal",
        "field_approvals",
        "record_sha256",
    ):
        assert compact[key] == full[key]
    assert compact["field_values"] == {"brief": "current"}
    assert "artifacts" not in compact and "conversation_history" not in compact
    assert compact["field_summaries"]["plan"]["byte_count"] == len(values["plan"].encode())
    assert workspace_field(tmp_db, ticket.id, "plan")["value"] == values["plan"]
    assert len(json.dumps(compact)) < 5000

    database_path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    stored_before = tmp_db.execute(
        "SELECT field_values FROM tickets WHERE id=?", (ticket.id,)
    ).fetchone()[0]

    def field_connection() -> Iterator[Connection]:
        # The in-process route uses the same fixture database through a read-only
        # connection that can cross TestClient's worker threads.
        connection = sqlite3.connect(
            f"file:{database_path}?mode=ro", uri=True, check_same_thread=False
        )
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.add_exception_handler(PlannerError, handle_planner_error)
    app.dependency_overrides[db_conn] = field_connection
    with TestClient(app) as client:
        response = client.get(f"/api/tickets/{ticket.id}/workspace/fields/plan")
        assert response.status_code == 200, response.text
        assert response.json() == {
            "ticket_id": ticket.id,
            "field": "plan",
            "revision": compact["field_summaries"]["plan"]["revision"],
            "value": values["plan"],
        }
        unknown = client.get(f"/api/tickets/{ticket.id}/workspace/fields/never_stored")
        assert unknown.status_code == 404, unknown.text
        assert unknown.json()["error"]["message"] == "Ticket field not found"
    assert tmp_db.execute(
        "SELECT field_values FROM tickets WHERE id=?", (ticket.id,)
    ).fetchone()[0] == stored_before


def test_field_revision_changes_only_with_its_body_and_rolls_back(tmp_db: Connection) -> None:
    ticket = data.create_ticket(
        tmp_db,
        title="Field identity",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )
    provider = TicketWorkspaceResources()
    request = Request({"type": "http"})
    key = f"/api/tickets/{ticket.id}/workspace/fields/brief"
    original = provider.capture(tmp_db, request, key)
    assert original is not None
    tmp_db.execute("UPDATE tickets SET recap='another recap' WHERE id=?", (ticket.id,))
    tmp_db.commit()
    same = provider.capture(tmp_db, request, key)
    assert same is not None and same.revision == original.revision
    tmp_db.execute("BEGIN")
    tmp_db.execute(
        "UPDATE tickets SET field_values=? WHERE id=?", ('{"brief":"changed"}', ticket.id)
    )
    changed = provider.capture(tmp_db, request, key)
    assert changed is not None and changed.revision != original.revision
    changed_body = changed.read()
    assert isinstance(changed_body, dict) and changed_body["value"] == "changed"
    tmp_db.rollback()
    restored = provider.capture(tmp_db, request, key)
    assert restored is not None and restored.revision == original.revision
    restored_body = restored.read()
    assert isinstance(restored_body, dict) and restored_body["value"] == ""


def test_retained_field_reads_do_not_admit_new_writes_or_unknown_fields(
    tmp_db: Connection,
) -> None:
    ticket = data.create_ticket(
        tmp_db,
        title="Retained field read boundary",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )
    values = {"brief": "current", "retired_custom": ""}
    tmp_db.execute("UPDATE tickets SET field_values=? WHERE id=?", (json.dumps(values), ticket.id))
    tmp_db.commit()
    provider = TicketWorkspaceResources()
    request = Request({"type": "http"})
    key = f"/api/tickets/{ticket.id}/workspace/fields/retired_custom"
    original = provider.capture(tmp_db, request, key)
    assert original is not None
    original_body = original.read()
    assert isinstance(original_body, dict) and original_body["value"] == ""

    with pytest.raises(PlannerError, match="unknown ticket field"):
        data.edit_ticket(
            tmp_db,
            ticket.id,
            edit={"field_values": {"retired_custom": "new text"}},
            title_max_chars=200,
            principal=OWNER_PRINCIPAL,
            now=2,
        )
    assert json.loads(
        tmp_db.execute("SELECT field_values FROM tickets WHERE id=?", (ticket.id,)).fetchone()[0]
    ) == values
    data.edit_ticket(
        tmp_db,
        ticket.id,
        edit={"recap": "An unrelated current edit"},
        title_max_chars=200,
        principal=OWNER_PRINCIPAL,
        now=3,
    )
    retained = provider.capture(tmp_db, request, key)
    assert retained is not None and retained.revision == original.revision
    retained_body = retained.read()
    assert isinstance(retained_body, dict) and retained_body["value"] == ""
    with pytest.raises(PlannerError, match="Ticket field not found"):
        provider.capture(tmp_db, request, f"/api/tickets/{ticket.id}/workspace/fields/never_stored")
