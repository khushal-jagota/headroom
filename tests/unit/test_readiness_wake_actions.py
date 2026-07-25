"""Domain actions, the change signal they ring, and the readiness they change.

Every committed Ticket, day, link, project and sprint write announces itself exactly
once, and a rejected or rolled-back write announces nothing. That is what wakes the
worker-step readiness loop. The readiness assertions here are the second half: which
of those writes actually make a Ticket ready for a worker step, and which do not.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core import change_signal
from planner.core import clock as planner_clock
from planner.core import links as core_links
from planner.core.clock import Clock, RealClock, build_clock
from planner.core.config import load_config
from planner.core.contracts import LinkKind
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.days import data as days_data
from planner.sprints import data as sprints_data
from planner.tickets import data as tickets_data
from planner.tickets.contracts import (
    NO_FURTHER,
    TITLE_MAX_CHARS,
    AtCap,
    StageOwnershipMode,
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

    def counts() -> tuple[int, int, int]:
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


def test_creator_blockers_all_must_clear_before_readiness_resumes(
    tmp_path: Path,
) -> None:
    app, db_path, _clock, _changes = _make_app(
        tmp_path, fake_now="2099-01-01T12:00:00+00:00"
    )
    first_blocker = _create_direct(db_path, title="First prerequisite")
    second_blocker = _create_direct(db_path, title="Second prerequisite")
    with TestClient(app) as client:
        created = client.post(
            "/api/tickets",
            json={
                "title": "Dependent",
                "worker_type": "coding",
                "blocked_by_ticket_ids": [first_blocker, second_blocker],
            },
        )
        assert created.status_code == 200, created.text
        dependent = created.json()["id"]
        conn = connect(str(db_path))
        days_data.add_day_ticket(conn, "day_2099-01-01", dependent, 2)
        conn.close()
        accepted = client.post(
            f"/api/tickets/{dependent}/accept/kickoff",
            json={"next_ceiling": "needs_success", "at_cap": "propose"},
        )
        assert accepted.status_code == 200, accepted.text
        assert not _is_ready_today(db_path, dependent)

        removed = client.delete(
            "/api/links",
            params={"from_id": first_blocker, "to_id": dependent, "kind": "blocks"},
        )
        assert removed.status_code == 200, removed.text
        assert not _is_ready_today(db_path, dependent)

        completed = client.post(
            f"/api/tickets/{second_blocker}/stage", json={"to_stage": "done"}
        )
        assert completed.status_code == 200, completed.text

    assert _is_ready_today(db_path, dependent)


def test_chief_rejections_do_not_signal_or_change_canonical_records(
    tmp_path: Path,
) -> None:
    app, db_path, _clock, changes = _make_app(tmp_path)
    with TestClient(app) as client:
        malformed_create = client.post(
            "/api/chief/tickets/from-external-work",
            json={"title": "Malformed", "worker_type": "coding", "stage": "needs_success"},
            headers=_CHIEF,
        )
        assert malformed_create.status_code == 400
        assert changes.calls == 0

        conn = connect(str(db_path))
        assert conn.execute("SELECT 1 FROM tickets WHERE title = 'Malformed'").fetchone() is None
        conn.close()

        ticket_id = _create_direct(db_path, title="Reconcile rejection")
        changes.reset()
        before_malformed = _ticket_snapshot(db_path, ticket_id)
        malformed_reconcile = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json={
                "stage": "needs_plan",
                "kickoff_note": "external note",
                "success": "success",
            },
            headers=_CHIEF,
        )
        assert malformed_reconcile.status_code == 400
        assert changes.calls == 0
        assert _ticket_snapshot(db_path, ticket_id) == before_malformed

        conn = connect(str(db_path))
        conn.execute(
            "UPDATE tickets SET ticket_status = 'agent' WHERE id = ?",
            (ticket_id,),
        )
        conn.close()
        changes.reset()
        before_active = _ticket_snapshot(db_path, ticket_id)
        active_reconcile = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json=_external_body("needs_success"),
            headers=_CHIEF,
        )
        assert active_reconcile.status_code == 409
        assert changes.calls == 0
        assert _ticket_snapshot(db_path, ticket_id) == before_active


def test_accepting_kickoff_into_paired_stage_leaves_empty_and_signals(
    tmp_path: Path,
) -> None:
    app, db_path, _clock, changes = _make_app(
        tmp_path,
        fake_now="2099-01-01T12:00:00+00:00",
    )
    conn = connect(str(db_path))
    try:
        ticket = tickets_data.create_ticket(
            conn,
            worker_type="new_worker",
            title="New specialist",
            actor="human",
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
        )
        days_data.add_day_ticket(conn, "day_2099-01-01", ticket.id, 2)
    finally:
        conn.close()
    changes.reset()

    with TestClient(app) as client:
        accepted = client.post(
            f"/api/tickets/{ticket.id}/accept/kickoff",
            json={"next_ceiling": "needs_understanding", "at_cap": "propose"},
        )

    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["stage"] == "needs_understanding"
    assert accepted.json()["ticket_status"] == TicketStatus.empty.value
    assert changes.calls == 1
    assert _is_ready_today(db_path, ticket.id)


def test_same_mode_ownership_does_not_reopen_but_real_paired_transition_does(
    tmp_path: Path,
) -> None:
    app, db_path, _clock, changes = _make_app(
        tmp_path,
        fake_now="2099-01-01T12:00:00+00:00",
    )
    ticket_id = _create_direct(db_path, title="Ownership")
    conn = connect(str(db_path))
    try:
        days_data.add_day_ticket(conn, "day_2099-01-01", ticket_id, 2)
    finally:
        conn.close()
    changes.reset()

    with TestClient(app) as client:
        paired = client.put(
            f"/api/tickets/{ticket_id}/stage-ownership/needs_success",
            json={"ownership_mode": StageOwnershipMode.paired.value},
        )
        assert paired.status_code == 200, paired.text
        assert paired.json()["ticket_status"] == TicketStatus.empty.value
        assert changes.calls == 1
        assert _is_ready_today(db_path, ticket_id)

        conn = connect(str(db_path))
        try:
            conn.execute(
                "UPDATE tickets SET ticket_status = 'paired', employee_session_id = ? "
                "WHERE id = ?",
                ("existing-session", ticket_id),
            )
            conn.commit()
        finally:
            conn.close()
        assert not _is_ready_today(db_path, ticket_id)

        changes.reset()
        same_paired = client.put(
            f"/api/tickets/{ticket_id}/stage-ownership/needs_success",
            json={"ownership_mode": StageOwnershipMode.paired.value},
        )
        assert same_paired.status_code == 200, same_paired.text
        assert same_paired.json()["ticket_status"] == TicketStatus.paired.value
        # Rewriting the same ownership mode changes nothing canonical, but the writer
        # still opens and commits its transaction, and every commit signals.
        assert changes.calls == 1
        assert not _is_ready_today(db_path, ticket_id)

        changes.reset()
        user = client.put(
            f"/api/tickets/{ticket_id}/stage-ownership/needs_success",
            json={"ownership_mode": StageOwnershipMode.user.value},
        )
        assert user.status_code == 200, user.text
        assert user.json()["ticket_status"] == TicketStatus.user.value
        assert changes.calls == 1

        changes.reset()
        paired_again = client.put(
            f"/api/tickets/{ticket_id}/stage-ownership/needs_success",
            json={"ownership_mode": StageOwnershipMode.paired.value},
        )
        assert paired_again.status_code == 200, paired_again.text
        assert paired_again.json()["ticket_status"] == TicketStatus.empty.value
        assert changes.calls == 1
        assert _is_ready_today(db_path, ticket_id)


def test_chief_reconcile_changes_canonical_records_for_a_semantic_change_only(
    tmp_path: Path,
) -> None:
    app, db_path, clock, changes = _make_app(
        tmp_path, fake_now="2099-01-01T12:00:00+00:00"
    )
    assert isinstance(clock, planner_clock.TestClock)
    body = {"title": "Imported", "worker_type": "coding", **_external_body("needs_success")}
    with TestClient(app) as client:
        created = client.post("/api/chief/tickets/from-external-work", json=body, headers=_CHIEF)
        assert created.status_code == 200, created.text
        ticket_id = created.json()["id"]
        changes.reset()

        before_replay = _ticket_snapshot(db_path, ticket_id, exclude_updated_at=True)
        before_replay_updated_at = _ticket_updated_at(db_path, ticket_id)
        clock.set(clock.now() + timedelta(minutes=1))
        replay = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json=_external_body("needs_success"),
            headers=_CHIEF,
        )
        assert replay.status_code == 200, replay.text
        assert (
            _ticket_snapshot(db_path, ticket_id, exclude_updated_at=True)
            == before_replay
        )
        assert _ticket_updated_at(db_path, ticket_id) == clock.now_unix()
        assert _ticket_updated_at(db_path, ticket_id) != before_replay_updated_at

        conn = connect(str(db_path))
        conn.execute("UPDATE tickets SET ticket_status = 'errored' WHERE id = ?", (ticket_id,))
        conn.commit()
        conn.close()
        changes.reset()

        normalized = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json=_external_body("needs_success"),
            headers=_CHIEF,
        )
        assert normalized.status_code == 200, normalized.text
        assert normalized.json()["ticket_status"] == "empty"
        assert changes.calls == 1

        before_second_replay = _ticket_snapshot(
            db_path, ticket_id, exclude_updated_at=True
        )
        before_second_replay_updated_at = _ticket_updated_at(db_path, ticket_id)
        clock.set(clock.now() + timedelta(minutes=1))
        second_replay = client.post(
            f"/api/chief/tickets/{ticket_id}/reconcile-from-external-work",
            json=_external_body("needs_success"),
            headers=_CHIEF,
        )
        assert second_replay.status_code == 200
        assert (
            _ticket_snapshot(db_path, ticket_id, exclude_updated_at=True)
            == before_second_replay
        )
        assert _ticket_updated_at(db_path, ticket_id) == clock.now_unix()
        assert _ticket_updated_at(db_path, ticket_id) != before_second_replay_updated_at


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


def test_ticket_delete_with_day_and_block_link_signals_exactly_once(tmp_path: Path) -> None:
    app, db_path, _clock, changes = _make_app(tmp_path)
    target = _create_direct(db_path, title="Delete")
    other = _create_direct(db_path, title="Other")
    incoming = _create_direct(db_path, title="Incoming")
    conn = connect(str(db_path))
    item = sprints_data.create_item(
        conn,
        title="Blocked item",
        project_id="project_vylo",
        clock=RealClock(),
    )
    days_data.add_day_ticket(conn, "day_2099-01-01", target, 1)
    core_links.add_link(conn, target, other, LinkKind.blocks, 1)
    core_links.add_link(conn, incoming, target, LinkKind.blocks, 1)
    core_links.add_link(conn, target, item.id, LinkKind.blocks, 1)
    conn.close()
    changes.reset()

    with TestClient(app) as client:
        missing = client.delete("/api/tickets/t_missing")
        assert missing.status_code == 404
        assert changes.calls == 0
        denied = client.delete(f"/api/tickets/{target}", headers=_AGENT)
        assert denied.status_code == 400
        assert changes.calls == 0
        deleted = client.delete(f"/api/tickets/{target}")
        assert deleted.status_code == 200, deleted.text
        assert sorted(deleted.json()["linked_entity_ids"]) == sorted([incoming, item.id, other])

    assert changes.calls == 1
    conn = connect(str(db_path))
    try:
        assert conn.execute("SELECT 1 FROM tickets WHERE id = ?", (target,)).fetchone() is None
        assert (
            conn.execute(
                "SELECT 1 FROM links WHERE from_id = ? OR to_id = ?", (target, target)
            ).fetchone()
            is None
        )
    finally:
        conn.close()


@pytest.mark.parametrize("date", ["today", "2099-02-03"])
def test_day_membership_add_and_remove_signal_and_a_missing_ticket_does_not(
    tmp_path: Path, date: str
) -> None:
    app, db_path, _clock, changes = _make_app(tmp_path)
    ticket_id = _create_direct(db_path)
    with TestClient(app) as client:
        # Each day route commits twice: the membership write, then the day view it
        # returns, which materializes the day in a transaction of its own.
        changes.reset()
        added = client.post(f"/api/day/{date}/tickets", json={"ticket_id": ticket_id})
        assert added.status_code == 200, added.text
        assert changes.calls == 2
        changes.reset()
        removed = client.delete(f"/api/day/{date}/tickets/{ticket_id}")
        assert removed.status_code == 200, removed.text
        assert changes.calls == 2

        changes.reset()
        invalid = client.post(f"/api/day/{date}/tickets", json={"ticket_id": "t_missing"})
        assert invalid.status_code == 404
        assert changes.calls == 0


def test_day_database_failure_rolls_back_and_does_not_signal(tmp_path: Path) -> None:
    app, db_path, _clock, changes = _make_app(tmp_path)
    ticket_id = _create_direct(db_path)
    conn = connect(str(db_path))
    conn.execute(
        """
        CREATE TRIGGER test_fail_day_ticket_insert
        BEFORE INSERT ON day_tickets
        BEGIN
          SELECT RAISE(ABORT, 'forced day-ticket insert failure');
        END
        """
    )
    conn.close()
    changes.reset()

    with TestClient(app, raise_server_exceptions=False) as client:
        failed = client.post("/api/day/2099-06-01/tickets", json={"ticket_id": ticket_id})
        assert failed.status_code == 500
        assert changes.calls == 0

    conn = connect(str(db_path))
    try:
        assert conn.execute("SELECT 1 FROM days WHERE id = 'day_2099-06-01'").fetchone() is None
        assert (
            conn.execute("SELECT 1 FROM day_tickets WHERE day_id = 'day_2099-06-01'").fetchone()
            is None
        )
    finally:
        conn.close()


@pytest.mark.parametrize("target_kind", ["ticket", "sprint_item"])
def test_link_actions_signal_for_blocks_to_ticket_and_sprint_item_targets(
    tmp_path: Path, target_kind: str
) -> None:
    app, db_path, _clock, changes = _make_app(tmp_path)
    first = _create_direct(db_path, title="First")
    if target_kind == "sprint_item":
        conn = connect(str(db_path))
        second = sprints_data.create_item(
            conn,
            title="Link target",
            project_id="project_vylo",
            clock=RealClock(),
        ).id
        conn.close()
    else:
        second = _create_direct(db_path, title="Second")
    changes.reset()
    with TestClient(app) as client:
        added = client.post(
            "/api/links",
            json={"from_id": first, "to_id": second, "kind": "blocks"},
        )
        assert added.status_code == 200, added.text
        assert changes.calls == 1

        changes.reset()
        duplicate = client.post(
            "/api/links",
            json={"from_id": first, "to_id": second, "kind": "blocks"},
        )
        assert duplicate.status_code == 400
        assert changes.calls == 0

        removed = client.delete(
            "/api/links", params={"from_id": first, "to_id": second, "kind": "blocks"}
        )
        assert removed.status_code == 200, removed.text
        assert changes.calls == 1

        changes.reset()
        missing = client.delete(
            "/api/links", params={"from_id": first, "to_id": second, "kind": "blocks"}
        )
        assert missing.status_code == 404
        assert changes.calls == 0


def test_blocks_cycle_failure_does_not_signal(tmp_path: Path) -> None:
    app, db_path, _clock, changes = _make_app(tmp_path)
    first = _create_direct(db_path, title="First")
    second = _create_direct(db_path, title="Second")
    changes.reset()
    with TestClient(app) as client:
        added = client.post(
            "/api/links",
            json={"from_id": first, "to_id": second, "kind": "blocks"},
        )
        assert added.status_code == 200, added.text
        assert changes.calls == 1
        changes.reset()
        cycle = client.post(
            "/api/links",
            json={"from_id": second, "to_id": first, "kind": "blocks"},
        )
        assert cycle.status_code == 400
        assert changes.calls == 0


def test_agent_link_add_and_remove_are_rejected_without_writes_or_signals(
    tmp_path: Path,
) -> None:
    app, db_path, _clock, changes = _make_app(tmp_path)
    first = _create_direct(db_path, title="First")
    second = _create_direct(db_path, title="Second")

    links_before = _link_rows(db_path)
    changes.reset()
    with TestClient(app) as client:
        add_response = client.post(
            "/api/links",
            json={"from_id": first, "to_id": second, "kind": "blocks"},
            headers=_AGENT,
        )

    assert add_response.status_code == 400
    assert add_response.json()["error"]["code"] == "agent_forbidden"
    assert _link_rows(db_path) == links_before
    assert changes.calls == 0

    with TestClient(app) as client:
        direct_add = client.post(
            "/api/links",
            json={"from_id": first, "to_id": second, "kind": "blocks"},
        )
        assert direct_add.status_code == 200, direct_add.text
    changes.reset()
    links_before_remove = _link_rows(db_path)

    with TestClient(app) as client:
        remove_response = client.delete(
            "/api/links",
            params={"from_id": first, "to_id": second, "kind": "blocks"},
            headers=_AGENT,
        )

    assert remove_response.status_code == 400
    assert remove_response.json()["error"]["code"] == "agent_forbidden"
    assert _link_rows(db_path) == links_before_remove
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


def test_reactivating_source_rejects_active_blocks_cycle_and_does_not_signal(
    tmp_path: Path,
) -> None:
    app, db_path, _clock, changes = _make_app(tmp_path)
    source = _create_direct(db_path, title="Source")
    target = _create_direct(db_path, title="Target")
    conn = connect(str(db_path))
    tickets_data.set_stage(conn, source, new_stage="done", actor="human", now=1)
    core_links.add_link(conn, source, target, LinkKind.blocks, 1)
    core_links.add_link(conn, target, source, LinkKind.blocks, 1)
    conn.close()
    changes.reset()

    with TestClient(app) as client:
        reopened = client.post(f"/api/tickets/{source}/stage", json={"to_stage": "needs_success"})

    assert reopened.status_code == 400
    assert reopened.json()["error"]["code"] == "link_cycle"
    assert changes.calls == 0


def test_link_database_constraint_failure_rolls_back_and_does_not_signal(
    tmp_path: Path,
) -> None:
    app, db_path, _clock, changes = _make_app(tmp_path)
    first = _create_direct(db_path, title="First")
    second = _create_direct(db_path, title="Second")
    third = _create_direct(db_path, title="Third")
    conn = connect(str(db_path))
    core_links.add_link(conn, first, second, LinkKind.blocks, 1)
    conn.execute("CREATE UNIQUE INDEX test_links_one_from ON links(from_id)")
    links_before = tuple(
        tuple(row)
        for row in conn.execute(
            "SELECT from_id, to_id, kind FROM links ORDER BY from_id, to_id, kind"
        ).fetchall()
    )
    conn.close()
    changes.reset()

    with TestClient(app) as client:
        failed = client.post(
            "/api/links",
            json={"from_id": first, "to_id": third, "kind": "blocks"},
        )
        assert failed.status_code == 400
        assert failed.json()["error"]["code"] == "link_invalid"
        assert changes.calls == 0

    conn = connect(str(db_path))
    try:
        links_after = tuple(
            tuple(row)
            for row in conn.execute(
                "SELECT from_id, to_id, kind FROM links ORDER BY from_id, to_id, kind"
            ).fetchall()
        )
    finally:
        conn.close()
    assert links_after == links_before


def test_every_committed_ticket_and_day_write_signals(tmp_path: Path) -> None:
    # The signal says only "something committed", so the writes that change readiness
    # and the ones that do not are indistinguishable here on purpose: the readiness loop
    # answers a signal by re-running the whole readiness check.
    app, db_path, _clock, changes = _make_app(tmp_path)
    ticket_id = _create_direct(db_path)
    combined_proposal_id = _create_direct(db_path, title="Combined proposal")
    conn = connect(str(db_path))
    recap_ticket = tickets_data.create_ticket_from_external_work(
        conn,
        worker_type="coding",
        title="Recap-ready",
        kickoff_note="external note",
        target_stage="needs_approach",
        provided_values={"success": "success"},
        actor="chief",
        now=2,
        title_max_chars=TITLE_MAX_CHARS,
    )
    conn.close()
    changes.reset()
    with TestClient(app) as client:
        ordinary = client.put(
            f"/api/tickets/{ticket_id}/value/kickoff", json={"body": "ordinary edit"}
        )
        assert ordinary.status_code == 200, ordinary.text
        assert ordinary.json()["fields"]["kickoff"]["value"] == "ordinary edit"

        note = client.put(
            f"/api/tickets/{ticket_id}/notes/success", json={"user_note": "field note"}
        )
        assert note.status_code == 200, note.text
        assert note.json()["fields"]["success"]["user_note"] == "field note"

        recap = client.put(f"/api/tickets/{recap_ticket.id}/recap", json={"body": "recap"})
        assert recap.status_code == 200, recap.text
        assert recap.json()["recap"] == "recap"

        proposal = client.post(
            f"/api/tickets/{ticket_id}/propose/success",
            json={"body": "worker proposal"},
            headers=_AGENT,
        )
        assert proposal.status_code == 200, proposal.text
        assert proposal.json()["fields"]["success"]["proposal"]["body"] == "worker proposal"

        combined_proposal = client.post(
            f"/api/tickets/{combined_proposal_id}/propose",
            json={"body": "combined proposal", "recap": "combined recap"},
            headers=_AGENT,
        )
        assert combined_proposal.status_code == 200, combined_proposal.text
        assert (
            combined_proposal.json()["fields"]["success"]["proposal"]["body"] == "combined proposal"
        )
        assert combined_proposal.json()["recap"] == "combined recap"

        changes.reset()
        day = client.patch("/api/day/2099-03-04", json={"focus": "Focus"})
        assert day.status_code == 200, day.text
        assert day.json()["focus"] == "Focus"
        # The field write, then the day view that materializes the day it returns.
        assert changes.calls == 2

    conn = connect(str(db_path))
    try:
        persisted = tickets_data.read_ticket(conn, combined_proposal_id)
    finally:
        conn.close()
    assert fields_codec.get_slot(persisted.fields, "success").proposal is not None
    assert fields_codec.get_slot(persisted.fields, "success").proposal.body == "combined proposal"
    assert persisted.recap == "combined recap"
    assert persisted.ticket_status is TicketStatus.awaiting_approval


def test_parentage_project_and_sprint_writes_signal_like_every_other_commit(
    tmp_path: Path,
) -> None:
    app, db_path, _clock, changes = _make_app(tmp_path)
    ticket_id = _create_direct(db_path, title="Parent me")
    changes.reset()
    with TestClient(app) as client:
        project = client.post(
            "/api/projects",
            json={"name": "No automatic work project", "summary": "Created for the signal"},
        )
        assert project.status_code == 200, project.text
        project_id = project.json()["id"]
        project_edit = client.patch(
            f"/api/projects/{project_id}", json={"summary": "Updated for the signal"}
        )
        assert project_edit.status_code == 200, project_edit.text
        assert project_edit.json()["summary"] == "Updated for the signal"

        sprint = client.post(
            "/api/sprints",
            json={
                "name": "Signalling sprint",
                "date_start": "2099-05-01",
                "date_end": "2099-05-14",
            },
        )
        assert sprint.status_code == 200, sprint.text
        sprint_id = sprint.json()["id"]
        sprint_edit = client.patch(f"/api/sprints/{sprint_id}", json={"name": "Edited sprint"})
        assert sprint_edit.status_code == 200, sprint_edit.text
        assert sprint_edit.json()["name"] == "Edited sprint"

        item = client.post(
            "/api/items",
            json={"title": "Parent item", "project_id": project_id},
        )
        assert item.status_code == 200, item.text
        item_id = item.json()["id"]
        parented = client.post(f"/api/items/{item_id}/tickets", json={"ticket_id": ticket_id})
        assert parented.status_code == 200, parented.text
        assert parented.json()["rollup"]["needs_success"] == 1
        unparented = client.delete(f"/api/items/{item_id}/tickets/{ticket_id}")
        assert unparented.status_code == 200, unparented.text
        assert unparented.json()["rollup"]["needs_success"] == 0

    # Seven committed writes: the project create and edit, the sprint create and edit,
    # the item create, and the two parentage changes.
    assert changes.calls == 7


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


def test_routes_do_not_own_readiness_policy() -> None:
    root = Path(__file__).resolve().parents[2]
    forbidden = {
        "WorkerStepReadinessLoop",
        "get_system_a",
        "Sa",
        "_poke",
        "system_a",
        "worker_step_readiness_loop",
    }
    for relative in ("src/planner/tickets/api.py", "src/planner/days/api.py"):
        source = (root / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        assert forbidden.isdisjoint(names | attributes)
        assert ".wake(" not in source

    server_source = (root / "src/planner/core/server.py").read_text(encoding="utf-8")
    assert "app.state.system_a" not in server_source
    assert "app_.state.system_a" not in server_source
