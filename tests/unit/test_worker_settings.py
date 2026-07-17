from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from planner.conversation.hermes_backend_configuration import provision_planner_home_skills
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import EventKind
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS, StageOwnershipMode
from planner.worker_settings import api as worker_settings_api
from planner.worker_settings import service as worker_settings_service
from planner.worker_types.configuration import configured_worker_type_registry


def _app(
    tmp_path: Path, *, raise_server_exceptions: bool = True
) -> tuple[TestClient, Path]:
    db_path = tmp_path / "worker-settings.db"
    boot = connect(str(db_path))
    create_schema(boot)
    boot.close()
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
        },
    )

    def conn_factory() -> sqlite3.Connection:
        return connect(str(db_path))

    app = create_app(config, build_clock(config), conn_factory)
    return TestClient(app, raise_server_exceptions=raise_server_exceptions), db_path


def test_workers_api_composes_registry_with_managed_settings_and_emits_event(
    tmp_path: Path,
) -> None:
    client, db_path = _app(tmp_path)
    with client:
        index = client.get("/api/workers").json()
        assert [worker["worker_type"] for worker in index["workers"]] == list(
            configured_worker_type_registry().registered_worker_types()
        )
        detail = client.get("/api/workers/coding").json()
        assert "worker_type" not in detail
        assert detail["manifest"]["worker_type"] == "coding"
        assert (
            detail["settings"]["specialist_skill"]["name"]
            == "panels-worker-coding"
        )

        updated = client.put(
            "/api/workers/coding/stages/needs_success/default-ownership",
            json={"ownership_mode": "user"},
        )
        assert updated.status_code == 200
        assert (
            updated.json()["stage_ownership_defaults"]["needs_success"] == "user"
        )

        terminal = client.put(
            "/api/workers/coding/stages/done/default-ownership",
            json={"ownership_mode": "worker"},
        )
        assert terminal.status_code == 400
        assert terminal.json()["error"]["message"] == "terminal stage cannot have ownership"

        missing = client.get("/api/workers/not_a_worker")
        assert missing.status_code == 404

    conn = connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT entity_id, kind, payload FROM events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row["entity_id"] == "worker_coding"
        assert row["kind"] == EventKind.worker_settings_changed.value
        assert json.loads(row["payload"]) == {
            "worker_type": "coding",
            "changed": "stage_default_ownership",
        }
    finally:
        conn.close()


def test_api_skill_patch_materializes_runtime_skill_without_touching_ticket_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planner_home = tmp_path / "explicit-hermes-home"
    monkeypatch.setenv("PLAN_HERMES_HOME", str(planner_home))
    client, db_path = _app(tmp_path)
    conn = connect(str(db_path))
    try:
        ticket = tickets_data.create_ticket(
            conn,
            title="API session stays",
            actor="human",
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
            kickoff_note="go",
            worker_type="coding",
        )
        conn.execute(
            "UPDATE tickets SET employee_session_id = 'session_keep_api' WHERE id = ?",
            (ticket.id,),
        )
    finally:
        conn.close()

    with client:
        before = client.get("/api/workers/coding").json()["settings"]["specialist_skill"]
        saved_description = client.patch(
            "/api/workers/coding/skill",
            json={
                "description": "API materialized description",
            },
        )
        assert saved_description.status_code == 200
        assert (
            saved_description.json()["specialist_skill"]["description"]
            == "API materialized description"
        )
        assert (
            saved_description.json()["specialist_skill"]["markdown_body"]
            == before["markdown_body"]
        )

        saved_body = client.patch(
            "/api/workers/coding/skill",
            json={"markdown_body": "# API materialized\n\nManaged body\n"},
        )
        assert saved_body.status_code == 200
        assert (
            saved_body.json()["specialist_skill"]["description"]
            == "API materialized description"
        )
        assert (
            saved_body.json()["specialist_skill"]["markdown_body"]
            == "\n# API materialized\n\nManaged body\n"
        )

        rejected_empty = client.patch("/api/workers/coding/skill", json={})
        assert rejected_empty.status_code == 400
        assert (
            rejected_empty.json()["error"]["message"]
            == "specialist skill patch requires exactly one field"
        )

        rejected_both = client.patch(
            "/api/workers/coding/skill",
            json={"description": "Two", "markdown_body": "# Two\n"},
        )
        assert rejected_both.status_code == 400

    materialized = planner_home / "skills" / "panels-worker-coding" / "SKILL.md"
    skill_text = materialized.read_text(encoding="utf-8")
    assert 'description: "API materialized description"' in skill_text
    assert "# API materialized" in skill_text

    conn = connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT employee_session_id FROM tickets WHERE id = ?", (ticket.id,)
        ).fetchone()
        assert row["employee_session_id"] == "session_keep_api"
        events = conn.execute(
            "SELECT entity_id, kind, payload FROM events "
            "WHERE kind = ? ORDER BY id",
            (EventKind.worker_settings_changed.value,),
        ).fetchall()
        assert [row["entity_id"] for row in events] == ["worker_coding", "worker_coding"]
        assert [json.loads(row["payload"]) for row in events] == [
            {"worker_type": "coding", "changed": "skill"},
            {"worker_type": "coding", "changed": "skill"},
        ]
    finally:
        conn.close()


