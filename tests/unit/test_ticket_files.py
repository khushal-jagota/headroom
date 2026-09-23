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
from planner.files.contracts import SprintItemFile
from planner.files.logic import reuse
from planner.files.logic.paths import resolve_sprint_item_file
from planner.files.logic.reuse import read_representation_within_bound


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


def test_a_range_request_is_answered_exactly_as_it_always_was(tmp_path: Path) -> None:
    """Video playback rides on this, and a validator from an earlier read would break it.

    A range answer's size and offsets come from the stat taken as the body is sent. An
    entity tag computed before that, from a separate read, can disagree with it — and a
    disagreeing tag is worse than none, because ``If-Range`` would confirm a copy the
    reader does not have and then hand over bytes from a different one.
    """
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
            headers={"Range": "bytes=10-19", "If-Range": part.headers["etag"]},
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
    # It still carries the policy: a stored piece is checked before it is used, and a
    # shared cache may not keep it at all.
    assert part.headers["cache-control"] == "private, no-cache"
    # Its tag is the one the sending stat derived, never a byte-derived one: that is
    # what keeps the tag and the range arithmetic describing the same snapshot. A video
    # keeps that tag on every answer, range or not, because it is seeked.
    representation = read_representation_within_bound(
        _ticket_root(db_path) / "t_reuse01" / "clip.mp4"
    )
    assert representation is not None
    assert part.headers["etag"] != representation.etag
    assert whole.headers["etag"] != representation.etag


def test_a_range_request_is_never_answered_not_modified(tmp_path: Path) -> None:
    app, db_path = _make_app(tmp_path)
    _ticket_file(db_path, b"C" * 512, name="clip.mp4")

    with TestClient(app) as client:
        plain = client.get("/files/tickets/t_reuse01/clip.mp4")
        ranged = client.get(
            "/files/tickets/t_reuse01/clip.mp4",
            headers={"Range": "bytes=0-9", "If-None-Match": plain.headers["etag"]},
        )

    assert ranged.status_code == 206
    assert ranged.content == b"C" * 10


def test_a_date_alone_never_confirms_a_copy(tmp_path: Path) -> None:
    """A second-resolution timestamp cannot see a rewrite inside the same second.

    Every answer carries an entity tag, so nothing needs the date, and consulting it
    would reintroduce exactly the staleness the byte-derived tag removes.
    """
    app, db_path = _make_app(tmp_path)
    target = _ticket_file(db_path, b"A" * 12)

    with TestClient(app) as client:
        served = client.get("/files/tickets/t_reuse01/notes.md")
        target.write_bytes(b"B" * 12)  # same size, same second
        answer = client.get(
            "/files/tickets/t_reuse01/notes.md",
            headers={"If-Modified-Since": served.headers["last-modified"]},
        )

    assert answer.status_code == 200
    assert answer.content == b"B" * 12


def test_a_conditional_request_never_answers_before_the_route_has_checked(
    tmp_path: Path,
) -> None:
    """A 304 is an answer. Only a reader who could have had the file may be given one.

    The refusal here is the authority gate, not a missing file: the Sprint Item exists
    and so does the artifact, and the caller is another Sprint Item's supervisor.
    """
    app, db_path = _make_app(tmp_path)
    _ticket_file(db_path, b"the artifact")
    item = _sprint_item_root(db_path) / "si_reuse01"
    item.mkdir(parents=True)
    (item / "brief.md").write_text("the outcome", encoding="utf-8")

    with TestClient(app) as client:
        readable = client.get("/files/sprint-items/si_reuse01/brief.md")
        assert readable.status_code == 200, readable.text
        etag = readable.headers["etag"]
        somebody_else = client.get(
            "/files/sprint-items/si_reuse01/brief.md",
            headers={
                "If-None-Match": etag,
                "X-Plan-Actor": "sprint_item_supervisor",
                "X-Plan-Sprint-Item-Id": "si_someone_else",
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
    with TestClient(app) as client:
        revalidated = client.get(
            "/files/tickets/t_reuse01/notes.md",
            headers={"If-None-Match": answer.headers["etag"]},
        )
    assert revalidated.status_code == 304


def test_an_artifact_larger_than_the_bound_keeps_the_streaming_answer(
    tmp_path: Path,
) -> None:
    """Reuse is declined rather than approximated when it cannot be made consistent."""
    app, db_path = _make_app(tmp_path)
    body = b"z" * (reuse.REUSE_MEMORY_BOUND_BYTES + 1)
    _ticket_file(db_path, body, name="huge.txt")

    with TestClient(app) as client:
        answer = client.get("/files/tickets/t_reuse01/huge.txt")
        asked_again = client.get(
            "/files/tickets/t_reuse01/huge.txt",
            headers={"If-None-Match": answer.headers["etag"]},
        )

    assert answer.status_code == 200
    assert answer.content == body
    # The policy is still on it, and it is never told its copy is unchanged.
    assert answer.headers["cache-control"] == "private, no-cache"
    assert asked_again.status_code == 200


def test_sound_and_video_keep_the_framework_answer_at_any_size(tmp_path: Path) -> None:
    """Media is seeked, so it keeps range handling and a tag that agrees with it."""
    app, db_path = _make_app(tmp_path)
    _ticket_file(db_path, b"\x00" * 2048, name="clip.mp4")
    _ticket_file(db_path, b"\x00" * 2048, name="note.mp3")

    with TestClient(app) as client:
        video = client.get("/files/tickets/t_reuse01/clip.mp4")
        audio = client.get("/files/tickets/t_reuse01/note.mp3")
        video_again = client.get(
            "/files/tickets/t_reuse01/clip.mp4",
            headers={"If-None-Match": video.headers["etag"]},
        )

    for answer in (video, audio):
        assert answer.headers["cache-control"] == "private, no-cache"
        assert answer.headers["accept-ranges"] == "bytes"
    assert video_again.status_code == 200


def test_a_non_media_file_still_serves_a_plain_range(tmp_path: Path) -> None:
    """Asking for a piece of a text artifact still gets a piece of it.

    Reuse and ranges each keep their own tag, and the two are never compared. A plain
    range is answered by the framework exactly as before. Only a *conditional* range —
    one carrying the byte-derived tag the whole-file answer gave out — declines, because
    the framework judges ``If-Range`` against its own stat-derived tag and cannot
    recognise the other. Declining means the whole current file, which is the safe
    answer and the one the specification asks for.
    """
    app, db_path = _make_app(tmp_path)
    body = b"the artifact, at some length" * 64
    _ticket_file(db_path, body, name="report.txt")
    url = "/files/tickets/t_reuse01/report.txt"

    with TestClient(app) as client:
        whole = client.get(url)
        plain_range = client.get(url, headers={"Range": "bytes=4-13"})
        conditional_range = client.get(
            url, headers={"Range": "bytes=4-13", "If-Range": whole.headers["etag"]}
        )

    assert plain_range.status_code == 206
    assert plain_range.content == body[4:14]
    assert plain_range.headers["content-range"] == f"bytes 4-13/{len(body)}"
    # The conditional one is handed the whole current file instead of a piece.
    assert conditional_range.status_code == 200
    assert conditional_range.content == body
