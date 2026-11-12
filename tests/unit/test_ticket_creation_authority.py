"""Ticket creation respects the caller's manager and approval authority."""

from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient
from tests.support.api_app import _app
from tests.support.principals import OWNER_PRINCIPAL

from planner.core.clock import Clock
from planner.managers import data as managers_data
from planner.tickets import actions as tickets_actions
from planner.tickets.contracts import TITLE_MAX_CHARS


@pytest.fixture
def chain(tmp_db: Connection, fake_clock: Clock) -> tuple[str, str, str]:
    own = managers_data.create_manager(
        tmp_db, title="Own Manager", project_id="project_vylo", clock=fake_clock
    )
    unrelated = managers_data.create_manager(
        tmp_db, title="Unrelated Manager", project_id="project_personal", clock=fake_clock
    )
    worker = tickets_actions.create_ticket(
        tmp_db,
        title="Synthetic caller",
        worker_type="coding",
        manager_id=own.id,
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
    )
    return own.id, unrelated.id, worker.id


def test_direct_creation_checks_holder(tmp_db: Connection, chain: tuple[str, str, str]) -> None:
    own, unrelated, worker = chain
    with TestClient(_app(tmp_db)) as client:
        headers_by_actor = (
            {"X-Plan-Actor": "manager", "X-Plan-Manager-ID": own},
            {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": worker},
        )
        for headers in headers_by_actor:
            direct = {"title": "Direct work", "worker_type": "coding"}
            before_direct = tuple(
                tuple(row) for row in tmp_db.execute("SELECT * FROM tickets ORDER BY id")
            )
            direct_response = client.post(
                "/api/tickets",
                json={**direct, "manager": {"kind": "manager", "id": own}},
                headers=headers,
            )
            if headers["X-Plan-Actor"] == "manager":
                assert direct_response.status_code == 200, direct_response.text
                assert direct_response.json()["ceiling_holder"] == {"kind": "manager", "id": own}
            else:
                # Same-chain creation authority does not make a Worker an eligible reviewer.
                assert direct_response.status_code == 400, direct_response.text
                error = direct_response.json()["error"]
                assert error["code"] == "validation"
                assert error["message"] == (
                    "ceiling holder must be the user or the Ticket's direct manager; "
                    "name an eligible holder explicitly"
                )
                assert (
                    tuple(tuple(row) for row in tmp_db.execute("SELECT * FROM tickets ORDER BY id"))
                    == before_direct
                )
            denied = client.post(
                "/api/tickets",
                json={**direct, "manager": {"kind": "manager", "id": unrelated}},
                headers=headers,
            )
            assert denied.status_code == 400, denied.text
            assert denied.json()["error"]["code"] == "agent_forbidden"
