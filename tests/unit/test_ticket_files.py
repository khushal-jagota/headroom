"""Managed ticket-file path and response policy."""

from __future__ import annotations

from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.files.contracts import TicketFile
from planner.files.logic.paths import resolve_ticket_file


def _make_app(tmp_path: Path) -> tuple[object, Path]:
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


def test_resolve_ticket_file_accepts_nested_paths_and_spaces(tmp_path: Path) -> None:
    db_path = tmp_path / "data" / "planning.db"
    target = _ticket_root(db_path) / "t_file123" / "notes" / "space name.md"
    target.parent.mkdir(parents=True)
    target.write_text("# Notes\n", encoding="utf-8")

    resolved = resolve_ticket_file(db_path, "t_file123", "notes/space name.md")

    assert resolved == TicketFile(
        ticket_id="t_file123",
        relative_path="notes/space name.md",
        absolute_path=target.resolve(strict=True),
    )


@pytest.mark.parametrize(
    ("ticket_id", "relative_path"),
    [
        ("", "notes.md"),
        ("../ticket", "notes.md"),
        ("t_file123", ""),
        ("t_file123", "/notes.md"),
        ("t_file123", "notes\\x.md"),
        ("t_file123", "nested/./notes.md"),
        ("t_file123", "../notes.md"),
        ("t_file123", "nested/../notes.md"),
        ("t_file123", "%2e%2e/notes.md"),
        ("t_file123", "nested%2fnotes.md"),
        ("t_file123", "nested%5cnotes.md"),
        ("t_file123", "%252e%252e/notes.md"),
    ],
)
def test_resolve_ticket_file_rejects_unsafe_paths(
    tmp_path: Path, ticket_id: str, relative_path: str
) -> None:
    db_path = tmp_path / "data" / "planning.db"
    with pytest.raises(ValueError):
        resolve_ticket_file(db_path, ticket_id, relative_path)


def test_resolve_ticket_file_rejects_missing_directories_and_symlink_escapes(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data" / "planning.db"
    root = _ticket_root(db_path)
    ticket_dir = root / "t_file123"
    ticket_dir.mkdir(parents=True)
    (ticket_dir / "folder").mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (ticket_dir / "escape.md").symlink_to(outside)

    for relative_path in ("missing.md", "folder", "escape.md"):
        with pytest.raises(ValueError):
            resolve_ticket_file(db_path, "t_file123", relative_path)


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


@pytest.mark.parametrize(
    ("filename", "body"),
    [
        ("notes.md", b"# Notes\n"),
        ("page.html", b"<script>window.parent.hacked = true</script>"),
        ("icon.svg", b"<svg></svg>"),
        ("archive.bin", b"unknown"),
        ("track.mid", b"midi"),
        ("clip.ts", b"video"),
    ],
)
def test_ticket_file_route_attaches_unsafe_and_unknown_types(
    tmp_path: Path, filename: str, body: bytes
) -> None:
    app, db_path = _make_app(tmp_path)
    target = _ticket_root(db_path) / "t_file123" / filename
    target.parent.mkdir(parents=True)
    target.write_bytes(body)

    with TestClient(app) as client:
        response = client.get(f"/files/tickets/t_file123/{filename}")

    assert response.status_code == 200, response.text
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == f'attachment; filename="{filename}"'


@pytest.mark.parametrize("path", ["%2e%2e/notes.md", "nested%2fnotes.md", "nested%5cnotes.md"])
def test_ticket_file_route_rejects_encoded_unsafe_paths(tmp_path: Path, path: str) -> None:
    app, _db_path = _make_app(tmp_path)

    with TestClient(app) as client:
        response = client.get(f"/files/tickets/t_file123/{path}")

    assert response.status_code == 404
