from __future__ import annotations

import concurrent.futures
import json
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from planner.core import change_signal
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.contracts import PlannerError
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.environments.hermes_home import provision_planner_home_skills
from planner.skill_sources import ensure_managed_panels_skills, panels_skill_root
from planner.tickets import data as tickets_data
from planner.tickets.contracts import TITLE_MAX_CHARS, StageOwnershipMode
from planner.worker_settings import api as worker_settings_api
from planner.worker_settings import service as worker_settings_service
from planner.worker_settings.contracts import ManagedWorkerLaunchDefaults
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
        assert [worker["worker_type"] for worker in index["workers"]] == list(
            configured_worker_type_registry().registered_worker_types()
        )
        detail = client.get("/api/workers/coding").json()
        assert "worker_type" not in detail
        assert detail["manifest"]["worker_type"] == "coding"
        assert detail["settings"]["specialist_skill"]["name"] == "panels-worker-coding"

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


def test_skills_home_api_lists_and_edits_any_packaged_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = panels_skill_root()
    target = tmp_path / "skills"
    shutil.copytree(source, target)
    monkeypatch.setattr(worker_settings_service, "panels_skill_root", lambda: target)
    client, _ = _app(tmp_path)
    with client:
        listed = client.get("/api/skills")
        assert listed.status_code == 200
        assert any(skill["name"] == "panels" for skill in listed.json()["skills"])
        edited = client.patch("/api/skills/panels", json={"description": "edited from home"})
        assert edited.status_code == 200
        assert edited.json()["description"] == "edited from home"
        assert "edited from home" in (target / "panels" / "SKILL.md").read_text(encoding="utf-8")
        assert client.patch("/api/skills/panels", json={"name": "other"}).status_code == 400


def test_launch_defaults_are_file_backed_and_only_future_tickets_change(
    tmp_path: Path,
) -> None:
    client, db_path = _app(tmp_path)
    conn = connect(str(db_path))
    try:
        before = tickets_data.create_ticket(
            conn,
            title="Historical launch",
            actor="human",
            now=1,
            title_max_chars=TITLE_MAX_CHARS,
            worker_type="coding",
        )
        assert (
            before.employee_backend,
            before.employee_launch_model,
            before.employee_launch_reasoning_effort,
        ) == ("codex", "gpt-5.6-sol", "medium")
    finally:
        conn.close()

    with client:
        index = client.get("/api/workers").json()
        assert index["chief_of_staff"]["launch_defaults"] == {
            "employee_backend": "codex",
            "employee_launch_model": "gpt-5.6-sol",
            "employee_launch_reasoning_effort": "medium",
        }
        changed = client.put(
            "/api/workers/coding/launch-defaults",
            json={
                "employee_backend": "hermes",
                "employee_launch_model": "openai-codex:gpt-5.6-sol",
                "employee_launch_reasoning_effort": None,
            },
        )
        assert changed.status_code == 200

    conn = connect(str(db_path))
    try:
        historical = tickets_data.read_ticket(conn, before.id)
        after = tickets_data.create_ticket_from_external_work(
            conn,
            title="Future external launch",
            target_stage="needs_success",
            provided_values={},
            kickoff_note="Completed elsewhere",
            actor="chief",
            now=2,
            title_max_chars=TITLE_MAX_CHARS,
            worker_type="coding",
        )
        assert (
            historical.employee_backend,
            historical.employee_launch_model,
            historical.employee_launch_reasoning_effort,
        ) == ("codex", "gpt-5.6-sol", "medium")
        assert (
            after.employee_backend,
            after.employee_launch_model,
            after.employee_launch_reasoning_effort,
        ) == ("hermes", "openai-codex:gpt-5.6-sol", None)
    finally:
        conn.close()


def test_launch_defaults_naming_no_model_are_refused(tmp_path: Path) -> None:
    client, _db_path = _app(tmp_path, raise_server_exceptions=False)
    with client:
        for path in (
            "/api/workers/coding/launch-defaults",
            "/api/workers/chief-of-staff/launch-defaults",
        ):
            refused = client.put(
                path,
                json={
                    "employee_backend": "claude",
                    "employee_launch_model": None,
                    "employee_launch_reasoning_effort": "high",
                },
            )
            assert refused.status_code == 400


