"""Managed ticket-file path and response policy."""

from __future__ import annotations

import json
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.files.contracts import SprintItemFile, TicketFile
from planner.files.logic.paths import (
    resolve_sprint_item_file,
    resolve_ticket_file,
    resolve_ticket_file_destination,
)


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


@pytest.mark.parametrize(
    "relative_path",
    ("", "../brief.md", "notes\\brief.md", "%2e%2e/brief.md", "folder"),
)
def test_sprint_item_file_resolution_rejects_unsafe_or_non_file_targets(
    tmp_path: Path, relative_path: str
) -> None:
    db_path = tmp_path / "data" / "planning.db"
    folder = _sprint_item_root(db_path) / "si_files" / "folder"
    folder.mkdir(parents=True)
    with pytest.raises(ValueError):
        resolve_sprint_item_file(db_path, "si_files", relative_path)


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


_EMPTY_CODING_FIELDS = json.dumps(
    {
        field: {"value": None, "proposal": None, "user_note": None}
        for field in (
            "kickoff",
            "success",
            "approach",
            "plan",
            "implementation",
            "closeout",
        )
    }
)


def _insert_ticket(db_path: Path, ticket_id: str) -> None:
    with connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO tickets (id, title, worker_type, employee_backend, ceiling, "
            "fields, alias, ticket_status, stage, created_at, updated_at) "
            "VALUES (?, 'Artifact ticket', 'coding', 'hermes', 'needs_success', ?, "
            "?, 'agent', 'needs_success', 1, 1)",
            (ticket_id, _EMPTY_CODING_FIELDS, f"alias-{ticket_id}"),
        )
        conn.commit()


@pytest.mark.parametrize(
    ("ticket_id", "relative_path"),
    [
        ("", "notes.md"),
        ("../ticket", "notes.md"),
        ("t_file123", ""),
        ("t_file123", "/notes.md"),
        ("t_file123", "notes\\x.md"),
        ("t_file123", "../notes.md"),
        ("t_file123", "nested/../notes.md"),
        ("t_file123", "%2e%2e/notes.md"),
        ("t_file123", "nested%2fnotes.md"),
        ("t_file123", "nested%5cnotes.md"),
    ],
)
def test_resolve_ticket_file_destination_rejects_unsafe_paths(
    tmp_path: Path, ticket_id: str, relative_path: str
) -> None:
    db_path = tmp_path / "data" / "planning.db"
    with pytest.raises(ValueError):
        resolve_ticket_file_destination(db_path, ticket_id, relative_path)


def test_resolve_ticket_file_destination_allows_a_file_that_does_not_exist_yet(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data" / "planning.db"

    destination = resolve_ticket_file_destination(db_path, "t_file123", "artifacts/plan.html")

    assert destination == _ticket_root(db_path) / "t_file123" / "artifacts" / "plan.html"
    assert not destination.exists()


def test_resolve_ticket_file_destination_rejects_symlinks_and_directories(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data" / "planning.db"
    root = _ticket_root(db_path)
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "t_escape").symlink_to(outside)
    ticket_dir = root / "t_file123"
    ticket_dir.mkdir()
    (ticket_dir / "folder").mkdir()

    with pytest.raises(ValueError):
        resolve_ticket_file_destination(db_path, "t_escape", "notes.md")
    with pytest.raises(ValueError):
        resolve_ticket_file_destination(db_path, "t_file123", "folder")


def test_ticket_file_put_creates_parents_and_get_returns_the_same_bytes(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    _insert_ticket(db_path, "t_file123")
    body = b"<h1>Plan</h1>"

    with TestClient(app) as client:
        put = client.put("/files/tickets/t_file123/artifacts/nested/plan.html", content=body)
        get = client.get("/files/tickets/t_file123/artifacts/nested/plan.html")

    assert put.status_code == 200, put.text
    assert put.json()["url"] == "/files/tickets/t_file123/artifacts/nested/plan.html"
    assert get.status_code == 200, get.text
    assert get.content == body


def test_ticket_file_put_replaces_an_existing_file(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    _insert_ticket(db_path, "t_file123")

    with TestClient(app) as client:
        client.put("/files/tickets/t_file123/notes.md", content=b"first")
        second = client.put("/files/tickets/t_file123/notes.md", content=b"second")
        get = client.get("/files/tickets/t_file123/notes.md")

    assert second.status_code == 200, second.text
    assert get.content == b"second"
    assert list((_ticket_root(db_path) / "t_file123").iterdir()) == [
        _ticket_root(db_path) / "t_file123" / "notes.md"
    ]


def test_ticket_file_put_rejects_an_unknown_ticket_and_an_empty_body(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    _insert_ticket(db_path, "t_file123")

    with TestClient(app) as client:
        unknown = client.put("/files/tickets/t_missing/notes.md", content=b"body")
        empty = client.put("/files/tickets/t_file123/notes.md", content=b"")

    assert unknown.status_code == 404, unknown.text
    assert empty.status_code == 400, empty.text
    assert not (_ticket_root(db_path) / "t_missing").exists()


@pytest.mark.parametrize("path", ["%2e%2e/notes.md", "nested%2fnotes.md", "nested%5cnotes.md"])
def test_ticket_file_put_rejects_encoded_unsafe_paths(tmp_path: Path, path: str) -> None:
    app, db_path = _make_app(tmp_path)
    _insert_ticket(db_path, "t_file123")

    with TestClient(app) as client:
        response = client.put(f"/files/tickets/t_file123/{path}", content=b"body")

    assert response.status_code == 404, response.text


def test_ticket_file_put_accepts_a_ticket_backed_worker_and_refuses_a_bare_agent(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    _insert_ticket(db_path, "t_file123")

    with TestClient(app) as client:
        worker = client.put(
            "/files/tickets/t_file123/notes.md",
            content=b"body",
            headers={"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": "t_file123"},
        )
        bare = client.put(
            "/files/tickets/t_file123/other.md",
            content=b"body",
            headers={"X-Plan-Actor": "agent"},
        )

    assert worker.status_code == 200, worker.text
    assert bare.status_code == 400, bare.text
    assert bare.json()["error"]["code"] == "agent_forbidden"
    assert not (_ticket_root(db_path) / "t_file123" / "other.md").exists()


def test_ticket_file_put_rejects_a_destination_blocked_by_a_file(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    _insert_ticket(db_path, "t_file123")

    with TestClient(app) as client:
        client.put("/files/tickets/t_file123/notes.md", content=b"first")
        blocked = client.put("/files/tickets/t_file123/notes.md/deeper.md", content=b"second")

    assert blocked.status_code == 400, blocked.text
    assert blocked.json()["error"]["code"] == "validation"
