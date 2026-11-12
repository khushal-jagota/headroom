"""Permanent deletion crosses authority, private files, and shared evidence boundaries."""

import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from sqlite3 import Connection, IntegrityError
from threading import Event
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from tests.support.api_app import _app
from tests.support.navigation_current import current_navigation_database
from tests.unit.test_dataset_persistence import candidate, collect, dataset
from tests.unit.test_goal_pages import create
from tests.unit.test_measure_persistence import NOW, run, source, taken
from tests.unit.test_measure_persistence import collect as collect_measure

from planner.cli import http
from planner.cli.main import main
from planner.core.clock import TestClock as Clock
from planner.core.contracts import OWNER_PRINCIPAL, PlannerError, Principal, PrincipalKind
from planner.core.db import connect
from planner.files import managed_files
from planner.files.goal_files import write_file
from planner.files.logic.paths import goal_files_root
from planner.goals import actions, data, views
from planner.managers.data import create_manager
from planner.measures.contracts import CandidateObservation
from planner.navigation import data as navigation


def test_delete_cli_requires_confirmation_and_uses_encoded_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def send(method: str, path: str, **kwargs: Any) -> Any:
        calls.append((method, path))
        return {"goal_id": "goal /one", "title": "Example"}

    monkeypatch.setattr(http, "send", send)
    runner = CliRunner()
    refused = runner.invoke(main, ["goal", "delete", "goal /one", "--json"])
    assert refused.exit_code != 0
    assert "requires --yes" in refused.output and calls == []
    accepted = runner.invoke(main, ["goal", "delete", "goal /one", "--yes", "--json"])
    assert accepted.exit_code == 0, accepted.output
    assert json.loads(accepted.output)["goal_id"] == "goal /one"
    assert calls == [("DELETE", "/api/goals/goal%20%2Fone")]


def test_delete_api_allows_owner_chief_and_holder_but_rejects_other_manager(
    tmp_db: Connection,
) -> None:
    clock = Clock(datetime.fromtimestamp(NOW, UTC))
    holder, other = [
        create_manager(tmp_db, title=title, project_id="project_vylo", clock=clock)
        for title in ("Holder", "Other")
    ]
    with TestClient(_app(tmp_db)) as client:
        for headers in (
            {},
            {"X-Plan-Actor": "chief"},
            {"X-Plan-Actor": "manager", "X-Plan-Manager-ID": holder.id},
        ):
            goal = create(tmp_db, holder_kind="manager", holder_id=holder.id)
            denied = client.delete(
                f"/api/goals/{goal.id}",
                headers={"X-Plan-Actor": "manager", "X-Plan-Manager-ID": other.id},
            )
            assert denied.status_code == 400, denied.text
            assert denied.json()["error"]["code"] == "agent_forbidden"
            assert data.read_goal(tmp_db, goal.id).title == goal.title
            deleted = client.delete(f"/api/goals/{goal.id}", headers=headers)
            assert deleted.status_code == 200, deleted.text
            assert deleted.json() == {"goal_id": goal.id, "title": goal.title}
            assert client.get(f"/api/goals/{goal.id}").status_code == 404
        chief_goal = create(tmp_db)
        denied = client.delete(
            f"/api/goals/{chief_goal.id}",
            headers={"X-Plan-Actor": "manager", "X-Plan-Manager-ID": holder.id},
        )
        assert denied.status_code == 400


def test_delete_cleans_private_state_and_navigation_but_preserves_shared_evidence(
    tmp_db: Connection,
) -> None:
    goal = create(tmp_db, completed=True, deadline="2026-10-27")
    survivor = create(tmp_db, title="Keep", deadline="2026-10-26")
    source_id = source(tmp_db, responsible=Principal(PrincipalKind.goal, goal.id))
    item = dataset(tmp_db, source_id)
    measure = taken(tmp_db, source_id)
    run(tmp_db, source_id)
    collect_measure(tmp_db, source_id, (CandidateObservation(measure.id, "2026-10-27", 12),))
    collect(tmp_db, item, candidate(item, "2026-10-27", "US"))
    shared_before = tuple(tuple(row) for row in tmp_db.execute("SELECT * FROM dataset_results"))
    evidence_tables = (
        "measures",
        "measure_observations",
        "datasets",
        "dataset_results",
        "dataset_rows",
        "measure_source_runs",
    )
    evidence = {
        table: tuple(tuple(row) for row in tmp_db.execute(f"SELECT * FROM {table}"))
        for table in evidence_tables
    }
    path = Path(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    write_file(tmp_db, OWNER_PRINCIPAL, goal.id, str(path), "artifacts/note.md", "Delete")
    write_file(tmp_db, OWNER_PRINCIPAL, survivor.id, str(path), "artifacts/note.md", "Keep")
    with tmp_db:
        tmp_db.execute(
            "INSERT INTO notification_push_subscriptions "
            "VALUES('sub','https://push.example', 'key','auth',1,1,NULL)"
        )
        tmp_db.execute(
            "INSERT INTO notification_deliveries(subject_kind,subject_id,notification_type,"
            "generation,subscription_id,title,body,route,tag,created_at,status,next_attempt_at) "
            "VALUES('goal',?,'awaiting_reply',1,'sub','Goal','Reply','/goal','goal',1,'pending',1)",
            (goal.id,),
        )
        tmp_db.execute(
            "INSERT INTO notification_attention_state VALUES('goal',?,'awaiting_reply',1,1)",
            (goal.id,),
        )
        tmp_db.execute(
            "INSERT INTO notification_attention_edges VALUES('goal',?,'awaiting_reply',1,1,0)",
            (goal.id,),
        )
        tmp_db.execute(
            "INSERT INTO manager_wakes(target_kind,target_id,source_kind,source_revision,"
            "summary,created_at) VALUES('goal',?,'turn_failure',1,'old',1)",
            (goal.id,),
        )
    now = datetime.fromtimestamp(NOW, UTC)
    before = views.list_json(tmp_db, now)["outcome_summary"]
    assert before["hits"] == 1 and before["misses"] == 1
    survivor_before = data.read_goal(tmp_db, survivor.id)
    with current_navigation_database(path) as conn:
        assert any(
            row["kind"] == "goal" and row["id"] == goal.id
            for row in navigation.snapshot(conn)["records"]
        )
        actions.delete_goal(conn, goal.id, principal=OWNER_PRINCIPAL, now=NOW + 1)
        assert not any(
            row["kind"] == "goal" and row["id"] == goal.id
            for row in navigation.snapshot(conn)["records"]
        )
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            tuple(tuple(row) for row in conn.execute("SELECT * FROM dataset_results"))
            == shared_before
        )
        assert conn.execute(
            "SELECT responsible_kind,responsible_goal_id FROM measure_sources WHERE id=?",
            (source_id,),
        ).fetchone()[:] == ("chief", None)
        for table in evidence_tables:
            assert (
                tuple(tuple(row) for row in conn.execute(f"SELECT * FROM {table}"))
                == evidence[table]
            )
        assert data.read_goal(conn, survivor.id) == survivor_before
        assert views.list_json(conn, now)["outcome_summary"] == {
            "hits": 0,
            "misses": 1,
            "rate": 0.0,
        }
        assert (
            conn.execute(
                "SELECT 1 FROM notification_deliveries WHERE subject_kind='goal' AND subject_id=?",
                (goal.id,),
            ).fetchone()
            is None
        )
        assert (
            conn.execute(
                "SELECT 1 FROM notification_attention_state "
                "WHERE subject_kind='goal' AND subject_id=?",
                (goal.id,),
            ).fetchone()
            is None
        )
        assert (
            conn.execute(
                "SELECT 1 FROM manager_wakes WHERE target_kind='goal' AND target_id=?", (goal.id,)
            ).fetchone()
            is None
        )
    assert not (goal_files_root(str(path)) / goal.id).exists()
    assert (goal_files_root(str(path)) / survivor.id / "artifacts/note.md").read_text() == "Keep"