def test_stored_launch_defaults_naming_no_model_are_repaired_to_the_shipped_ones(
    tmp_path: Path,
) -> None:
    registry = configured_worker_type_registry()
    worker_settings_service.read_worker_settings(tmp_path, registry, "coding")
    root = worker_settings_service.managed_worker_settings_root(tmp_path)
    settings_path = root / "coding" / "settings.json"
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    payload["launch_defaults"] = {
        "employee_backend": "claude",
        "employee_launch_model": None,
        "employee_launch_reasoning_effort": "high",
    }
    settings_path.write_text(json.dumps(payload), encoding="utf-8")

    repaired = worker_settings_service.read_worker_settings(tmp_path, registry, "coding")

    # The whole block goes back to what the Worker type ships with, backend included: the
    # shipped model belongs to the shipped backend and means nothing to another one.
    assert repaired.launch_defaults.employee_backend == "codex"
    assert repaired.launch_defaults.employee_launch_model == "gpt-5.6-sol"
    assert repaired.launch_defaults.employee_launch_reasoning_effort == "medium"
    assert json.loads(settings_path.read_text(encoding="utf-8"))["launch_defaults"] == {
        "employee_backend": "codex",
        "employee_launch_model": "gpt-5.6-sol",
        "employee_launch_reasoning_effort": "medium",
    }


def test_stored_chief_launch_defaults_naming_no_model_are_repaired_to_the_shipped_ones(
    tmp_path: Path,
) -> None:
    worker_settings_service.read_chief_settings(tmp_path)
    root = worker_settings_service.managed_worker_settings_root(tmp_path)
    settings_path = root / "chief_of_staff" / "settings.json"
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    payload["launch_defaults"] = {
        "employee_backend": "hermes",
        "employee_launch_model": None,
        "employee_launch_reasoning_effort": None,
    }
    settings_path.write_text(json.dumps(payload), encoding="utf-8")

    repaired = worker_settings_service.read_chief_settings(tmp_path)

    assert repaired.launch_defaults == ManagedWorkerLaunchDefaults(
        employee_backend=worker_settings_service.DEFAULT_CHIEF_BACKEND,
        employee_launch_model=worker_settings_service.DEFAULT_CHIEF_MODEL,
        employee_launch_reasoning_effort=worker_settings_service.DEFAULT_CHIEF_REASONING_EFFORT,
    )
    assert json.loads(settings_path.read_text(encoding="utf-8"))["launch_defaults"] == {
        "employee_backend": worker_settings_service.DEFAULT_CHIEF_BACKEND,
        "employee_launch_model": worker_settings_service.DEFAULT_CHIEF_MODEL,
        "employee_launch_reasoning_effort": (
            worker_settings_service.DEFAULT_CHIEF_REASONING_EFFORT
        ),
    }


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


def test_skill_save_preserves_unknown_frontmatter_and_rejects_name_changes(
    tmp_path: Path, canonical_skills_root: Path,
) -> None:
    client, db_path = _app(tmp_path)
    settings_parent = db_path.parent
    registry = configured_worker_type_registry()
    worker_settings_service.read_worker_settings(settings_parent, registry, "coding")
    skill_path = canonical_skills_root / "panels-worker-coding" / "SKILL.md"
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


def test_corrupt_current_files_restore_custom_launch_defaults_on_first_read(
    tmp_path: Path,
) -> None:
    registry = configured_worker_type_registry()
    expected = worker_settings_service.update_worker_launch_defaults(
        tmp_path,
        registry,
        "coding",
        {
            "employee_backend": "claude",
            "employee_launch_model": "claude-sonnet",
            "employee_launch_reasoning_effort": "high",
        },
    ).launch_defaults
    root = worker_settings_service.managed_worker_settings_root(tmp_path)
    settings_path = root / "coding" / "settings.json"
    settings_path.write_text("{not-json", encoding="utf-8")

    recovered = worker_settings_service.read_worker_settings(tmp_path, registry, "coding")

    assert recovered.launch_defaults == expected


