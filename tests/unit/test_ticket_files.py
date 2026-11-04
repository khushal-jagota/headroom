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
from planner.core.contracts import OWNER_PRINCIPAL
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.files import api
from planner.files.contracts import ManagerFile
from planner.files.logic import reuse
from planner.files.logic.paths import resolve_goal_file, resolve_manager_file
from planner.tickets.data import create_ticket


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
    html = target.with_name("index.html")
    html.write_text("<p>HTML</p>", encoding="utf-8")
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
        for prefix in ("managers", "sprint-items"):
            confined = client.get(
                f"/files/{prefix}/si_files/notes/index.html",
                headers={"X-Panels-Artifact-Root": "/files/managers/si_files/notes/index.html"},
            )
            assert confined.status_code == 200 and confined.text == "<p>HTML</p>"

    assert response.status_code == 200 and response.text == "brief"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert missing.status_code == 404


@pytest.mark.parametrize("kind", ["manager", "goal"])
def test_managed_file_resolution_rejects_root_entity_and_file_symlinks(
    tmp_path: Path, kind: str,
) -> None:
    db_path = tmp_path / "data" / "planning.db"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "brief.md").write_text("outside", encoding="utf-8")
    root = db_path.parent / "files" / "goals" if kind == "goal" else _manager_root(db_path)
    resolve = resolve_goal_file if kind == "goal" else resolve_manager_file
    root.parent.mkdir(parents=True)
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        resolve(db_path, "si_files", "brief.md")
    root.unlink()
    root.mkdir()
    (root / "si_files").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        resolve(db_path, "si_files", "brief.md")
    (root / "si_files").unlink()
    (root / "si_files").mkdir()
    (root / "si_files" / "brief.md").symlink_to(outside / "brief.md")
    with pytest.raises(ValueError):
        resolve(db_path, "si_files", "brief.md")




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


def test_html_navigation_confines_resolved_paths_without_changing_delivery(tmp_path: Path) -> None:
    """A sibling reached through a symlink must not enter the preview sandbox."""
    app, db_path = _make_app(tmp_path)
    index = _ticket_file(db_path, b"index", "artifact/index.html")
    child = _ticket_file(db_path, b"child", "artifact/child.html")
    outside = _ticket_file(db_path, b"private sibling", "outside.html")
    (index.parent / "escape.html").symlink_to(outside)
    (index.parent / "inside.html").symlink_to(child)
    (index.parent / "nested").symlink_to(index.parent, target_is_directory=True)
    root_href = "/files/tickets/t_reuse01/artifact/index.html"
    headers = {"X-Panels-Artifact-Root": root_href}
    with TestClient(app) as client:
        ordinary = client.get(root_href)
        confined = client.get(root_href, headers=headers)
        valid = client.get("/files/tickets/t_reuse01/artifact/inside.html", headers=headers)
        nested = client.get("/files/tickets/t_reuse01/artifact/nested/child.html", headers=headers)
        missing = client.get("/files/tickets/t_reuse01/artifact/missing.html", headers=headers)
        escaped = client.get("/files/tickets/t_reuse01/artifact/escape.html", headers=headers)
        sibling = client.get("/files/tickets/t_reuse01/outside.html", headers=headers)
    assert all(answer.status_code == 200 for answer in (ordinary, confined, valid, nested))
    assert ordinary.content == confined.content == b"index"
    assert valid.content == nested.content == b"child"
    for name in ("content-disposition", "x-content-type-options", "cache-control", "etag"):
        assert ordinary.headers[name] == confined.headers[name]
    assert missing.status_code == escaped.status_code == sibling.status_code == 404
    assert missing.json() == escaped.json() == sibling.json()
    assert str(tmp_path) not in escaped.text


