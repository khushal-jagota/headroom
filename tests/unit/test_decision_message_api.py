"""HTTP decision notes take their author from the authenticated request context.

Domain tests pass principals directly. This existing API fixture proves that raw
actor fields cannot override the Manager identity, and that the wire carries the
note, visible revision, and retry identity through the parser to durable storage.
"""

from pathlib import Path

from fastapi.testclient import TestClient
from tests.support.launch_identity import agent_headers
from tests.support.principals import OWNER_PRINCIPAL, ticket_principal
from tests.unit.test_ticket_create_api import create_api as create_api

from planner.core.clock import Clock
from planner.core.contracts import Principal, PrincipalKind
from planner.core.db import connect
from planner.managers import data as managers_data
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS


def test_authenticated_manager_owns_the_exact_decision_note_and_retry(
    create_api: tuple[TestClient, Path], fake_clock: Clock
) -> None:
    client, db_path = create_api
    with connect(str(db_path)) as conn:
        manager = managers_data.create_manager(
            conn, title="Evidence Review", project_id="project_vylo", clock=fake_clock
        )
        holder = Principal(PrincipalKind.manager, manager.id)
        ticket = tickets_data.create_ticket(
            conn,
            title="An attributed API decision",
            worker_type="coding",
            brief="Agreed scope",
            principal=OWNER_PRINCIPAL,
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
            manager_id=manager.id,
            stated_holder=holder,
            stated_ceiling="needs_what_changes",
        )
        ticket = tickets_data.file_current_proposal(
            conn, ticket.id, body="Proposed change", principal=ticket_principal(ticket.id), now=2
        )
    note = "  Preserve the full note.\n\nUnicode café 🧭\nFinal line.  "
    body = {
        "message": note,
        "decision_id": "authenticated-manager-decision",
        "expected_proposal_revision": ticket.pending_proposal_revision,
        "next_ceiling": "needs_implementation",
        "next_holder": {"kind": "manager", "id": manager.id},
        # These untrusted labels must never determine the author of the prompt.
        "actor": {"kind": "owner", "id": "owner"},
        "sender": {"kind": "owner", "id": "owner"},
        "sender_label": "User",
    }
    headers = agent_headers(client, "manager", manager.id)
    path = f"/api/tickets/{ticket.id}/accept/what_changes"
    stale = client.post(
        path,
        json={**body, "expected_proposal_revision": ticket.pending_proposal_revision + 1},
        headers=headers,
    )
    assert stale.status_code == 400, stale.text
    assert "visible proposal changed" in stale.json()["error"]["message"]
    with connect(str(db_path)) as conn:
        assert conn.execute("SELECT count(*) FROM ticket_decision_messages").fetchone()[0] == 0
    accepted = client.post(path, json=body, headers=headers)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["stage"] == "needs_implementation"
    repeated = client.post(path, json=body, headers=headers)
    assert repeated.status_code == 200, repeated.text
    # The legacy API already treats a field alias as the same ceiling request.
    alias_retry = client.post(
        path, json={**body, "next_ceiling": "implementation"}, headers=headers
    )
    assert alias_retry.status_code == 200, alias_retry.text
    with connect(str(db_path)) as conn:
        rows = conn.execute("SELECT * FROM ticket_decision_messages").fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row["decision_id"] == body["decision_id"]
    assert row["proposal_revision"] == ticket.pending_proposal_revision
    assert row["sender_kind"] == "manager"
    assert row["sender_id"] == manager.id
    assert row["message"] == note
    assert row["model_text"] == (
        f"Manager Evidence Review approved the What Changes proposal with this note:\n{note}"
    )
    # Historical final commands named the completion stop. Its field alias must
    # still recover that exact decision after the report moves the Ticket to Done.
    worker = agent_headers(client, "worker", ticket.id)
    proposed = client.post(
        f"/api/tickets/{ticket.id}/propose",
        json={"body": "Verified implementation"}, headers=worker,
    )
    final_body = {
        "decision_id": "historical-final-stop",
        "expected_proposal_revision": proposed.json()["pending_proposal"]["revision"],
        "next_ceiling": "needs_consequences",
        "next_holder": {"kind": "manager", "id": manager.id},
    }
    final_path = f"/api/tickets/{ticket.id}/accept/implementation"
    final = client.post(final_path, json=final_body, headers=headers)
    assert final.status_code == 200, final.text
    assert final.json()["stage"] == "needs_consequences"
    done = client.post(
        f"/api/tickets/{ticket.id}/propose",
        json={"body": "Verified follow-through"}, headers=worker,
    )
    assert done.status_code == 200, done.text
    assert done.json()["stage"] == "needs_consequences"
    done = client.post(
        f"/api/tickets/{ticket.id}/accept/consequences",
        json={"expected_proposal_revision": done.json()["pending_proposal"]["revision"]},
        headers=headers,
    )
    assert done.status_code == 200, done.text
    assert done.json()["stage"] == "done"
    final_retry = client.post(
        final_path, json={**final_body, "next_ceiling": "consequences"}, headers=headers
    )
    assert final_retry.status_code == 200, final_retry.text
    assert final_retry.json()["stage"] == "done"
    with connect(str(db_path)) as conn:
        assert conn.execute(
            "SELECT count(*) FROM ticket_decision_messages "
            "WHERE decision_id='historical-final-stop'"
        ).fetchone()[0] == 1