def test_skill_save_preserves_unknown_frontmatter_and_rejects_name_changes(
    tmp_path: Path,
) -> None:
    client, db_path = _app(tmp_path)
    settings_parent = db_path.parent
    registry = configured_worker_type_registry()
    worker_settings_service.read_worker_settings(settings_parent, registry, "coding")
    skill_path = (
        worker_settings_service.managed_worker_settings_root(settings_parent)
        / "coding"
        / "SKILL.md"
    )
    original = skill_path.read_text(encoding="utf-8")
    skill_path.write_text(original.replace("---\n", "---\nunknown-key: keep-me\n", 1))

    with client:
        rejected = client.put(
            "/api/workers/coding/skill",
            json={
                "name": "other-skill",
                "description": "Edited description",
                "markdown_body": "# Edited\n\nBody\n",
            },
        )
        assert rejected.status_code == 400
        assert rejected.json()["error"]["message"] == "specialist skill name is immutable"
        after_reject = client.get("/api/workers/coding").json()
        assert (
            after_reject["settings"]["candidate_specialist_skill"]["description"]
            == "Edited description"
        )
        assert after_reject["settings"]["specialist_skill"]["description"] != "Edited description"

        saved = client.put(
            "/api/workers/coding/skill",
            json={
                "description": "Edited description",
                "markdown_body": "# Edited\n\nBody\n",
            },
        )
        assert saved.status_code == 200
        body = saved.json()["specialist_skill"]
        assert body["name"] == "panels-worker-coding"
        assert body["description"] == "Edited description"
        assert body["markdown_body"].startswith("\n# Edited")

    written = skill_path.read_text(encoding="utf-8")
    assert "unknown-key: keep-me" in written
    assert 'name: "panels-worker-coding"' in written
    assert 'description: "Edited description"' in written

    conn = connect(str(db_path))
    try:
        events = conn.execute(
            "SELECT kind, payload FROM events WHERE entity_id = 'worker_coding' ORDER BY id"
        ).fetchall()
        assert [row["kind"] for row in events] == [EventKind.worker_settings_changed.value]
        assert json.loads(events[0]["payload"]) == {
            "worker_type": "coding",
            "changed": "skill",
        }
    finally:
        conn.close()


