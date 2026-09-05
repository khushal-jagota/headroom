"""Domain actions, the change signal they ring, and the readiness they change.

Every committed Ticket, day, link, project and sprint write announces itself exactly
once, and a rejected or rolled-back write announces nothing. That is what wakes the
worker-step readiness loop. The readiness assertions here are the second half: which
of those writes actually make a Ticket ready for a worker step, and which do not.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core import change_signal
from planner.core import links as core_links
from planner.core.clock import Clock, RealClock, build_clock
from planner.core.config import load_config
from planner.core.contracts import LinkKind
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.sprints import data as sprints_data
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
    TicketStatus,
)
from planner.tickets.logic import fields_codec

_AGENT = {"X-Plan-Actor": "agent"}
_CHIEF = {"X-Plan-Actor": "chief"}


class RecordingChangeSignal:
    def __init__(self) -> None:
        self.calls = 0

    def record(self) -> None:
        self.calls += 1

    def reset(self) -> None:
        self.calls = 0


_subscriptions: list[Callable[[], None]] = []


@pytest.fixture(autouse=True)
def _drop_change_signal_subscriptions() -> Iterator[None]:
    yield
    while _subscriptions:
        _subscriptions.pop()()


def _make_app(
    tmp_path: Path, *, fake_now: str | None = None
) -> tuple[FastAPI, Path, Clock, RecordingChangeSignal]:
    db_path = tmp_path / "readiness-wake-actions.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    env = {
        "PLAN_TEST_MODE": "1",
        "PLAN_DB_PATH": str(db_path),
    }
    if fake_now is not None:
        env["PLAN_FAKE_NOW"] = fake_now
    config = load_config(path=None, env=env)
    clock = build_clock(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    app = create_app(config, clock, conn_factory)
    changes = RecordingChangeSignal()
    _subscriptions.append(change_signal.subscribe(changes.record))
    return app, db_path, clock, changes


def _create_direct(db_path: Path, *, title: str = "Ready") -> str:
    conn = connect(str(db_path))
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="coding",
            title=title,
            actor="human",
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
        )
        return tickets_data.accept_proposal(
            conn,
            ticket.id,
            field="kickoff",
            actor="human",
            now=1,
            next_ceiling=NO_FURTHER,
            at_cap=AtCap.propose,
        ).id
    finally:
        conn.close()


def _is_ready_today(db_path: Path, ticket_id: str) -> bool:
    from planner.runtime.worker_step_readiness import is_ready_for_worker_step
    from planner.worker_types.configuration import configured_worker_type_registry

    conn = connect(str(db_path))
    try:
        ticket = tickets_data.read_ticket(conn, ticket_id)
        return is_ready_for_worker_step(
            conn,
            ticket,
            planning_day_id="day_2099-01-01",
            worker_type_definition=configured_worker_type_registry().require(ticket.worker_type),
        )
    finally:
        conn.close()


def _link_rows(db_path: Path) -> tuple[tuple[str, str, str], ...]:
    conn = connect(str(db_path))
    try:
        return tuple(
            (str(row["from_id"]), str(row["to_id"]), str(row["kind"]))
            for row in conn.execute(
                "SELECT from_id, to_id, kind FROM links ORDER BY from_id, to_id, kind"
            ).fetchall()
        )
    finally:
        conn.close()


def _ticket_snapshot(
    db_path: Path, ticket_id: str, *, exclude_updated_at: bool = False
) -> tuple[tuple[str, object], ...]:
    conn = connect(str(db_path))
    try:
        ticket = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        assert ticket is not None
        columns = tuple(str(column) for column in ticket.keys())
        assert columns.count("updated_at") == 1
        if exclude_updated_at:
            columns = tuple(column for column in columns if column != "updated_at")
        return tuple((column, ticket[column]) for column in columns)
    finally:
        conn.close()


def _ticket_updated_at(db_path: Path, ticket_id: str) -> int:
    conn = connect(str(db_path))
    try:
        row = conn.execute("SELECT updated_at FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        assert row is not None
        return int(row["updated_at"])
    finally:
        conn.close()


def _external_body(state: str) -> dict[str, str]:
    values = {
        "success": "success",
        "approach": "approach",
        "plan": "plan",
        "implementation": "implementation",
        "closeout": "closeout",
    }
    count = {
        "needs_success": 0,
        "needs_approach": 1,
        "needs_plan": 2,
        "needs_implementation": 3,
        "needs_closeout": 4,
        "done": 5,
    }[state]
    return {
        "stage": state,
        "kickoff_note": "external note",
        **dict(list(values.items())[:count]),
    }


def test_ticket_create_and_chief_create_signal_after_success_only(tmp_path: Path) -> None:
    app, db_path, _clock, changes = _make_app(tmp_path)
    with TestClient(app) as client:
        invalid = client.post("/api/tickets", json={"title": "", "worker_type": "coding"})
        assert invalid.status_code == 400
        assert changes.calls == 0

        created = client.post("/api/tickets", json={"title": "Created", "worker_type": "coding"})
        assert created.status_code == 200, created.text
        assert changes.calls == 1

        unauthorized = client.post(
            "/api/chief/tickets/from-external-work",
            json={"title": "Denied", "worker_type": "coding", **_external_body("needs_success")},
        )
        assert unauthorized.status_code == 400
        assert changes.calls == 1

        imported = client.post(
            "/api/chief/tickets/from-external-work",
            json={"title": "Imported", "worker_type": "coding", **_external_body("needs_success")},
            headers=_CHIEF,
        )
        assert imported.status_code == 200, imported.text
        assert changes.calls == 2


@pytest.mark.parametrize(
    ("path", "headers", "extra_body"),
    [
        ("/api/tickets", {}, {}),
        (
            "/api/chief/tickets/from-external-work",
            _CHIEF,
            _external_body("needs_success"),
        ),
    ],
)
def test_ticket_creators_atomically_add_all_blockers_and_reject_any_invalid_set(
    tmp_path: Path,
    path: str,
    headers: dict[str, str],
    extra_body: dict[str, str],
) -> None:
    app, db_path, _clock, changes = _make_app(tmp_path)
    first_blocker = _create_direct(db_path, title="First blocker")
    second_blocker = _create_direct(db_path, title="Second blocker")
    changes.reset()

    def counts() -> tuple[int, ...]:
        conn = connect(str(db_path))
        try:
            return tuple(
                int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in ("tickets", "links")
            )
        finally:
            conn.close()

    base = {
        "title": "Dependent",
        "worker_type": "coding",
        **extra_body,
    }
    with TestClient(app) as client:
        before_missing = counts()
        missing = client.post(
            path,
            json={**base, "title": "Missing rejected", "blocked_by_ticket_ids": ["t_missing"]},
            headers=headers,
        )
        assert missing.status_code == 400
        assert missing.json()["error"] == {
            "code": "link_invalid",
            "message": "from_id must be an existing ticket",
            "detail": {"from_id": "t_missing"},
        }
        assert counts() == before_missing
        assert changes.calls == 0

        before_duplicate = counts()
        duplicate = client.post(
            path,
            json={
                **base,
                "title": "Duplicate rejected",
                "blocked_by_ticket_ids": [first_blocker, first_blocker],
            },
            headers=headers,
        )
        assert duplicate.status_code == 400
        assert duplicate.json()["error"]["code"] == "link_invalid"
        assert duplicate.json()["error"]["detail"]["from_id"] == first_blocker
        assert counts() == before_duplicate
        assert changes.calls == 0

        created = client.post(
            path,
            json={
                **base,
                "blocked_by_ticket_ids": [first_blocker, second_blocker],
            },
            headers=headers,
        )
        assert created.status_code == 200, created.text

    dependent_id = created.json()["id"]
    assert set(_link_rows(db_path)) == {
        (first_blocker, dependent_id, "blocks"),
        (second_blocker, dependent_id, "blocks"),
    }
    assert changes.calls == 1


def test_every_approved_ticket_control_action_signals_once_and_failures_signal_zero(
    tmp_path: Path,
) -> None:
    app, db_path, _clock, changes = _make_app(tmp_path)
    with TestClient(app) as client:
        # accept proposal
        accept_id = _create_direct(db_path, title="Accept")
        conn = connect(str(db_path))
        tickets_data.file_proposal(
            conn,
            accept_id,
            field="success",
            body="proposal",
            actor="agent",
            now=2,
        )
        conn.close()
        changes.reset()
        denied_accept = client.post(
            f"/api/tickets/{accept_id}/accept/success",
            json={"next_ceiling": "needs_approach", "at_cap": "propose"},
            headers=_AGENT,
        )
        assert denied_accept.status_code == 400
        assert changes.calls == 0
        response = client.post(
            f"/api/tickets/{accept_id}/accept/success",
            json={"next_ceiling": "needs_approach", "at_cap": "propose"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["fields"]["success"]["value"] == "proposal"
        assert changes.calls == 1

        changes.reset()
        missing = client.post("/api/tickets/t_missing/accept/success", json={})
        assert missing.status_code == 404
        assert changes.calls == 0

        # pending closeout proposal approval (the final gate before done)
        conn = connect(str(db_path))
        review = tickets_data.create_ticket_from_external_work(
            conn,
            worker_type="coding",
            title="Approve",
            kickoff_note="external note",
            target_stage="needs_closeout",
            provided_values={
                "success": "success",
                "approach": "approach",
                "plan": "plan",
                "implementation": "implementation",
            },
            actor="chief",
            now=3,
            title_max_chars=TITLE_MAX_CHARS,
        )
        tickets_data.change_scope(
            conn,
            review.id,
            ceiling="needs_closeout",
            at_cap=AtCap.propose,
            actor="human",
            now=3,
        )
        tickets_data.file_proposal(
            conn,
            review.id,
            field="closeout",
            body="closeout",
            actor="agent",
            now=3,
        )
        conn.close()
        changes.reset()
        denied_approve = client.post(
            f"/api/tickets/{review.id}/accept/closeout",
            json={"next_ceiling": "none", "at_cap": "propose"},
            headers=_AGENT,
        )
        assert denied_approve.status_code == 400
        assert changes.calls == 0
        response = client.post(
            f"/api/tickets/{review.id}/accept/closeout",
            json={"next_ceiling": "none", "at_cap": "propose"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["stage"] == "done"
        assert changes.calls == 1

        wrong_review_id = _create_direct(db_path)
        changes.reset()
        wrong_review = client.post(f"/api/tickets/{wrong_review_id}/accept/closeout", json={})
        assert wrong_review.status_code == 404
        assert changes.calls == 0

        # settled field edit
        conn = connect(str(db_path))
        editable = tickets_data.create_ticket_from_external_work(
            conn,
            worker_type="coding",
            title="Edit value",
            kickoff_note="external note",
            target_stage="needs_approach",
            provided_values={"success": "old"},
            actor="chief",
            now=4,
            title_max_chars=TITLE_MAX_CHARS,
        )
        conn.close()
        changes.reset()
        denied_value = client.put(
            f"/api/tickets/{editable.id}/value/success",
            json={"body": "denied"},
            headers=_AGENT,
        )
        assert denied_value.status_code == 400
        assert changes.calls == 0
        response = client.put(f"/api/tickets/{editable.id}/value/success", json={"body": "new"})
        assert response.status_code == 200, response.text
        assert response.json()["fields"]["success"]["value"] == "new"
        assert changes.calls == 1

        unsettled_id = _create_direct(db_path)
        changes.reset()
        future_edit = client.put(
            f"/api/tickets/{unsettled_id}/value/success",
            json={"body": "not settled"},
        )
        assert future_edit.status_code == 400
        assert changes.calls == 0

        # scope, direct state, drop, takeover, release
        scope_id = _create_direct(db_path, title="Scope")
        changes.reset()
        denied_scope = client.post(
            f"/api/tickets/{scope_id}/scope",
            json={"ceiling": "needs_approach", "at_cap": "propose"},
            headers=_AGENT,
        )
        assert denied_scope.status_code == 400
        assert changes.calls == 0
        response = client.post(
            f"/api/tickets/{scope_id}/scope",
            json={"ceiling": "needs_approach", "at_cap": "propose"},
        )
        assert response.status_code == 200, response.text
        assert changes.calls == 1
        changes.reset()
        invalid_scope = client.post(
            f"/api/tickets/{scope_id}/scope",
            json={"ceiling": "bogus", "at_cap": "propose"},
        )
        assert invalid_scope.status_code == 400
        assert changes.calls == 0

        state_id = _create_direct(db_path, title="State")
        changes.reset()
        denied_state = client.post(
            f"/api/tickets/{state_id}/stage",
            json={"to_stage": "needs_approach"},
            headers=_AGENT,
        )
        assert denied_state.status_code == 400
        assert changes.calls == 0
        same_state = client.post(
            f"/api/tickets/{state_id}/stage", json={"to_stage": "needs_success"}
        )
        assert same_state.status_code == 400
        assert changes.calls == 0
        response = client.post(
            f"/api/tickets/{state_id}/stage", json={"to_stage": "needs_approach"}
        )
        assert response.status_code == 200, response.text
        assert changes.calls == 1
        changes.reset()
        invalid_state = client.post(f"/api/tickets/{state_id}/stage", json={"to_stage": "bogus"})
        assert invalid_state.status_code == 400
        assert changes.calls == 0

        drop_id = _create_direct(db_path, title="Drop")
        changes.reset()
        denied_drop = client.post(f"/api/tickets/{drop_id}/drop", headers=_AGENT)
        assert denied_drop.status_code == 400
        assert changes.calls == 0
        response = client.post(f"/api/tickets/{drop_id}/drop")
        assert response.status_code == 200, response.text
        assert changes.calls == 1
        changes.reset()
        terminal_retry = client.post(f"/api/tickets/{drop_id}/drop")
        assert terminal_retry.status_code == 400
        assert changes.calls == 0

        control_id = _create_direct(db_path, title="Control")
        changes.reset()
        response = client.post(f"/api/tickets/{control_id}/takeover")
        assert response.status_code == 200, response.text
        assert response.json()["ticket_status"] == "user"
        assert changes.calls == 1
        changes.reset()
        denied_release = client.post(f"/api/tickets/{control_id}/release", headers=_AGENT)
        assert denied_release.status_code == 400
        assert changes.calls == 0
        response = client.post(f"/api/tickets/{control_id}/release")
        assert response.status_code == 200, response.text
        assert response.json()["ticket_status"] == "empty"
        assert changes.calls == 1

        changes.reset()
        unauthorized = client.post(f"/api/tickets/{control_id}/takeover", headers=_AGENT)
        assert unauthorized.status_code == 400
        missing_takeover = client.post("/api/tickets/t_missing/takeover")
        assert missing_takeover.status_code == 404
        missing_release = client.post("/api/tickets/t_missing/release")
        assert missing_release.status_code == 404
        assert changes.calls == 0


def test_source_state_deactivation_frees_its_blocked_targets_and_signals_once(
    tmp_path: Path,
) -> None:
    app, db_path, _clock, changes = _make_app(tmp_path)
    source = _create_direct(db_path, title="Source")
    target = _create_direct(db_path, title="Target")
    conn = connect(str(db_path))
    item = sprints_data.create_item(
        conn,
        title="Target item",
        project_id="project_vylo",
        clock=RealClock(),
    )
    core_links.add_link(conn, source, target, LinkKind.blocks, 1)
    core_links.add_link(conn, source, item.id, LinkKind.blocks, 1)
    conn.close()
    changes.reset()

    with TestClient(app) as client:
        dropped = client.post(f"/api/tickets/{source}/drop")
        assert dropped.status_code == 200, dropped.text

    assert changes.calls == 1
    conn = connect(str(db_path))
    try:
        assert core_links.blocked_target_ids(conn) == set()
        assert tickets_data.read_ticket(conn, target).ticket_status is TicketStatus.empty
    finally:
        conn.close()


def test_change_signal_reaches_subscribers_only_after_the_commit(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    app, db_path, _clock, _changes = _make_app(tmp_path)
    observed: list[str] = []

    def ticket_delivery() -> None:
        conn = connect(str(db_path))
        try:
            assert conn.execute("SELECT 1 FROM tickets WHERE title = 'Committed first'").fetchone()
        finally:
            conn.close()
        observed.append("ticket")
        raise RuntimeError("ticket subscriber failed")

    _subscriptions.append(change_signal.subscribe(ticket_delivery))
    with TestClient(app) as client:
        created = client.post(
            "/api/tickets", json={"title": "Committed first", "worker_type": "coding"}
        )
    assert created.status_code == 200, created.text

    ticket_id = created.json()["id"]

    def day_delivery() -> None:
        conn = connect(str(db_path))
        try:
            assert conn.execute(
                "SELECT 1 FROM day_tickets WHERE day_id = 'day_2099-04-05' AND ticket_id = ?",
                (ticket_id,),
            ).fetchone()
        finally:
            conn.close()
        observed.append("day")
        raise RuntimeError("day subscriber failed")

    _subscriptions.pop()()
    _subscriptions.append(change_signal.subscribe(day_delivery))
    with TestClient(app) as client:
        added = client.post("/api/day/2099-04-05/tickets", json={"ticket_id": ticket_id})
    assert added.status_code == 200, added.text
    # The day route commits twice, so its subscriber runs twice; what matters is that
    # every run saw the committed row, and that raising did not break the request.
    assert observed[0] == "ticket"
    assert set(observed[1:]) == {"day"}
    assert "ticket subscriber failed" in caplog.text
    assert "day subscriber failed" in caplog.text
    assert (
        sum(
            record.getMessage() == "change signal subscriber failed"
            for record in caplog.records
        )
        == len(observed)
    )


def test_proposal_change_signal_sees_the_committed_proposal(tmp_path: Path) -> None:
    # The signal is announced by the commit itself: the subscriber reads the filed
    # proposal through its own connection, so a signal-before-commit regression
    # would leave `observed` empty.
    app, db_path, _clock, _changes = _make_app(tmp_path)
    ticket_id = _create_direct(db_path, title="Commit before wake")
    observed: list[str] = []

    def proposal_delivery() -> None:
        conn = connect(str(db_path))
        try:
            persisted = tickets_data.read_ticket(conn, ticket_id)
        finally:
            conn.close()
        slot = fields_codec.get_slot(persisted.fields, "success")
        assert slot.proposal is not None
        assert slot.proposal.body == "committed before wake"
        observed.append("proposal")

    _subscriptions.append(change_signal.subscribe(proposal_delivery))
    with TestClient(app) as client:
        response = client.post(
            f"/api/tickets/{ticket_id}/propose/success",
            json={"body": "committed before wake"},
            headers=_AGENT,
        )
    assert response.status_code == 200, response.text
    assert observed == ["proposal"]


