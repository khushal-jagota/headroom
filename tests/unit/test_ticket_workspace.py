"""Closed field omission must preserve control admission and exact field revisions."""

import json
from sqlite3 import Connection

from starlette.requests import Request
from tests.support.principals import OWNER_PRINCIPAL

from planner.tickets import data, views
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


def test_validation_only_change_updates_selected_revision_in_same_second(
    tmp_db: Connection,
) -> None:
    """Mounted controls must refresh when only attributed verification changes."""
    from planner.conversation.model_resolution.contracts import ValidationReason
    from planner.conversation.model_resolution.logic.validation import unresolved_validation
    from planner.runtime.conversation_start import worker_resolve, workspace_binding_from_start
    from planner.tickets.validation.data import read_launch_selection, record_launch_validation

    ticket = data.create_ticket(
        tmp_db,
        title="Verification state",
        worker_type="coding",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
    )
    provider = TicketWorkspaceResources()
    request = Request({"type": "http"})
    key = f"/api/tickets/{ticket.id}/workspace"
    original = provider.capture(tmp_db, request, key)
    assert original is not None
    selection = read_launch_selection(tmp_db, ticket.id)
    binding = workspace_binding_from_start(worker_resolve(tmp_db, ticket), ticket.locale)
    assert record_launch_validation(
        tmp_db,
        ticket.id,
        unresolved_validation(selection, ValidationReason.offline),
        expected_workspace=binding,
        now=1,
    )
    changed = provider.capture(tmp_db, request, key)
    assert changed is not None and changed.revision != original.revision
    body = changed.read()
    assert isinstance(body, dict)
    assert body["launch_validation"]["validation"]["reason"] == "offline"
    assert body["updated_at"] == ticket.updated_at