def test_html_navigation_resolves_symlinked_artifact_root_before_comparison(tmp_path: Path) -> None:
    """The lexical artifact alias is permitted only inside its resolved directory."""
    app, db_path = _make_app(tmp_path)
    index = _ticket_file(db_path, b"index", "actual/index.html")
    _ticket_file(db_path, b"child", "actual/child.html")
    outside = _ticket_file(db_path, b"outside", "sibling/outside.html")
    owner_root = index.parent.parent
    (owner_root / "alias").symlink_to(index.parent, target_is_directory=True)
    (index.parent / "escape.html").symlink_to(outside)
    # A root document symlink cannot silently redefine the artifact directory.
    (index.parent / "bad-root.html").symlink_to(outside)
    headers = {"X-Panels-Artifact-Root": "/files/tickets/t_reuse01/alias/index.html"}
    with TestClient(app) as client:
        valid = client.get("/files/tickets/t_reuse01/alias/child.html", headers=headers)
        escaped = client.get("/files/tickets/t_reuse01/alias/escape.html", headers=headers)
        bad_root = client.get("/files/tickets/t_reuse01/actual/child.html", headers={
            "X-Panels-Artifact-Root": "/files/tickets/t_reuse01/actual/bad-root.html",
        })
    assert valid.status_code == 200 and valid.content == b"child"
    assert escaped.status_code == bad_root.status_code == 404


def test_html_navigation_rejects_untrusted_root_descriptors(tmp_path: Path) -> None:
    """Headers are untrusted too, including encoded traversal and URL authorities."""
    app, db_path = _make_app(tmp_path)
    _ticket_file(db_path, b"index", "artifact/index.html")
    target = "/files/tickets/t_reuse01/artifact/index.html"
    unsafe_roots = [
        "https://outside.test" + target, "//outside.test" + target,
        target + "?query=yes", target + "#fragment", target.replace("/artifact/", "/artifact/../"),
        target.replace("/artifact/", "/artifact/%2e%2e/"),
        target.replace("/artifact/", "/artifact%2f"), target.replace("/artifact/", "/artifact%5c"),
        target.replace("/artifact/", "/artifact/%252e%252e/"),
        target.replace("/artifact/", "/artifact\\"),
        target.replace("t_reuse01", "t_other"),
        target.replace("tickets", "managers"),
    ]
    with TestClient(app) as client:
        absent = client.get("/files/tickets/t_reuse01/artifact/absent.html")
        for root in unsafe_roots:
            response = client.get(target, headers={"X-Panels-Artifact-Root": root})
            assert response.status_code == 404, root
            assert response.json() == absent.json(), root


def _create_goal(client: TestClient) -> str:
    result = client.post(
        "/api/goals", json={"title": "Files", "project_id": "project_other", "kind": "binary",
               "primary_checklist_block_id": None}
    )
    assert result.status_code == 200, result.text
    return str(result.json()["id"])


