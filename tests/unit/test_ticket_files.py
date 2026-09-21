"""Managed ticket-file path and response policy."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.files.contracts import SprintItemFile
from planner.files.logic.paths import resolve_sprint_item_file


def _make_app(tmp_path: Path) -> tuple[FastAPI, Path]:
    db_path = tmp_path / "data" / "planning-test.db"
    db_path.parent.mkdir(parents=True)
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

    return create_app(config, clock, conn_factory), db_path


def _ticket_root(db_path: Path) -> Path:
    return db_path.parent / "files" / "tickets"


def _sprint_item_root(db_path: Path) -> Path:
    return db_path.parent / "files" / "sprint-items"


def test_sprint_item_files_use_an_isolated_root_and_route(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    target = _sprint_item_root(db_path) / "si_files" / "notes" / "brief.md"
    target.parent.mkdir(parents=True)
    target.write_text("brief", encoding="utf-8")
    with connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO sprint_items(id,title,project_id,created_at,updated_at) "
            "VALUES ('si_files','Files','project_vylo',1,1)"
        )
        conn.commit()

    assert resolve_sprint_item_file(db_path, "si_files", "notes/brief.md") == (
        SprintItemFile("si_files", "notes/brief.md", target.resolve(strict=True))
    )
    with TestClient(app) as client:
        response = client.get("/files/sprint-items/si_files/notes/brief.md")
        missing = client.get("/files/sprint-items/si_other/notes/brief.md")

    assert response.status_code == 200 and response.text == "brief"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert missing.status_code == 404


def test_sprint_item_file_resolution_rejects_root_entity_and_file_symlinks(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data" / "planning.db"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "brief.md").write_text("outside", encoding="utf-8")
    root = _sprint_item_root(db_path)
    root.parent.mkdir(parents=True)
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        resolve_sprint_item_file(db_path, "si_files", "brief.md")
    root.unlink()
    root.mkdir()
    (root / "si_files").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        resolve_sprint_item_file(db_path, "si_files", "brief.md")
    (root / "si_files").unlink()
    (root / "si_files").mkdir()
    (root / "si_files" / "brief.md").symlink_to(outside / "brief.md")
    with pytest.raises(ValueError):
        resolve_sprint_item_file(db_path, "si_files", "brief.md")


def test_ticket_file_route_serves_inline_allowlist_with_nosniff(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    target = _ticket_root(db_path) / "t_file123" / "images" / "pic.png"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\x89PNG\r\n\x1a\n")

    with TestClient(app) as client:
        response = client.get("/files/tickets/t_file123/images/pic.png")

    assert response.status_code == 200, response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == 'inline; filename="pic.png"'
    assert response.headers["content-type"].startswith("image/png")


@pytest.mark.parametrize("path", ["%2e%2e/notes.md", "nested%2fnotes.md", "nested%5cnotes.md"])
def test_ticket_file_route_rejects_encoded_unsafe_paths(tmp_path: Path, path: str) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get(f"/files/tickets/t_file123/{path}")

    assert response.status_code == 404