def test_stage_default_event_failure_restores_canonical_settings_and_api_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, db_path = _app(tmp_path, raise_server_exceptions=False)
    settings_parent = db_path.parent
    registry = configured_worker_type_registry()
    original = worker_settings_service.read_worker_settings(settings_parent, registry, "coding")
    root = worker_settings_service.managed_worker_settings_root(settings_parent)
    settings_path = root / "coding" / "settings.json"
    original_settings_bytes = settings_path.read_bytes()

    def fail_event(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced event failure")

    monkeypatch.setattr(worker_settings_api, "append_event", fail_event)
    with client:
        response = client.put(
            "/api/workers/coding/stages/needs_success/default-ownership",
            json={"ownership_mode": "user"},
        )
        assert response.status_code == 500
        detail = client.get("/api/workers/coding").json()

    assert settings_path.read_bytes() == original_settings_bytes
    assert (
        detail["settings"]["stage_ownership_defaults"]["needs_success"]
        == original.stage_ownership_defaults["needs_success"].value
    )
    conn = connect(str(db_path))
    try:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM events WHERE kind = ?",
                (EventKind.worker_settings_changed.value,),
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


def test_skill_event_failure_restores_canonical_and_runtime_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planner_home = tmp_path / "explicit-hermes-home"
    monkeypatch.setenv("PLAN_HERMES_HOME", str(planner_home))
    client, db_path = _app(tmp_path, raise_server_exceptions=False)
    settings_parent = db_path.parent
    registry = configured_worker_type_registry()
    worker_settings_service.save_specialist_skill(
        settings_parent,
        registry,
        "coding",
        {
            "description": "Old runtime description",
            "markdown_body": "# Old runtime\n\nBody\n",
        },
    )
    runtime_root = planner_home / "skills"
    worker_settings_service.materialize_specialist_skill(
        settings_parent,
        registry,
        "coding",
        runtime_root,
    )
    root = worker_settings_service.managed_worker_settings_root(settings_parent)
    skill_path = root / "coding" / "SKILL.md"
    runtime_skill_path = runtime_root / "panels-worker-coding" / "SKILL.md"
    original_skill_bytes = skill_path.read_bytes()
    original_runtime_bytes = runtime_skill_path.read_bytes()

    def fail_event(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced event failure")

    monkeypatch.setattr(worker_settings_api, "append_event", fail_event)
    with client:
        response = client.patch(
            "/api/workers/coding/skill",
            json={"description": "New runtime description"},
        )
        assert response.status_code == 500
        detail = client.get("/api/workers/coding").json()

    assert skill_path.read_bytes() == original_skill_bytes
    assert runtime_skill_path.read_bytes() == original_runtime_bytes
    assert detail["settings"]["specialist_skill"]["description"] == "Old runtime description"
    conn = connect(str(db_path))
    try:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM events WHERE kind = ?",
                (EventKind.worker_settings_changed.value,),
            ).fetchone()[0]
            == 0
        )
    finally:
        conn.close()


def test_skill_patch_preserves_concurrent_other_field_values(tmp_path: Path) -> None:
    registry = configured_worker_type_registry()
    worker_settings_service.save_specialist_skill(
        tmp_path,
        registry,
        "coding",
        {
            "description": "Original description",
            "markdown_body": "# Original\n\nBody\n",
        },
    )

    worker_settings_service.patch_specialist_skill(
        tmp_path,
        registry,
        "coding",
        {"markdown_body": "# Body winner\n\nSecond field\n"},
    )
    saved = worker_settings_service.patch_specialist_skill(
        tmp_path,
        registry,
        "coding",
        {"description": "Description winner"},
    )

    assert saved.specialist_skill.description == "Description winner"
    assert saved.specialist_skill.markdown_body == "\n# Body winner\n\nSecond field\n"


def test_corrupt_current_files_restore_exact_prior_good_revision(tmp_path: Path) -> None:
    registry = configured_worker_type_registry()
    worker_settings_service.save_specialist_skill(
        tmp_path,
        registry,
        "coding",
        {
            "description": "Prior good description",
            "markdown_body": "# Prior good\n\nBody\n",
        },
    )
    root = worker_settings_service.managed_worker_settings_root(tmp_path)
    settings_path = root / "coding" / "settings.json"
    skill_path = root / "coding" / "SKILL.md"
    prior_good_settings = settings_path.read_text(encoding="utf-8")
    prior_good_skill = skill_path.read_text(encoding="utf-8")

    settings_path.write_text("{not-json", encoding="utf-8")
    skill_path.write_text(
        "---\nname: other-skill\ndescription: corrupt\n---\n# Bad\n",
        encoding="utf-8",
    )

    recovered = worker_settings_service.read_worker_settings(tmp_path, registry, "coding")

    assert recovered.specialist_skill.description == "Prior good description"
    assert settings_path.read_text(encoding="utf-8") == prior_good_settings
    assert skill_path.read_text(encoding="utf-8") == prior_good_skill