def test_failed_database_delete_restores_goal_files_and_source_responsibility(
    tmp_db: Connection,
) -> None:
    goal = create(tmp_db)
    source_id = source(tmp_db, responsible=Principal(PrincipalKind.goal, goal.id))
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    write_file(tmp_db, OWNER_PRINCIPAL, goal.id, path, "artifacts/note.md", "Restore")
    with tmp_db:
        tmp_db.execute(
            "CREATE TRIGGER refuse_goal_delete BEFORE DELETE ON goals "
            "BEGIN SELECT RAISE(ABORT,'delete failed'); END"
        )
    with pytest.raises(IntegrityError, match="delete failed"):
        actions.delete_goal(tmp_db, goal.id, principal=OWNER_PRINCIPAL, now=NOW + 1)
    assert data.read_goal(tmp_db, goal.id) == goal
    assert (goal_files_root(path) / goal.id / "artifacts/note.md").read_text() == "Restore"
    assert (
        tmp_db.execute(
            "SELECT responsible_goal_id FROM measure_sources WHERE id=?", (source_id,)
        ).fetchone()[0]
        == goal.id
    )


def test_file_write_serializes_with_delete_and_cannot_recreate_deleted_folder(
    tmp_db: Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    goal = create(tmp_db)
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    entered, release, deleting = Event(), Event(), Event()
    original_write = managed_files.write_text

    def held_write(root: Path, relative_path: str, content: str, label: str) -> None:
        entered.set()
        assert release.wait(5)
        original_write(root, relative_path, content, label)

    monkeypatch.setattr(managed_files, "write_text", held_write)

    def write() -> None:
        with closing(connect(path)) as conn:
            write_file(conn, OWNER_PRINCIPAL, goal.id, path, "artifacts/overlap.md", "Draft")

    def delete() -> None:
        with closing(connect(path)) as conn:
            deleting.set()
            actions.delete_goal(conn, goal.id, principal=OWNER_PRINCIPAL, now=NOW + 1)

    with ThreadPoolExecutor(max_workers=2) as executor:
        writing = executor.submit(write)
        assert entered.wait(5)
        deletion = executor.submit(delete)
        try:
            assert deleting.wait(5)
            with pytest.raises(TimeoutError):
                deletion.result(timeout=0.05)
        finally:
            release.set()
        writing.result(timeout=5)
        deletion.result(timeout=5)
    assert not (goal_files_root(path) / goal.id).exists()
    with pytest.raises(PlannerError, match="goal not found"):
        write_file(tmp_db, OWNER_PRINCIPAL, goal.id, path, "artifacts/late.md", "Late")
    assert not (goal_files_root(path) / goal.id).exists()


def test_delete_rejects_outer_transaction_before_touching_files(tmp_db: Connection) -> None:
    goal = create(tmp_db)
    path = str(tmp_db.execute("PRAGMA database_list").fetchone()[2])
    write_file(tmp_db, OWNER_PRINCIPAL, goal.id, path, "artifacts/note.md", "Keep")
    tmp_db.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(PlannerError, match="needs its own transaction"):
            actions.delete_goal(tmp_db, goal.id, principal=OWNER_PRINCIPAL, now=NOW)
    finally:
        tmp_db.rollback()
    assert (goal_files_root(path) / goal.id / "artifacts/note.md").read_text() == "Keep"
    assert data.read_goal(tmp_db, goal.id) == goal
