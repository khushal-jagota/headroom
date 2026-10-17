"""Publication admission uses isolated synthetic state and the real runtime lease."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.conversation.lifecycle_storage import ConversationRuntimeAlreadyOwned
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.navigation import snapshot


def make_app(tmp_path: Path, *, test_mode: bool = True) -> FastAPI:
    path = tmp_path / "planner.db"
    conn = connect(str(path))
    create_schema(conn)
    conn.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(path),
            "PLAN_LOGS_DIR": str(tmp_path / "logs"),
            "PLAN_DISPATCHER_LOCK_PATH": str(tmp_path / "polling.lock"),
            "PLAN_HERMES_HOME": str(tmp_path / "hermes"),
        },
    )
    if not test_mode:
        from dataclasses import replace

        config = replace(config, test_mode=False, dispatch_enabled=True)
    return create_app(
        config,
        build_clock(config),
        lambda: connect(str(path)),
        conversation_system_for_test=InMemoryConversationSystem() if test_mode else None,
    )


def epoch(tmp_path: Path) -> str:
    conn = connect(str(tmp_path / "planner.db"))
    try:
        return snapshot(conn)["epoch"]
    finally:
        conn.close()


def test_real_owner_rejects_second_injected_runtime_before_epoch_rotation(tmp_path: Path) -> None:
    app = make_app(tmp_path)
    before = epoch(tmp_path)
    with TestClient(app) as first:
        started = epoch(tmp_path)
        assert started != before
        assert app.state.navigation_delivery.ready
        second = make_app(tmp_path)
        with pytest.raises(ConversationRuntimeAlreadyOwned):
            with TestClient(second):
                pass
        assert epoch(tmp_path) == started
        assert first.get("/api/meta").status_code == 200
    assert not app.state.navigation_delivery.ready
    with TestClient(make_app(tmp_path)):
        assert epoch(tmp_path) != started


def test_audit_failure_keeps_epoch_and_releases_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.tickets import data

    app = make_app(tmp_path)
    before = epoch(tmp_path)
    original = data.audit_ticket_registry_integrity

    def fail(_conn: object) -> None:
        raise RuntimeError("synthetic audit failure")

    monkeypatch.setattr(data, "audit_ticket_registry_integrity", fail)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        with TestClient(app):
            pass
    assert epoch(tmp_path) == before
    assert not app.state.navigation_delivery.ready
    monkeypatch.setattr(data, "audit_ticket_registry_integrity", original)
    with TestClient(make_app(tmp_path)):
        assert epoch(tmp_path) != before


def test_notifications_reconcile_before_epoch_and_task_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.core.loops import BackgroundLoops
    from planner.notifications.runtime import NotificationLoop

    app = make_app(tmp_path, test_mode=False)
    before = epoch(tmp_path)
    seen: list[str] = []
    reconcile = NotificationLoop.reconcile_imported_state
    start = BackgroundLoops.start

    def observe_reconcile(self: NotificationLoop) -> None:
        assert epoch(tmp_path) == before
        assert not app.state.navigation_delivery.ready
        seen.append("reconcile")
        reconcile(self)

    def observe_start(self: BackgroundLoops, tick_seconds: int) -> None:
        assert epoch(tmp_path) != before
        assert app.state.navigation_delivery.ready
        seen.append("start")
        # Start real loops with no Tickets and isolated configuration.
        start(self, tick_seconds)

    monkeypatch.setattr(NotificationLoop, "reconcile_imported_state", observe_reconcile)
    monkeypatch.setattr(BackgroundLoops, "start", observe_start)
    with TestClient(app):
        assert seen == ["reconcile", "start"]
    assert not app.state.navigation_delivery.ready


def test_lost_lifecycle_reconciliation_precedes_epoch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from planner.navigation import data

    app = make_app(tmp_path)
    conn = connect(str(tmp_path / "planner.db"))
    conn.execute(
        "INSERT INTO conversations(conversation_id,backend_key,workspace_folder,access,created_at) "
        "VALUES('lost','codex','/synthetic','full',0)"
    )
    conn.execute(
        "INSERT INTO conversation_lifecycle_facts VALUES('lost','turn','1',1,'turn','running',NULL)"
    )
    conn.commit()
    conn.close()
    original = data.reset_epoch
    observed: list[str] = []

    def reset_after_reconciliation(connection: Any) -> Any:
        status = connection.execute(
            "SELECT status FROM conversation_lifecycle_facts WHERE conversation_id='lost'"
        ).fetchone()[0]
        assert status == "unavailable"
        observed.append(status)
        return original(connection)

    monkeypatch.setattr(data, "reset_epoch", reset_after_reconciliation)
    with TestClient(app):
        assert observed == ["unavailable"]


def test_older_backup_fresh_runtime_resets_cursor_and_proves_only_surviving_commands(
    tmp_path: Path,
) -> None:
    import json
    import sqlite3
    from contextlib import closing

    from planner.navigation.command_contracts import FENCE_HEADER

    path = tmp_path / "planner.db"
    backup_path = tmp_path / "synthetic-older-backup.db"

    def backup_from_readonly(source: Path, destination: Path) -> None:
        # Both paths belong to this synthetic fixture. The previous application
        # lifespan has stopped before SQLite opens either backup destination.
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
            with closing(sqlite3.connect(destination)) as writer:
                reader.backup(writer)

    def create_project(client: TestClient, name: str) -> str:
        response = client.post(
            "/api/projects", json={"name": name, "summary": "Synthetic", "priority": "P2"}
        )
        assert response.status_code == 200, response.text
        return str(response.headers[FENCE_HEADER.decode()])

    first_app = make_app(tmp_path)
    with TestClient(first_app) as client:
        surviving_fence = create_project(client, "Survives the backup")
        backed_up = client.get("/api/navigation/snapshot").json()
    assert not first_app.state.navigation_delivery.ready
    backup_from_readonly(path, backup_path)

    second_app = make_app(tmp_path)
    with TestClient(second_app) as client:
        lost_fence = create_project(client, "Absent from the older backup")
        lost_snapshot = client.get("/api/navigation/snapshot").json()
    assert not second_app.state.navigation_delivery.ready
    assert lost_snapshot["snapshot"]["epoch"] != backed_up["snapshot"]["epoch"]

    backup_from_readonly(backup_path, path)
    restored_app = make_app(tmp_path)
    with TestClient(restored_app) as client:
        fresh = client.get("/api/navigation/snapshot").json()
        assert fresh["snapshot"]["scope"] == backed_up["snapshot"]["scope"]
        assert fresh["snapshot"]["epoch"] not in {
            backed_up["snapshot"]["epoch"],
            lost_snapshot["snapshot"]["epoch"],
        }
        # A divergent history can pass the lost command's old numeric revision.
        # It still cannot substitute for its absent atomic commit witness.
        for number in range(int(lost_snapshot["snapshot"]["revision"]) + 2):
            create_project(client, f"Divergent project {number}")
        current = client.get("/api/navigation/snapshot").json()
        assert int(current["snapshot"]["revision"]) > int(lost_snapshot["snapshot"]["revision"])

        reset = client.get(
            "/api/navigation/events", headers={"Last-Event-ID": lost_snapshot["cursor"]}
        )
        assert reset.status_code == 200
        assert reset.headers["content-type"].startswith("text/event-stream")
        assert "event: reset\n" in reset.text
        payloads = [
            line.removeprefix("data: ")
            for line in reset.text.splitlines()
            if line.startswith("data: ")
        ]
        assert len(payloads) == 1
        assert json.loads(payloads[0])["reason"] == "incompatible_continuity"
        assert "event: batch" not in reset.text

        lost_proof = client.get("/api/navigation/snapshot", params={"fence": lost_fence})
        assert lost_proof.status_code == 200
        assert lost_proof.json()["proof"] == {"status": "unknown", "reason": "missing_witness"}
        surviving_proof = client.get("/api/navigation/snapshot", params={"fence": surviving_fence})
        assert surviving_proof.status_code == 200
        assert surviving_proof.json()["proof"] == {"status": "included", "reason": None}
        names = {
            record["value"]["name"]
            for record in current["snapshot"]["records"]
            if record["kind"] == "project"
        }
        assert "Survives the backup" in names
        assert "Absent from the older backup" not in names
    assert not restored_app.state.navigation_delivery.ready