@pytest.mark.parametrize("missing_file_name", ["settings.json", "SKILL.md"])
def test_missing_current_file_restores_exact_edited_last_known_good_revision(
    tmp_path: Path, missing_file_name: str
) -> None:
    registry = configured_worker_type_registry()
    worker_settings_service.update_stage_default_ownership(
        tmp_path,
        registry,
        "coding",
        "needs_plan",
        StageOwnershipMode.user,
    )
    worker_settings_service.save_specialist_skill(
        tmp_path,
        registry,
        "coding",
        {
            "description": "Edited last known good description",
            "markdown_body": "# Edited last known good\n\nBody\n",
        },
    )
    root = worker_settings_service.managed_worker_settings_root(tmp_path)
    settings_path = root / "coding" / "settings.json"
    skill_path = root / "coding" / "SKILL.md"
    edited_settings = settings_path.read_text(encoding="utf-8")
    edited_skill = skill_path.read_text(encoding="utf-8")

    (root / "coding" / missing_file_name).unlink()

    recovered = worker_settings_service.read_worker_settings(tmp_path, registry, "coding")

    assert recovered.stage_ownership_defaults["needs_plan"] == StageOwnershipMode.user
    assert (
        recovered.specialist_skill.description
        == "Edited last known good description"
    )
    assert settings_path.read_text(encoding="utf-8") == edited_settings
    assert skill_path.read_text(encoding="utf-8") == edited_skill


