"""Managed ticket-file path and response policy."""

from __future__ import annotations

import hashlib
from pathlib import Path
from sqlite3 import Connection

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.files import api
from planner.files.contracts import ManagerFile
from planner.files.logic import reuse
from planner.files.logic.paths import resolve_manager_file


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


def _manager_root(db_path: Path) -> Path:
    return db_path.parent / "files" / "sprint-items"


def test_manager_files_use_an_isolated_root_and_route(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    target = _manager_root(db_path) / "si_files" / "notes" / "brief.md"
    target.parent.mkdir(parents=True)
    target.write_text("brief", encoding="utf-8")
    with connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO agents(agent_key,conversation_id) "
            "VALUES ('manager_si_files',NULL)"
        )
        conn.execute(
            "INSERT INTO managers(id,title,project_id,manager_backend,manager_model,"
            "manager_reasoning_effort,created_at,updated_at) "
            "VALUES ('si_files','Files','project_vylo','codex','gpt-5.6-sol','medium',1,1)"
        )
        conn.commit()

    assert resolve_manager_file(db_path, "si_files", "notes/brief.md") == (
        ManagerFile("si_files", "notes/brief.md", target.resolve(strict=True))
    )
    with TestClient(app) as client:
        response = client.get("/files/managers/si_files/notes/brief.md")
        retained = client.get("/files/sprint-items/si_files/notes/brief.md")
        assert retained.content == response.content
        assert retained.headers["etag"] == response.headers["etag"]
        missing = client.get("/files/managers/si_other/notes/brief.md")

    assert response.status_code == 200 and response.text == "brief"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert missing.status_code == 404


def test_manager_file_resolution_rejects_root_entity_and_file_symlinks(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "data" / "planning.db"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "brief.md").write_text("outside", encoding="utf-8")
    root = _manager_root(db_path)
    root.parent.mkdir(parents=True)
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        resolve_manager_file(db_path, "si_files", "brief.md")
    root.unlink()
    root.mkdir()
    (root / "si_files").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        resolve_manager_file(db_path, "si_files", "brief.md")
    (root / "si_files").unlink()
    (root / "si_files").mkdir()
    (root / "si_files" / "brief.md").symlink_to(outside / "brief.md")
    with pytest.raises(ValueError):
        resolve_manager_file(db_path, "si_files", "brief.md")




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








def test_a_conditional_request_never_answers_before_the_route_has_checked(
    tmp_path: Path,
) -> None:
    """A 304 is an answer. Only a reader who could have had the file may be given one.

    The refusal here is the authority gate, not a missing file: the Manager exists
    and so does the artifact, and the caller is another Manager's supervisor.
    """
    app, db_path = _make_app(tmp_path)
    _ticket_file(db_path, b"the artifact")
    item = _manager_root(db_path) / "si_reuse01"
    item.mkdir(parents=True)
    (item / "brief.md").write_text("the outcome", encoding="utf-8")

    with TestClient(app) as client:
        readable = client.get("/files/managers/si_reuse01/brief.md")
        assert readable.status_code == 200, readable.text
        etag = readable.headers["etag"]
        somebody_else = client.get(
            "/files/managers/si_reuse01/brief.md",
            headers={
                "If-None-Match": etag,
                "X-Plan-Actor": "manager",
                "X-Plan-Manager-ID": "si_someone_else",
            },
        )
        missing = client.get(
            "/files/tickets/t_reuse01/absent.md", headers={"If-None-Match": etag}
        )

    # Refused by the authority gate, with the copy it named still current: the check ran
    # first. The refusal must be that gate, not a file the caller simply could not find,
    # which is what makes this an ordering proof at all.
    assert somebody_else.json()["error"]["code"] == "agent_forbidden", somebody_else.text
    assert somebody_else.json()["error"]["detail"]["target_id"] == "si_reuse01"
    assert missing.status_code == 404




def test_the_tag_always_describes_the_bytes_that_were_sent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A worker rewrites an artifact mid-answer, then writes it back as it was.

    This is the case that makes a two-read design unsafe. Hash the file, send it from
    disk separately, and a rewrite in between travels under a tag describing bytes nobody
    received. Restore the artifact and the reader's stale copy is then confirmed by a
    304, permanently. Reading once removes the window rather than narrowing it: the
    mutation below lands after the read and cannot affect what was sent.
    """
    app, db_path = _make_app(tmp_path)
    target = _ticket_file(db_path, b"A" * 64)
    original = reuse.read_representation_within_bound

    def rewrite_the_artifact_mid_answer(path: Path) -> reuse.Representation | None:
        representation = original(path)
        target.write_bytes(b"B" * 64)  # same size, different bytes
        return representation

    monkeypatch.setattr(api, "read_representation_within_bound", rewrite_the_artifact_mid_answer)
    with TestClient(app) as client:
        answer = client.get("/files/tickets/t_reuse01/notes.md")

        served = f'"{hashlib.sha256(answer.content).hexdigest()}"'
        assert answer.headers["etag"] == served, "the tag must describe the bytes that travelled"
        assert answer.content == b"A" * 64

        # Without this the test passes whether or not the patch ever took: serving A and
        # tagging A is also what happens when nothing mutates, so the mutation needs a
        # witness of its own.
        assert target.read_bytes() == b"B" * 64, "the rewrite must actually have landed"

        # The artifact goes back to what it was when the reader took its copy. The reader's
        # copy really is current now, so confirming it is correct.
        monkeypatch.undo()
        target.write_bytes(b"A" * 64)
        revalidated = client.get(
            "/files/tickets/t_reuse01/notes.md",
            headers={"If-None-Match": answer.headers["etag"]},
        )
        assert revalidated.status_code == 304
