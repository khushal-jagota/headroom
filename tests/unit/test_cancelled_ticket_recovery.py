"""Upgrade/recovery boundaries that cannot be proved by runtime Ticket decoding."""

import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from planner.cli import http
from planner.cli.main import main
from planner.core.db import create_schema
from planner.core.errors import PlannerError
from planner.maintenance import recovery
from planner.server_lifecycle.supervisor import PortScopedServerLifecycleLease
from planner.tickets.data import delete_ticket_records

_process_probe = recovery.require_no_backend_processes


@pytest.fixture(autouse=True)
def isolated_backend_process_inventory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    probe = recovery.require_no_backend_processes
    processes = tmp_path / "proc"
    processes.mkdir()
    monkeypatch.setattr(
        recovery,
        "require_no_backend_processes",
        lambda ids, database: probe(ids, database, proc=processes),
    )


def _ticket(conn: sqlite3.Connection, ticket_id: str, stage: str = "cancelled") -> None:
    conn.execute(
        "INSERT INTO tickets(id,title,worker_type,employee_backend,ceiling,field_values,"
        "stage,created_at,updated_at) VALUES (?,?,'personal','codex','done',?,?,1,1)",
        (
            ticket_id,
            f"Title {ticket_id}",
            json.dumps({"brief": "Keep", "retirement": "Old"}),
            stage,
        ),
    )


def _path(conn: sqlite3.Connection) -> Path:
    return Path(conn.execute("PRAGMA database_list").fetchone()[2])


def _old_revision(conn: sqlite3.Connection) -> None:
    conn.execute("UPDATE alembic_version SET version_num='current_model_variants'")


def test_upgrade_refuses_whole_transaction_and_names_offline_resolution(
    tmp_db: sqlite3.Connection,
) -> None:
    _ticket(tmp_db, "t_cancelled")
    _ticket(tmp_db, "t_personal", "needs_brief")
    _old_revision(tmp_db)
    before = list(tmp_db.iterdump())
    with pytest.raises(RuntimeError, match="archive-cancelled-tickets") as error:
        create_schema(tmp_db)
    assert "t_cancelled (Title t_cancelled)" in str(error.value)
    assert list(tmp_db.iterdump()) == before
    # A custom Stage must not be silently skipped or left gating a removed field.
    tmp_db.execute("DELETE FROM tickets WHERE id='t_cancelled'")
    definition = json.loads(
        tmp_db.execute(
            "SELECT definition_json FROM worker_types WHERE worker_type='coding'"
        ).fetchone()[0]
    )
    stage = next(stage for stage in definition["stages"] if not stage["is_terminal"])
    stage["gating_field"] = "retirement"
    tmp_db.execute(
        "UPDATE worker_types SET definition_json=? WHERE worker_type='coding'",
        (json.dumps(definition),),
    )
    before = list(tmp_db.iterdump())
    with pytest.raises(RuntimeError, match="Worker type coding") as custom:
        create_schema(tmp_db)
    assert stage["id"] in str(custom.value)
    assert list(tmp_db.iterdump()) == before


