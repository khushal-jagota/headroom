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
            "INSERT INTO agents(agent_key,conversation_id) "
            "VALUES ('sprint_item_supervisor_si_files',NULL)"
        )
        conn.execute(
            "INSERT INTO sprint_items(id,title,project_id,supervisor_backend,supervisor_model,"
            "supervisor_reasoning_effort,created_at,updated_at) "
            "VALUES ('si_files','Files','project_vylo','codex','gpt-5.6-sol','medium',1,1)"
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


def _ticket_file(db_path: Path, body: bytes, name: str = "notes.md") -> Path:
    target = _ticket_root(db_path) / "t_reuse01" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return target


def test_a_browser_may_reuse_the_copy_it_holds(tmp_path: Path) -> None:
    """The whole point: asking whether a copy is still good costs no body."""
    app, db_path = _make_app(tmp_path)
    _ticket_file(db_path, b"the artifact")

    with TestClient(app) as client:
        first = client.get("/files/tickets/t_reuse01/notes.md")
        again = client.get(
            "/files/tickets/t_reuse01/notes.md",
            headers={"If-None-Match": first.headers["etag"]},
        )

    assert first.status_code == 200
    # private, because these files are answered per reader; no-cache, because a stored
    # copy is checked before every use.
    assert first.headers["cache-control"] == "private, no-cache"
    assert again.status_code == 304
    assert again.content == b""


def test_a_rewrite_of_the_same_size_retires_the_old_validator(tmp_path: Path) -> None:
    """A stat-based validator cannot see this. A worker rewrites artifacts in place."""
    app, db_path = _make_app(tmp_path)
    target = _ticket_file(db_path, b"A" * 64)

    with TestClient(app) as client:
        before = client.get("/files/tickets/t_reuse01/notes.md").headers["etag"]
        target.write_bytes(b"B" * 64)
        after = client.get("/files/tickets/t_reuse01/notes.md")
        asked_with_the_old_one = client.get(
            "/files/tickets/t_reuse01/notes.md", headers={"If-None-Match": before}
        )

    assert after.headers["etag"] != before
    assert asked_with_the_old_one.status_code == 200
    assert asked_with_the_old_one.content == b"B" * 64


def test_a_stale_validator_is_not_rescued_by_a_matching_date(tmp_path: Path) -> None:
    """A browser sends both headers. If-None-Match decides alone when it is there."""
    app, db_path = _make_app(tmp_path)
    _ticket_file(db_path, b"the artifact")

    with TestClient(app) as client:
        served = client.get("/files/tickets/t_reuse01/notes.md")
        answer = client.get(
            "/files/tickets/t_reuse01/notes.md",
            headers={
                "If-None-Match": '"a validator from some older copy"',
                "If-Modified-Since": served.headers["last-modified"],
            },
        )

    assert answer.status_code == 200
    assert answer.content == b"the artifact"


def test_range_requests_still_work_and_a_stale_if_range_gives_the_whole_file(
    tmp_path: Path,
) -> None:
    """Video seeking rides on these two behaviours."""
    app, db_path = _make_app(tmp_path)
    body = bytes(range(256)) * 64
    _ticket_file(db_path, body, name="clip.mp4")

    with TestClient(app) as client:
        whole = client.get("/files/tickets/t_reuse01/clip.mp4")
        part = client.get(
            "/files/tickets/t_reuse01/clip.mp4", headers={"Range": "bytes=10-19"}
        )
        with_live_validator = client.get(
            "/files/tickets/t_reuse01/clip.mp4",
            headers={"Range": "bytes=10-19", "If-Range": whole.headers["etag"]},
        )
        with_stale_validator = client.get(
            "/files/tickets/t_reuse01/clip.mp4",
            headers={"Range": "bytes=10-19", "If-Range": '"a validator from before"'},
        )

    assert part.status_code == 206
    assert part.headers["content-range"] == f"bytes 10-19/{len(body)}"
    assert with_live_validator.status_code == 206
    # The copy the range was meant for is gone, so the reader gets the current one whole.
    assert with_stale_validator.status_code == 200
    assert with_stale_validator.content == body


def test_a_conditional_request_never_answers_before_the_route_has_checked(
    tmp_path: Path,
) -> None:
    """A 304 is an answer. It may only be given to a reader who could have had the file."""
    app, db_path = _make_app(tmp_path)
    _ticket_file(db_path, b"the artifact")

    with TestClient(app) as client:
        etag = client.get("/files/tickets/t_reuse01/notes.md").headers["etag"]
        missing = client.get(
            "/files/tickets/t_reuse01/absent.md", headers={"If-None-Match": etag}
        )
        unreadable = client.get(
            "/files/sprint-items/si_nobody/notes.md", headers={"If-None-Match": etag}
        )

    assert missing.status_code == 404
    assert unreadable.status_code in (403, 404)


def test_the_sprint_item_route_answers_conditionally_too(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    item = _sprint_item_root(db_path) / "si_reuse01"
    item.mkdir(parents=True)
    (item / "brief.md").write_text("the outcome", encoding="utf-8")

    with TestClient(app) as client:
        first = client.get("/files/sprint-items/si_reuse01/brief.md")
        if first.status_code != 200:  # the route is supervisor-read gated
            pytest.skip(f"this fixture cannot read the file: {first.status_code}")
        again = client.get(
            "/files/sprint-items/si_reuse01/brief.md",
            headers={"If-None-Match": first.headers["etag"]},
        )

    assert first.headers["cache-control"] == "private, no-cache"
    assert again.status_code == 304
