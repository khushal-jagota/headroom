"""Recap requests admit only callers above the recipient, before any side effect."""

import sqlite3
from sqlite3 import Connection
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests.support.api_app import _app

from planner.conversation.contracts import PromptDeliveryQueued
from planner.core.authctx import RequestContext
from planner.core.authority import service as authority
from planner.core.authority.contracts import Target
from planner.core.clock import Clock
from planner.core.contracts import OWNER_PRINCIPAL, Principal, PrincipalKind
from planner.core.db import connect
from planner.managers import data as managers_data
from planner.message_delivery import recap_request, service
from planner.message_delivery.contracts import MessageDeliveryResult
from planner.tickets import actions as tickets_actions
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS


def test_recap_request_http_authority_and_clear_before_send(
    tmp_db: Connection, fake_clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The route and shared operation must refuse before clearing or delivering.

    Existing delivery coverage proves queue semantics, not the authority matrix.
    Reuse one HTTP host for the distinct caller/target relationships and refusals.
    """
    own = managers_data.create_manager(
        tmp_db, title="Own work", project_id="project_vylo", clock=fake_clock
    )
    foreign = managers_data.create_manager(
        tmp_db, title="Foreign work", project_id="project_vylo", clock=fake_clock
    )
    ticket = tickets_actions.create_ticket(
        tmp_db,
        title="Child",
        principal=OWNER_PRINCIPAL,
        now=fake_clock.now_unix(),
        worker_type="coding",
        title_max_chars=TITLE_MAX_CHARS,
        manager_id=own.id,
    )
    sent: list[tuple[Principal, Principal, str]] = []

    async def capture_send(*args: Any, **kwargs: Any) -> MessageDeliveryResult:
        conn, ctx, target, text = args[1], args[3], args[4], args[5]
        assert not conn.in_transaction
        if target.kind is PrincipalKind.ticket:
            assert (
                conn.execute("SELECT recap FROM tickets WHERE id=?", (target.id,)).fetchone()[0]
                == ""
            )
        elif target.kind is PrincipalKind.manager:
            assert (
                conn.execute("SELECT recap FROM managers WHERE id=?", (target.id,)).fetchone()[0]
                == ""
            )
        else:
            assert conn.execute("SELECT recap FROM chief_settings").fetchone()[0] == ""
        assert kwargs["system_message"] is True
        sent.append((ctx.principal, target, text))
        return MessageDeliveryResult(target, "synthetic", PromptDeliveryQueued(queue_position=1))

    monkeypatch.setattr(service, "send_message", capture_send)
    owner: dict[str, str] = {}
    chief = {"X-Plan-Actor": "chief"}
    manager = {"X-Plan-Actor": "manager", "X-Plan-Manager-ID": own.id}
    other_manager = {"X-Plan-Actor": "manager", "X-Plan-Manager-ID": foreign.id}
    worker = {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket.id}
    cases = (
        (owner, "chief", "chief", None, "the owner"),
        (owner, "manager", own.id, None, "the owner"),
        (owner, "ticket", ticket.id, None, "the owner"),
        (chief, "manager", own.id, None, "Chief"),
        (chief, "ticket", ticket.id, None, "Chief"),
        (manager, "ticket", ticket.id, None, "Manager Own work"),
        (other_manager, "ticket", ticket.id, "agent_forbidden", None),
        (manager, "manager", own.id, "agent_forbidden", None),
        (manager, "manager", foreign.id, "agent_forbidden", None),
        (manager, "chief", "chief", "agent_forbidden", None),
        (chief, "chief", "chief", "agent_forbidden", None),
        (worker, "ticket", ticket.id, "agent_forbidden", None),
        (worker, "manager", own.id, "agent_forbidden", None),
        (worker, "chief", "chief", "agent_forbidden", None),
        (owner, "owner", "owner", "validation", None),
        (owner, "ticket", "missing", "not_found", None),
        (chief, "manager", "missing", "not_found", None),
    )
    with TestClient(_app(tmp_db)) as client:
        for headers, kind, target_id, error, requester in cases:
            tmp_db.execute("UPDATE tickets SET recap='Spent recap'")
            tmp_db.execute("UPDATE managers SET recap='Spent recap'")
            tmp_db.execute("UPDATE chief_settings SET recap='Spent recap'")
            tmp_db.commit()
            before_sends = len(sent)
            response = client.post(
                "/api/recap-requests",
                headers=headers,
                json={"target": {"kind": kind, "id": target_id}},
            )
            if error is not None:
                assert response.status_code != 200, response.text
                assert response.json()["error"]["code"] == error, response.text
                assert len(sent) == before_sends
                for table in ("tickets", "managers", "chief_settings"):
                    assert all(
                        row[0] == "Spent recap"
                        for row in tmp_db.execute(f"SELECT recap FROM {table}")
                    )
            else:
                assert response.status_code == 200, response.text
                assert response.json()["fate"] == "queued"
                assert len(sent) == before_sends + 1
                assert sent[-1][2].endswith(f"Requested by {requester}.")


def test_recap_admission_and_clear_exclude_concurrent_reparent(
    tmp_db: Connection, fake_clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parent move cannot invalidate admission before the recap clear.

    The authority matrix cannot prove the gap between its read and the write. A second
    SQLite connection exercises that boundary without another server or a timed race.
    """
    own = managers_data.create_manager(
        tmp_db, title="Current parent", project_id="project_vylo", clock=fake_clock
    )
    foreign = managers_data.create_manager(
        tmp_db, title="New parent", project_id="project_vylo", clock=fake_clock
    )
    ticket = tickets_actions.create_ticket(
        tmp_db,
        title="Child",
        principal=OWNER_PRINCIPAL,
        now=fake_clock.now_unix(),
        worker_type="coding",
        title_max_chars=TITLE_MAX_CHARS,
        manager_id=own.id,
    )
    tickets_data.edit_ticket(
        tmp_db,
        ticket.id,
        edit={"recap": "Spent recap"},
        title_max_chars=TITLE_MAX_CHARS,
        principal=OWNER_PRINCIPAL,
        now=fake_clock.now_unix(),
    )
    database_path = tmp_db.execute("PRAGMA database_list").fetchone()[2]
    competing = connect(database_path, busy_timeout_ms=0)
    require_above = authority.require_above
    checked: list[str] = []

    def move_to_foreign_parent() -> None:
        tickets_data.edit_ticket(
            competing,
            ticket.id,
            edit={"manager_id": foreign.id},
            title_max_chars=TITLE_MAX_CHARS,
            principal=OWNER_PRINCIPAL,
            now=fake_clock.now_unix(),
        )

    def check_and_attempt_move(conn: Connection, caller: Principal, target: Target) -> None:
        require_above(conn, caller, target)
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            move_to_foreign_parent()
        checked.append(target.id)

    monkeypatch.setattr(authority, "require_above", check_and_attempt_move)
    try:
        recap_request._prepare_request(
            tmp_db,
            fake_clock,
            RequestContext(Principal(PrincipalKind.manager, own.id)),
            Principal(PrincipalKind.ticket, ticket.id),
        )
        assert checked == [ticket.id]
        assert tickets_data.read_ticket(tmp_db, ticket.id).recap == ""
        assert not tmp_db.in_transaction
        move_to_foreign_parent()
        assert tickets_data.read_ticket(tmp_db, ticket.id).manager_id == foreign.id
    finally:
        competing.close()
