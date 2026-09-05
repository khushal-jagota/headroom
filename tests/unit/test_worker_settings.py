from __future__ import annotations

import json
import shutil
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from planner.core import change_signal
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.environments.hermes_home import provision_planner_home_skills
from planner.skill_sources import ensure_managed_panels_skills, panels_skill_root
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS
from planner.worker_settings import api as worker_settings_api
from planner.worker_settings import service as worker_settings_service
from planner.worker_types.configuration import configured_worker_type_registry


class _SignalCounter:
    """Counts the change signals raised while it is subscribed."""

    def __init__(self) -> None:
        self.count = 0

    def record(self) -> None:
        self.count += 1


@contextmanager
def _counting_change_signals() -> Iterator[_SignalCounter]:
    counter = _SignalCounter()
    unsubscribe = change_signal.subscribe(counter.record)
    try:
        yield counter
    finally:
        unsubscribe()


@pytest.fixture
def canonical_skills_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Give skill-edit tests an isolated managed home seeded from package defaults."""
    source = panels_skill_root()
    target = tmp_path / "canonical-skills"
    shutil.copytree(source, target)
    monkeypatch.setattr(worker_settings_service, "panels_skill_root", lambda: target)
    return ensure_managed_panels_skills(tmp_path, packaged_skill_root=target)


def _app(tmp_path: Path, *, raise_server_exceptions: bool = True) -> tuple[TestClient, Path]:
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


def test_workers_api_composes_registry_with_managed_settings_and_signals_the_change(
    tmp_path: Path,
) -> None:
    client, db_path = _app(tmp_path)
    with _counting_change_signals() as signals, client:
        index = client.get("/api/workers").json()
        assert {
            key: index["chief_of_staff"][key]
            for key in (
                "conversation_id",
                "agent_working",
                "needs_me",
                "latest_turn_ended_sequence",
            )
        } == {
            "conversation_id": None,
            "agent_working": False,
            "needs_me": False,
            "latest_turn_ended_sequence": 0,
        }
        assert [worker["worker_type"] for worker in index["workers"]] == list(
            configured_worker_type_registry().registered_worker_types()
        )
        detail = client.get("/api/workers/coding").json()
        assert "worker_type" not in detail
        assert detail["manifest"]["worker_type"] == "coding"
        assert detail["settings"]["specialist_skill"]["name"] == "panels-worker-coding"
        assert detail["settings"]["suggested_next_ceiling"] == "needs_success"

        updated = client.put(
            "/api/workers/coding/stages/needs_success/default-ownership",
            json={"ownership_mode": "user"},
        )
        assert updated.status_code == 200
        assert updated.json()["stage_ownership_defaults"]["needs_success"] == "user"

        terminal = client.put(
            "/api/workers/coding/stages/done/default-ownership",
            json={"ownership_mode": "worker"},
        )
        assert terminal.status_code == 400

        assert terminal.json()["error"]["message"] == "terminal stage cannot have ownership"

        missing = client.get("/api/workers/not_a_worker")
        assert missing.status_code == 404

    # Worker settings live in files, not the database, so the one accepted write says so
    # itself; the rejected terminal-stage write says nothing.
    assert signals.count == 1


def test_api_skill_patch_updates_canonical_skill_without_touching_ticket_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, canonical_skills_root: Path
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
            "UPDATE tickets SET conversation_id = 'session_keep_api' WHERE id = ?",
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
            saved_description.json()["specialist_skill"]["markdown_body"] == before["markdown_body"]
        )

        saved_body = client.patch(
            "/api/workers/coding/skill",
            json={"markdown_body": "# API materialized\n\nManaged body\n"},
        )
        assert saved_body.status_code == 200
        assert (
            saved_body.json()["specialist_skill"]["description"] == "API materialized description"
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

    skill_text = (canonical_skills_root / "panels-worker-coding" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert 'description: "API materialized description"' in skill_text
    assert "# API materialized" in skill_text

    conn = connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT conversation_id FROM tickets WHERE id = ?", (ticket.id,)
        ).fetchone()
        assert row["conversation_id"] == "session_keep_api"
    finally:
        conn.close()


def test_stage_default_signal_failure_restores_canonical_settings_and_api_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, db_path = _app(tmp_path, raise_server_exceptions=False)
    settings_parent = db_path.parent
    registry = configured_worker_type_registry()
    original = worker_settings_service.read_worker_settings(settings_parent, registry, "coding")
    root = worker_settings_service.managed_worker_settings_root(settings_parent)
    settings_path = root / "coding" / "settings.json"
    original_settings_bytes = settings_path.read_bytes()

    def fail_announcement(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced announcement failure")

    monkeypatch.setattr(
        worker_settings_api, "_announce_worker_settings_change", fail_announcement
    )
    with _counting_change_signals() as signals, client:
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
    assert signals.count == 0


def test_skill_signal_failure_restores_canonical_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, canonical_skills_root: Path
) -> None:
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
    skill_path = canonical_skills_root / "panels-worker-coding" / "SKILL.md"
    original_skill_bytes = skill_path.read_bytes()

    def fail_announcement(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("forced announcement failure")

    monkeypatch.setattr(
        worker_settings_api, "_announce_worker_settings_change", fail_announcement
    )
    with _counting_change_signals() as signals, client:
        response = client.patch(
            "/api/workers/coding/skill",
            json={"description": "New runtime description"},
        )
        assert response.status_code == 500
        detail = client.get("/api/workers/coding").json()

    assert skill_path.read_bytes() == original_skill_bytes
    assert detail["settings"]["specialist_skill"]["description"] == "Old runtime description"
    assert signals.count == 0


def test_skill_patch_preserves_concurrent_other_field_values(
    tmp_path: Path, canonical_skills_root: Path
) -> None:
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


def test_corrupt_current_settings_restore_exact_prior_good_revision(
    tmp_path: Path, canonical_skills_root: Path
) -> None:
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
    prior_good_settings = settings_path.read_text(encoding="utf-8")

    settings_path.write_text("{not-json", encoding="utf-8")

    recovered = worker_settings_service.read_worker_settings(tmp_path, registry, "coding")

    assert recovered.specialist_skill.description == "Prior good description"
    assert settings_path.read_text(encoding="utf-8") == prior_good_settings


def test_skill_parse_and_save_preserve_unrelated_multiline_frontmatter_segments(
    tmp_path: Path, canonical_skills_root: Path,
) -> None:
    registry = configured_worker_type_registry()
    worker_settings_service.read_worker_settings(tmp_path, registry, "coding")
    skill_path = canonical_skills_root / "panels-worker-coding" / "SKILL.md"
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


def test_provisioning_links_canonical_specialist_skill_without_touching_sessions(
    tmp_path: Path, canonical_skills_root: Path,
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
        "UPDATE tickets SET conversation_id = 'session_keep' WHERE id = ?",
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
    provision_planner_home_skills(
        home,
        configured_database_parent=db_path.parent,
        panels_skills_source_root=canonical_skills_root,
    )

    shared = home / "skills" / "panels-worker"
    specialist = home / "skills" / "panels-worker-coding"
    assert shared.is_symlink()
    assert specialist.is_symlink()
    assert specialist.resolve() == (canonical_skills_root / "panels-worker-coding").resolve()
    skill_text = (specialist / "SKILL.md").read_text(encoding="utf-8")
    assert 'description: "Materialized description"' in skill_text
    assert "# Materialized" in skill_text

    conn = connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT conversation_id FROM tickets WHERE id = ?", (ticket.id,)
        ).fetchone()
        assert row["conversation_id"] == "session_keep"
    finally:
        conn.close()