def test_concurrent_stage_updates_keep_both_values_and_leave_no_temp_files(
    tmp_path: Path,
) -> None:
    registry = configured_worker_type_registry()
    worker_settings_service.read_worker_settings(tmp_path, registry, "coding")
    start = threading.Barrier(3)

    def update(stage: str) -> None:
        start.wait(timeout=5)
        worker_settings_service.update_stage_default_ownership(
            tmp_path,
            registry,
            "coding",
            stage,
            StageOwnershipMode.user,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(update, "needs_success")
        second = executor.submit(update, "needs_plan")
        start.wait(timeout=5)
        first.result(timeout=5)
        second.result(timeout=5)

    settings = worker_settings_service.read_worker_settings(tmp_path, registry, "coding")
    assert settings.stage_ownership_defaults["needs_success"] == StageOwnershipMode.user
    assert settings.stage_ownership_defaults["needs_plan"] == StageOwnershipMode.user

    root = worker_settings_service.managed_worker_settings_root(tmp_path)
    temp_files = [
        path
        for path in root.rglob("*")
        if path.name.startswith(".settings.json.tmp-")
        or path.name.startswith(".SKILL.md.tmp-")
    ]
    assert temp_files == []


def test_skill_parse_and_save_preserve_unrelated_multiline_frontmatter_segments(
    tmp_path: Path,
) -> None:
    registry = configured_worker_type_registry()
    worker_settings_service.read_worker_settings(tmp_path, registry, "coding")
    root = worker_settings_service.managed_worker_settings_root(tmp_path)
    skill_path = root / "coding" / "SKILL.md"
    unknown_before_description = (
        "# leading comment stays byte-for-byte\n"
        "unknown-map:\n"
        "  alpha: one\n"
        "  nested:\n"
        "    - keep: yes\n"
    )
    unknown_after_description = (
        "unknown-list:\n"
        "  - first\n"
        "  - second: value\n"
        "# trailing comment stays byte-for-byte\n"
        "another: value # inline comment\n"
    )
    skill_path.write_text(
        "---\n"
        f"{unknown_before_description}"
        "name: panels-worker-coding\n"
        "description: |\n"
        "  First line: with punctuation\n"
        "  Second line # not a comment\n"
        f"{unknown_after_description}"
        "---\n"
        "# Original body\n",
        encoding="utf-8",
    )

    parsed = worker_settings_service.read_worker_settings(tmp_path, registry, "coding")
    assert (
        parsed.specialist_skill.description
        == "First line: with punctuation\nSecond line # not a comment\n"
    )

    edited_description = 'Edited: "quoted"\nSecond line # still text\ncolon: still text'
    saved = worker_settings_service.save_specialist_skill(
        tmp_path,
        registry,
        "coding",
        {
            "description": edited_description,
            "markdown_body": "# Edited\n\nBody\n",
        },
    )

    written = skill_path.read_text(encoding="utf-8")
    assert saved.specialist_skill.description == edited_description
    assert unknown_before_description in written
    assert unknown_after_description in written
    assert f"description: {json.dumps(edited_description)}\n" in written
    assert "description: |\n" not in written


def test_skill_save_replaces_quoted_multiline_description_and_preserves_unrelated_bytes(
    tmp_path: Path,
) -> None:
    registry = configured_worker_type_registry()
    worker_settings_service.read_worker_settings(tmp_path, registry, "coding")
    root = worker_settings_service.managed_worker_settings_root(tmp_path)
    skill_path = root / "coding" / "SKILL.md"
    old_description = (
        'description: "First line with colon: yes\n'
        "  second line # still scalar\n"
        '  final line with \\"quote\\""\n'
    )
    unrelated_after_description = (
        "# unrelated comment stays byte-for-byte\n"
        "\n"
        "unknown-list:\n"
        "  - one\n"
        "  - two: value\n"
    )
    skill_path.write_text(
        "---\n"
        "name: panels-worker-coding\n"
        f"{old_description}"
        f"{unrelated_after_description}"
        "---\n"
        "# Original body\n",
        encoding="utf-8",
    )

    worker_settings_service.save_specialist_skill(
        tmp_path,
        registry,
        "coding",
        {
            "description": "Replacement description",
            "markdown_body": "# Replacement body\n",
        },
    )

    assert skill_path.read_text(encoding="utf-8") == (
        "---\n"
        'name: "panels-worker-coding"\n'
        'description: "Replacement description"\n'
        f"{unrelated_after_description}"
        "---\n"
        "\n"
        "# Replacement body\n"
    )


def test_provisioning_materializes_managed_specialist_skill_without_touching_sessions(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "provision.db"
    conn = connect(str(db_path))
    create_schema(conn)
    ticket = tickets_data.create_ticket(
        conn,
        title="Session stays",
        actor="human",
        now=1,
        title_max_chars=TITLE_MAX_CHARS,
        kickoff_note="go",
        worker_type="coding",
    )
    conn.execute(
        "UPDATE tickets SET employee_session_id = 'session_keep' WHERE id = ?",
        (ticket.id,),
    )
    conn.close()

    registry = configured_worker_type_registry()
    worker_settings_service.save_specialist_skill(
        db_path.parent,
        registry,
        "coding",
        {
            "description": "Materialized description",
            "markdown_body": "# Materialized\n\nManaged body\n",
        },
    )

    home = tmp_path / "hermes-home"
    provision_planner_home_skills(home, configured_database_parent=db_path.parent)

    shared = home / "skills" / "panels-worker"
    specialist = home / "skills" / "panels-worker-coding"
    assert shared.is_symlink()
    assert not specialist.is_symlink()
    skill_text = (specialist / "SKILL.md").read_text(encoding="utf-8")
    assert 'description: "Materialized description"' in skill_text
    assert "# Materialized" in skill_text

    conn = connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT employee_session_id FROM tickets WHERE id = ?", (ticket.id,)
        ).fetchone()
        assert row["employee_session_id"] == "session_keep"
    finally:
        conn.close()