def test_archive_then_delete_preserves_private_snapshot_artifacts_and_upgrade_retry(
    tmp_db: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ticket(tmp_db, "t_cancelled")
    for index in range(19):
        _ticket(tmp_db, f"t_personal_{index}", "needs_brief")
    _old_revision(tmp_db)
    definition = json.loads(
        tmp_db.execute(
            "SELECT definition_json FROM worker_types WHERE worker_type='coding'"
        ).fetchone()[0]
    )
    definition["fields"].append({"id": "retirement", "label": "Retirement"})
    definition["stages"].append(
        {
            "id": "cancelled",
            "label": "Retired",
            "gating_field": None,
            "is_terminal": True,
            "ownership_mode": None,
        }
    )
    tmp_db.execute(
        "UPDATE worker_types SET definition_json=? WHERE worker_type='coding'",
        (json.dumps(definition),),
    )
    tmp_db.execute("INSERT INTO days(id,created_at,updated_at) VALUES ('day_old',1,1)")
    tmp_db.execute("INSERT INTO day_tickets VALUES ('day_old','t_cancelled',0)")
    tmp_db.execute("INSERT INTO day_tickets VALUES ('day_old','t_personal_0',1)")
    tmp_db.execute("INSERT INTO ticket_blocks VALUES ('t_cancelled','t_personal_0')")
    tmp_db.execute(
        "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,created_at) "
        "VALUES ('conv_old','codex','/tmp','full',1)"
    )
    tmp_db.execute("INSERT INTO ticket_conversations VALUES ('conv_old','t_cancelled')")
    tmp_db.execute(
        "INSERT INTO conversation_events VALUES ('conv_old',1,'prompt','{\"text\":\"History\"}',1)"
    )
    tmp_db.execute(
        "INSERT INTO ticket_revision_feedback VALUES "
        "('t_cancelled','needs_review','{\"feedback\":\"Keep\"}',1,1,1)"
    )
    tmp_db.execute(
        "INSERT INTO feedback_notes(id,text,state,ticket_id,created_at,updated_at,handled_at) "
        "VALUES ('feedback_old','Substance','handled','t_cancelled',1,1,1)"
    )
    tmp_db.execute(
        "INSERT INTO notification_attention_state VALUES ('ticket','t_cancelled','assigned',1,1)"
    )
    artifact = tmp_path / "files" / "tickets" / "t_cancelled" / "evidence.txt"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("Substance")
    archive = tmp_path / "archive"
    database = _path(tmp_db)
    result = recovery.archive_cancelled_tickets(database, archive)
    assert result["deleted_ticket_ids"] == ["t_cancelled"]
    manifest = json.loads((archive / "manifest.json").read_text())
    assert manifest["database"] == str(database)
    assert manifest["tickets"][0]["field_values"] == json.dumps(
        {"brief": "Keep", "retirement": "Old"}
    )
    assert manifest["artifact_references"] == ["/files/tickets/t_cancelled"]
    assert (
        hashlib.sha256((archive / "database.sqlite3").read_bytes()).hexdigest()
        == manifest["snapshot_sha256"]
    )
    assert (archive.stat().st_mode & 0o777) == 0o700
    assert ((archive / "database.sqlite3").stat().st_mode & 0o777) == 0o600
    assert artifact.read_text() == "Substance"
    with sqlite3.connect(archive / "database.sqlite3") as archived:
        assert archived.execute("SELECT count(*) FROM tickets").fetchone()[0] == 20
        for table in (
            "ticket_blocks",
            "ticket_conversations",
            "conversation_events",
            "ticket_revision_feedback",
            "notification_attention_state",
        ):
            assert archived.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 1
        assert (
            archived.execute("SELECT ticket_id FROM feedback_notes").fetchone()[0] == "t_cancelled"
        )
    assert tuple(tmp_db.execute("SELECT ticket_id,position FROM day_tickets").fetchone()) == (
        "t_personal_0",
        0,
    )
    assert tmp_db.execute("SELECT updated_at FROM days").fetchone()[0] > 1
    assert (
        tmp_db.execute("SELECT updated_at FROM tickets WHERE id='t_personal_0'").fetchone()[0] > 1
    )
    assert tmp_db.execute("SELECT ticket_id FROM feedback_notes").fetchone()[0] is None
    for table in (
        "ticket_blocks",
        "ticket_conversations",
        "ticket_revision_feedback",
        "notification_attention_state",
    ):
        assert tmp_db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert tmp_db.execute("SELECT count(*) FROM conversation_events").fetchone()[0] == 1
    create_schema(tmp_db)
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 19
    assert all(
        json.loads(row[0]) == {"brief": "Keep"}
        for row in tmp_db.execute("SELECT field_values FROM tickets")
    )
    stored = json.loads(
        tmp_db.execute(
            "SELECT definition_json FROM worker_types WHERE worker_type='coding'"
        ).fetchone()[0]
    )
    assert "retirement" not in {field["id"] for field in stored["fields"]}
    assert "cancelled" not in {stage["id"] for stage in stored["stages"]}


@pytest.mark.parametrize(
    "failure", ["archive", "delete", "claim", "ceiling", "activity", "ownership", "reference"]
)
def test_recovery_failure_never_partially_deletes(
    tmp_db: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    _ticket(tmp_db, "t_a")
    _ticket(tmp_db, "t_b")
    tmp_db_uid = _path(tmp_db).stat().st_uid
    if failure == "claim":
        tmp_db.execute("UPDATE tickets SET worker_step_claim='out' WHERE id='t_b'")
    if failure == "ceiling":
        _ticket(tmp_db, "t_holder", "needs_brief")
        tmp_db.execute(
            "UPDATE tickets SET ceiling_holder=? WHERE id='t_holder'",
            (json.dumps({"kind": "ticket", "id": "t_b"}),),
        )
    if failure == "archive":
        monkeypatch.setattr(
            recovery, "_durable_write", lambda *args: (_ for _ in ()).throw(OSError("disk full"))
        )
    if failure == "activity":
        monkeypatch.setattr(
            recovery,
            "require_no_backend_processes",
            lambda *args: (_ for _ in ()).throw(RuntimeError("unverifiable")),
        )
    if failure == "delete":
        original = delete_ticket_records

        def delete(conn: sqlite3.Connection, ticket_id: str, **kwargs: Any) -> Any:
            if ticket_id == "t_b":
                raise RuntimeError("post-archive failure")
            return original(conn, ticket_id, **kwargs)

        monkeypatch.setattr(recovery, "delete_ticket_records", delete)
    if failure == "ownership":
        monkeypatch.setattr(os, "geteuid", lambda: tmp_db_uid + 1)
    if failure == "reference":
        tmp_db.execute("CREATE TABLE protected_reference(ticket_id TEXT REFERENCES tickets(id))")
        tmp_db.execute("INSERT INTO protected_reference VALUES ('t_b')")
    before = list(tmp_db.iterdump())
    with pytest.raises((RuntimeError, OSError, ValueError, PlannerError)) as refused:
        recovery.archive_cancelled_tickets(_path(tmp_db), tmp_path / "archive")
    if failure in {"claim", "ceiling", "delete"}:
        assert "t_b (Title t_b)" in str(refused.value)
        assert "nothing deleted" in str(refused.value)
    assert list(tmp_db.iterdump()) == before


def test_offline_cli_requires_owner_confirmation_and_exclusive_stopped_lease(
    tmp_db: sqlite3.Connection,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ticket(tmp_db, "t_cancelled")
    _old_revision(tmp_db)
    monkeypatch.setenv("PLAN_DB_PATH", str(_path(tmp_db)))
    lease_path = tmp_path / "lifecycle.lock"
    monkeypatch.setenv("PLAN_SERVER_LIFECYCLE_LEASE_PATH", str(lease_path))
    for key in ("PLAN_TICKET_ID", "PLAN_SPRINT_ITEM_ID", "PLAN_AGENT_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("PLAN_ACTOR", "worker")
    monkeypatch.setattr(
        http, "send", lambda *a, **kw: pytest.fail("Offline command contacted HTTP")
    )
    args = ["maintenance", "archive-cancelled-tickets", "--archive-dir", str(tmp_path / "archive")]
    runner = CliRunner()
    assert runner.invoke(main, args).exit_code != 0
    assert "requires the owner" in runner.invoke(main, args + ["--yes"]).output
    monkeypatch.setenv("PLAN_ACTOR", "owner")
    lease = PortScopedServerLifecycleLease(lease_path, 0)
    lease.acquire()
    try:
        refused = runner.invoke(main, args + ["--yes"])
        assert refused.exit_code != 0
        assert "supervisor" in refused.output
        assert not (tmp_path / "archive").exists()
    finally:
        lease.release()
    completed = runner.invoke(main, args + ["--yes"])
    assert completed.exit_code == 0, completed.output
    assert str(_path(tmp_db)) in completed.output
    create_schema(tmp_db)
    assert tmp_db.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0


def test_pre_cutover_snapshot_recovers_materialized_cancelled_records_without_older_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.unit.test_db import _migration_tree_through

    from planner.core.db import connect
    from planner.core.migrations import planning_retirement

    database = tmp_path / "before-cutover.db"
    conn = connect(str(database))
    try:
        # Real ancestor schema, not a current schema with an edited version marker.
        with monkeypatch.context() as historical:
            _migration_tree_through(
                tmp_path, historical, "a_refused_codex_login_outlives_a_restart"
            )
            create_schema(conn)
        conn.execute(
            "INSERT INTO tickets(id,title,worker_type,employee_backend,ceiling,field_values,"
            "stage,created_at,updated_at) VALUES "
            "('t_planning','Old planning','planning-day','codex','done','{}','needs_review',1,1)"
        )
        row = dict(conn.execute("SELECT * FROM tickets WHERE id='t_planning'").fetchone())
        monkeypatch.setattr(
            planning_retirement,
            "COHORT",
            [
                {
                    "ticket_id": row["id"],
                    "original_worker_type": row["worker_type"],
                    "original_stage": row["stage"],
                    "conversation_id": row["conversation_id"],
                    "worker_step_claim": "none",
                    "worker_step_claim_revision": 0,
                    "has_pending_proposal": False,
                    "original_record_sha256": planning_retirement._row_hash(row),
                }
            ],
        )
        before = list(conn.iterdump())
        with pytest.raises(RuntimeError, match="archive-cancelled-tickets"):
            create_schema(conn)
        assert list(conn.iterdump()) == before
        archive = tmp_path / "archive-historical"
        recovered = recovery.archive_cancelled_tickets(database, archive)
        assert recovered["deleted_ticket_ids"] == ["t_planning"]
        with sqlite3.connect(archive / "database.sqlite3") as original:
            assert original.execute("SELECT stage FROM tickets").fetchone()[0] == "needs_review"
            assert original.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
                "a_refused_codex_login_outlives_a_restart"
            )
        manifest = json.loads((archive / "manifest.json").read_text())
        assert manifest["tickets"][0]["stage"] == "cancelled"
        assert "retirement" in json.loads(manifest["tickets"][0]["field_values"])
        materialized = (archive / "prepared-database.sql").read_bytes()
        assert hashlib.sha256(materialized).hexdigest() == manifest["prepared_database_sha256"]
        create_schema(conn)
        assert conn.execute("SELECT count(*) FROM tickets").fetchone()[0] == 0
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == (
            "remove_ticket_retirement"
        )
    finally:
        conn.close()


@pytest.mark.parametrize(
    "activity", ["relative_target", "missing_database", "other_database", "unreadable_backend"]
)
def test_offline_activity_probe_uses_process_identity_and_its_own_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    activity: str,
) -> None:
    proc = tmp_path / "processes"
    process = proc / "99999999"
    process.mkdir(parents=True)
    process_cwd = tmp_path / "backend-workspace"
    process_cwd.mkdir()
    (process / "cwd").symlink_to(process_cwd, target_is_directory=True)
    database = process_cwd / "relative.db"
    db_env = b"PLAN_DB_PATH=relative.db\0"
    if activity == "missing_database":
        db_env = b""
    elif activity == "other_database":
        db_env = b"PLAN_DB_PATH=other.db\0"
    (process / "environ").write_bytes(b"PLAN_TICKET_ID=t_target\0" + db_env)
    (process / "cmdline").write_bytes(b"codex\0app-server")
    if activity == "unreadable_backend":
        original = Path.read_bytes

        def read(path: Path) -> bytes:
            if path == process / "environ":
                raise PermissionError("undumpable backend")
            return original(path)

        monkeypatch.setattr(Path, "read_bytes", read)
    if activity == "other_database":
        _process_probe({"t_target"}, database, proc=proc)
    else:
        with pytest.raises(RuntimeError, match="backend activity"):
            _process_probe({"t_target"}, database, proc=proc)