def test_new_worker_settings_upgrade_adds_only_runtime_defaults_ownership(
    tmp_path: Path,
) -> None:
    registry = configured_worker_type_registry()
    worker_settings_service.read_worker_settings(tmp_path, registry, "new_worker")
    root = worker_settings_service.managed_worker_settings_root(tmp_path)
    settings_path = root / "new_worker" / "settings.json"
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    payload["stage_ownership_defaults"]["needs_stages"] = "user"
    payload["launch_defaults"] = {
        "employee_backend": "claude",
        "employee_launch_model": "claude-sonnet",
        "employee_launch_reasoning_effort": "high",
    }
    del payload["stage_ownership_defaults"]["needs_runtime_defaults"]
    settings_path.write_text(json.dumps(payload), encoding="utf-8")

    upgraded = worker_settings_service.read_worker_settings(tmp_path, registry, "new_worker")

    assert upgraded.stage_ownership_defaults["needs_runtime_defaults"] == (
        StageOwnershipMode.paired
    )
    assert upgraded.stage_ownership_defaults["needs_stages"] == StageOwnershipMode.user
    assert upgraded.launch_defaults.employee_backend == "claude"
    assert upgraded.launch_defaults.employee_launch_model == "claude-sonnet"
    assert upgraded.launch_defaults.employee_launch_reasoning_effort == "high"


def test_new_worker_settings_upgrade_applies_after_last_known_good_restore(
    tmp_path: Path,
) -> None:
    registry = configured_worker_type_registry()
    worker_settings_service.read_worker_settings(tmp_path, registry, "new_worker")
    root = worker_settings_service.managed_worker_settings_root(tmp_path)
    last_good_path = root / ".last-known-good" / "new_worker" / "settings.json"
    payload = json.loads(last_good_path.read_text(encoding="utf-8"))
    payload["stage_ownership_defaults"]["needs_thinking"] = "user"
    payload["launch_defaults"] = {
        "employee_backend": "claude",
        "employee_launch_model": "claude-sonnet",
        "employee_launch_reasoning_effort": "high",
    }
    del payload["stage_ownership_defaults"]["needs_runtime_defaults"]
    last_good_path.write_text(json.dumps(payload), encoding="utf-8")
    (root / "new_worker" / "settings.json").unlink()

    recovered = worker_settings_service.read_worker_settings(tmp_path, registry, "new_worker")

    assert recovered.stage_ownership_defaults["needs_runtime_defaults"] == (
        StageOwnershipMode.paired
    )
    assert recovered.stage_ownership_defaults["needs_thinking"] == StageOwnershipMode.user
    assert recovered.launch_defaults.employee_backend == "claude"
    assert recovered.launch_defaults.employee_launch_model == "claude-sonnet"
    assert recovered.launch_defaults.employee_launch_reasoning_effort == "high"


def test_missing_non_new_worker_stage_default_still_fails_validation(tmp_path: Path) -> None:
    registry = configured_worker_type_registry()
    worker_settings_service.read_worker_settings(tmp_path, registry, "coding")
    root = worker_settings_service.managed_worker_settings_root(tmp_path)
    for settings_path in (
        root / "coding" / "settings.json",
        root / ".last-known-good" / "coding" / "settings.json",
    ):
        payload = json.loads(settings_path.read_text(encoding="utf-8"))
        del payload["stage_ownership_defaults"]["needs_plan"]
        settings_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PlannerError, match="missing stage defaults"):
        worker_settings_service.read_worker_settings(tmp_path, registry, "coding")


def test_missing_current_settings_restores_exact_edited_last_known_good_revision(
    tmp_path: Path, canonical_skills_root: Path,
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
    edited_settings = settings_path.read_text(encoding="utf-8")

    settings_path.unlink()

    recovered = worker_settings_service.read_worker_settings(tmp_path, registry, "coding")

    assert recovered.stage_ownership_defaults["needs_plan"] == StageOwnershipMode.user
    assert recovered.specialist_skill.description == "Edited last known good description"
    assert settings_path.read_text(encoding="utf-8") == edited_settings


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
        if path.name.startswith(".settings.json.tmp-") or path.name.startswith(".SKILL.md.tmp-")
    ]
    assert temp_files == []


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


def test_skill_save_replaces_quoted_multiline_description_and_preserves_unrelated_bytes(
    tmp_path: Path, canonical_skills_root: Path,
) -> None:
    registry = configured_worker_type_registry()
    worker_settings_service.read_worker_settings(tmp_path, registry, "coding")
    skill_path = canonical_skills_root / "panels-worker-coding" / "SKILL.md"
    old_description = (
        'description: "First line with colon: yes\n'
        "  second line # still scalar\n"
        '  final line with \\"quote\\""\n'
    )
    unrelated_after_description = (
        "# unrelated comment stays byte-for-byte\n\nunknown-list:\n  - one\n  - two: value\n"
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
