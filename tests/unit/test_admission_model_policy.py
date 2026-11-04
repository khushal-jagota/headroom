"""A canonical configuration write refuses a model disabled after the preceding read."""

from sqlite3 import Connection

import pytest

from planner.conversation.backend_state import write_model_enablement
from planner.conversation.contracts import ConversationBackendKey as Backend
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.errors import PlannerError
from planner.tickets import data as tickets


def test_configuration_commit_rechecks_disables_without_catalog_evidence(
    tmp_db: Connection,
) -> None:
    ticket = tickets.create_ticket(
        tmp_db,
        title="Configuration race",
        principal=OWNER_PRINCIPAL,
        now=1,
        title_max_chars=200,
        worker_type="coding",
        employee_backend="codex",
        employee_launch_model="gpt-6-sol",
    )
    expected = tickets.employee_launch_configuration(ticket)
    write_model_enablement(tmp_db, Backend.codex, "gpt-6-sol", False)

    def commit_configuration() -> None:
        tickets.write_employee_configuration(
            tmp_db,
            ticket.id,
            expected_employee_configuration=expected,
            employee_backend="codex",
            employee_launch_model="gpt-6-sol",
            employee_launch_reasoning_effort="high",
            now=2,
        )

    with pytest.raises(PlannerError, match="disabled"):
        commit_configuration()
    assert tickets.employee_launch_configuration(tickets.read_ticket(tmp_db, ticket.id)) == expected
    write_model_enablement(tmp_db, Backend.codex, "gpt-6-sol", True)
    commit_configuration()
    current = tickets.read_ticket(tmp_db, ticket.id)
    assert current.employee_launch_model == "gpt-6-sol"
    assert current.employee_launch_reasoning_effort == "high"