def test_goal_file_and_catalog_authority_precedes_access_and_conditional_responses(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    with TestClient(app) as client:
        goal_id = _create_goal(client)
        foreign_id = _create_goal(client)
        url = f"/files/goals/{goal_id}/artifacts/brief.md"
        catalog = f"/api/artifact-catalog/goal/{goal_id}"
        own = {"X-Plan-Actor": "goal", "X-Plan-Goal-ID": goal_id}
        allowed = [{}, {"X-Plan-Actor": "chief"}, own]
        for headers in allowed:
            assert client.put(url, json={"content": "private"}, headers=headers).status_code == 200
            read = client.get(url, headers=headers)
            assert read.status_code == 200 and read.content == b"private"
            assert client.get(catalog, headers=headers).status_code == 200
            unchanged = client.get(url, headers={**headers, "If-None-Match": read.headers["etag"]})
            assert unchanged.status_code == 304
            assert unchanged.headers["cache-control"] == "private, no-cache"
        manager = client.post(
            "/api/managers", json={"title": "Another owner", "project_id": "project_other"}
        )
        assert manager.status_code == 200, manager.text
        with connect(str(db_path)) as conn:
            ticket = create_ticket(
                conn, title="Another worker", worker_type="coding", principal=OWNER_PRINCIPAL,
                now=1, title_max_chars=200,
            )
        refused = [
            {"X-Plan-Actor": "goal", "X-Plan-Goal-ID": foreign_id},
            {"X-Plan-Actor": "manager", "X-Plan-Manager-ID": manager.json()["id"]},
            {"X-Plan-Actor": "worker", "X-Plan-Ticket-ID": ticket.id},
        ]
        for headers in refused:
            conditional = client.get(
                url, headers={**headers, "If-None-Match": read.headers["etag"]}
            )
            assert conditional.status_code == 400
            assert conditional.json()["error"]["code"] == "agent_forbidden"
            assert conditional.json()["error"]["detail"]["target_id"] == goal_id
            assert client.get(catalog, headers=headers).status_code == 400
            assert client.put(url, json={"content": "stolen"}, headers=headers).status_code == 400
            assert client.delete(url, headers=headers).status_code == 400
            assert client.get(url).content == b"private"
        # Existing bytes under a missing goal never grant access, even to the owner.
        orphan = db_path.parent / "files" / "goals" / "goal_missing" / "artifacts" / "brief.md"
        orphan.parent.mkdir(parents=True)
        orphan.write_text("orphan")
        missing = "/files/goals/goal_missing/artifacts/brief.md"
        assert client.get(missing).status_code == 404
        assert client.get("/api/artifact-catalog/goal/goal_missing").status_code == 404
        assert client.put(missing, json={"content": "changed"}).status_code == 404
        assert client.delete(missing).status_code == 404
        assert orphan.read_text() == "orphan"
        for headers in allowed:
            assert client.delete(url, headers=headers).status_code == 200
            assert client.put(url, json={"content": "private"}, headers=headers).status_code == 200


def test_goal_files_reject_traversal_and_symlink_escapes_and_confine_html_navigation(
    tmp_path: Path,
) -> None:
    app, db_path = _make_app(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.html").write_text("secret")
    with TestClient(app) as client:
        goal_id = _create_goal(client)
        root = db_path.parent / "files" / "goals" / goal_id
        site = root / "artifacts" / "site"
        site.mkdir(parents=True)
        (site / "index.html").write_text("<p>root</p>")
        (site / "next.html").write_text("<p>next</p>")
        (site / "escape.html").symlink_to(outside / "secret.html")
        (root / "outside.html").write_text("outside site")
        (root / "escape").symlink_to(outside, target_is_directory=True)
        prefix = f"/files/goals/{goal_id}/"
        for path in ("%2e%2e/secret.html", "nested%2fsecret.html", "nested%5csecret.html"):
            assert client.get(prefix + path).status_code == 404
            assert client.put(prefix + path, json={"content": "changed"}).status_code == 404
            assert client.delete(prefix + path).status_code == 404
        for path in ("escape/secret.html", "artifacts/site/escape.html"):
            assert client.get(prefix + path).status_code == 404
        escaped_write = client.put(prefix + "escape/new/secret.html", json={"content": "changed"})
        assert escaped_write.status_code == 400
        assert not (outside / "new").exists()
        assert client.delete(prefix + "escape/secret.html").status_code == 400
        assert (outside / "secret.html").read_text() == "secret"
        headers = {"X-Panels-Artifact-Root": prefix + "artifacts/site/index.html"}
        ordinary = client.get(prefix + "artifacts/site/next.html")
        confined = client.get(prefix + "artifacts/site/next.html", headers=headers)
        assert confined.status_code == 200 and confined.content == ordinary.content
        for key in ("etag", "cache-control", "content-disposition", "x-content-type-options"):
            assert confined.headers[key] == ordinary.headers[key]
        for path in ("outside.html", "artifacts/site/escape.html"):
            assert client.get(prefix + path, headers=headers).status_code == 404
        other = _create_goal(client)
        other_url = f"/files/goals/{other}/artifacts/site/next.html"
        assert client.put(other_url, json={"content": "another goal"}).status_code == 200
        assert client.get(other_url, headers=headers).status_code == 404
