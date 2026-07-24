"""Server static mounts do not depend on the caller's working directory."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient

from planner.core import server
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def test_employee_workspace_root_prefers_existing_coding_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    preferred_root = tmp_path / "Coding"
    preferred_root.mkdir()
    monkeypatch.setattr(server, "_REPO_ROOT", repository_root)
    monkeypatch.setattr(server, "_PREFERRED_EMPLOYEE_WORKSPACE_ROOT", preferred_root)

    assert server.resolve_employee_workspace_root() == preferred_root.resolve()


def test_employee_workspace_root_falls_back_without_creating_missing_preference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    missing_preferred_root = tmp_path / "missing" / "Coding"
    monkeypatch.setattr(server, "_REPO_ROOT", repository_root)
    monkeypatch.setattr(
        server, "_PREFERRED_EMPLOYEE_WORKSPACE_ROOT", missing_preferred_root
    )

    assert server.resolve_employee_workspace_root() == repository_root.resolve()
    assert not missing_preferred_root.exists()
    assert not missing_preferred_root.parent.exists()


def test_employee_workspace_root_falls_back_when_preference_is_not_a_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    preferred_file = tmp_path / "Coding"
    preferred_file.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(server, "_REPO_ROOT", repository_root)
    monkeypatch.setattr(server, "_PREFERRED_EMPLOYEE_WORKSPACE_ROOT", preferred_file)

    assert server.resolve_employee_workspace_root() == repository_root.resolve()


def test_static_assets_are_served_when_cwd_has_no_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "planning-test.db"
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
    clock = build_clock(config)

    def conn_factory() -> Connection:
        return connect(str(db_path))

    monkeypatch.chdir(tmp_path)
    app = create_app(config, clock, conn_factory)
    with TestClient(app) as client:
        root = client.get("/")
        css = client.get("/assets/app.css")
        favicon = client.get("/static/favicon.ico")

    assert root.status_code == 200
    assert "data-svelte-app" in root.text
    assert css.status_code == 200
    assert ".ticket-page" in css.text
    assert favicon.status_code == 200


def test_static_asset_paths_remain_anchored_to_repository_root() -> None:
    assert server._WEB_DIST == server._REPO_ROOT / "web" / "dist"
    assert server._ASSETS_DIR == server._REPO_ROOT / "assets"
    assert server._STATIC_DIR == server._REPO_ROOT / "static"
