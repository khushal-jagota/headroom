"""Artifact CLI recovery and preservation through the existing HTTP authority."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from click.testing import CliRunner, Result
from fastapi.testclient import TestClient

from planner.cli.main import main
from planner.conversation.in_memory_conversation_system import InMemoryConversationSystem
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


@pytest.fixture
def artifact_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, CliRunner]]:
    for key in tuple(os.environ):
        if key.startswith("PLAN_"):
            monkeypatch.delenv(key)
    db_path = tmp_path / "data" / "planning.db"
    db_path.parent.mkdir()
    with closing(connect(str(db_path))) as conn:
        create_schema(conn)
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)},
    )
    app = create_app(
        config,
        build_clock(config),
        lambda: connect(str(db_path)),
        conversation_system_for_test=InMemoryConversationSystem(),
    )
    with TestClient(app) as client:

        def request(
            method: str,
            url: str,
            *,
            json: Any = None,
            params: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None,
            timeout: float | None = None,
        ) -> httpx.Response:
            del timeout
            return cast(
                httpx.Response,
                client.request(
                    method,
                    httpx.URL(url).raw_path.decode(),
                    json=json,
                    params=params,
                    headers=headers,
                ),
            )

        monkeypatch.setattr(httpx, "request", request)
        yield client, CliRunner()


def _create_item(client: TestClient, goal: str = "") -> str:
    response = client.post(
        "/api/managers",
        json={"title": "Recoverable context", "project_id": "project_other", "goal": goal},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def _invoke(
    runner: CliRunner,
    actor_item_id: str,
    *args: str,
    stdin: bytes | None = None,
) -> Result:
    return runner.invoke(
        main,
        ["manager", *args],
        input=stdin,
        env={
            "PLAN_SERVER_URL": "http://testserver",
            "PLAN_ACTOR": "manager",
            "PLAN_MANAGER_ID": actor_item_id,
        },
    )


@pytest.mark.parametrize("source", ["file", "stdin"])
@pytest.mark.parametrize(
    "content",
    ["", "no terminal newline", "line\n", "a\r\nb\r\n", "雪 · café\r\nend", "\x1b[31mred\x1b[0m"],
)
def test_own_artifact_round_trip_preserves_exact_text(
    artifact_cli: tuple[TestClient, CliRunner], tmp_path: Path, content: str, source: str
) -> None:
    client, runner = artifact_cli
    item_id = _create_item(client, "The goal stays here.")
    body_file = tmp_path / "original.md"
    body_file.write_bytes(content.encode("utf-8"))
    written = _invoke(
        runner,
        item_id,
        "artifact",
        "write",
        item_id,
        "notes/original.md",
        "--body-file",
        str(body_file) if source == "file" else "-",
        "--json",
        stdin=content.encode("utf-8") if source == "stdin" else None,
    )
    assert written.exit_code == 0, written.output
    url = json.loads(written.stdout)["url"]
    assert client.get(url).content == content.encode("utf-8")

    human = _invoke(runner, item_id, "artifact", "read", item_id, "notes/original.md")
    assert human.exit_code == 0, human.output
    assert human.stdout_bytes == content.encode("utf-8")
    structured = _invoke(
        runner, item_id, "artifact", "read", item_id, "notes/original.md", "--json"
    )
    assert structured.exit_code == 0, structured.output
    assert json.loads(structured.stdout) == {
        "manager_id": item_id,
        "path": "notes/original.md",
        "content": content,
    }
    assert client.get(f"/api/managers/{item_id}/workspace").json()["goal"] == "The goal stays here."




@pytest.mark.parametrize("operation", ["read", "write", "list"])
def test_foreign_manager_artifacts_remain_refused(
    artifact_cli: tuple[TestClient, CliRunner], tmp_path: Path, operation: str
) -> None:
    client, runner = artifact_cli
    own = _create_item(client)
    foreign = _create_item(client)
    body_file = tmp_path / "note.md"
    body_file.write_text("private", encoding="utf-8")
    written = _invoke(
        runner,
        foreign,
        "artifact",
        "write",
        foreign,
        "context.md",
        "--body-file",
        str(body_file),
        "--json",
    )
    assert written.exit_code == 0, written.output
    args = ["artifact", operation, foreign]
    if operation != "list":
        args.append("context.md")
    if operation == "write":
        args.extend(["--body-file", str(body_file)])
    result = _invoke(runner, own, *args, "--json")
    assert result.exit_code == 1, result.output
    assert result.stdout == ""
    assert json.loads(result.stderr)["error"]["code"] == "agent_forbidden"
    assert client.get(f"/files/managers/{foreign}/artifacts/context.md").content == b"private"






@pytest.mark.parametrize(
    ("artifact_path", "truncated_sibling"),
    [("notes/decision#1.md", "notes/decision"), ("notes/why?now.md", "notes/why")],
)
def test_reserved_url_characters_keep_artifact_targets_distinct(
    artifact_cli: tuple[TestClient, CliRunner],
    tmp_path: Path,
    artifact_path: str,
    truncated_sibling: str,
) -> None:
    client, runner = artifact_cli
    item_id = _create_item(client)
    source = tmp_path / "note.md"
    for path, content in ((truncated_sibling, "sibling"), (artifact_path, "requested artifact")):
        source.write_text(content, encoding="utf-8")
        written = _invoke(
            runner,
            item_id,
            "artifact",
            "write",
            item_id,
            path,
            "--body-file",
            str(source),
            "--json",
        )
        assert written.exit_code == 0, written.output
        assert json.loads(written.stdout)["path"] == f"artifacts/{path}"

    for path, content in ((truncated_sibling, "sibling"), (artifact_path, "requested artifact")):
        read = _invoke(runner, item_id, "artifact", "read", item_id, path, "--json")
        assert read.exit_code == 0, read.output
        assert json.loads(read.stdout)["path"] == path
        assert json.loads(read.stdout)["content"] == content

    deleted = _invoke(runner, item_id, "artifact", "delete", item_id, artifact_path, "--json")
    assert deleted.exit_code == 0, deleted.output
    assert json.loads(deleted.stdout)["path"] == f"artifacts/{artifact_path}"
    missing = _invoke(runner, item_id, "artifact", "read", item_id, artifact_path, "--json")
    assert missing.exit_code == 1, missing.output
    assert json.loads(missing.stderr)["error"]["code"] == "not_found"
    sibling = _invoke(runner, item_id, "artifact", "read", item_id, truncated_sibling)
    assert sibling.exit_code == 0, sibling.output
    assert sibling.stdout_bytes == b"sibling"


def _create_goal(client: TestClient) -> str:
    response = client.post(
        "/api/goals",
        json={"title": "Durable context", "project_id": "project_other", "kind": "binary",
               "primary_checklist_block_id": None}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def _goal_invoke(
    runner: CliRunner, goal_id: str, *args: str, stdin: bytes | None = None
) -> Result:
    return runner.invoke(
        main,
        ["goal", "artifact", *args],
        input=stdin,
        env={
            "PLAN_SERVER_URL": "http://testserver",
            "PLAN_ACTOR": "goal",
            "PLAN_GOAL_ID": goal_id,
        },
    )


def test_goal_artifact_commands_preserve_targets_text_and_lifecycle(
    artifact_cli: tuple[TestClient, CliRunner], tmp_path: Path
) -> None:
    client, runner = artifact_cli
    goal_id = _create_goal(client)
    path = "notes/why?now#1.md"
    content = "雪 · café\r\nno final newline"
    source = tmp_path / "note.md"
    source.write_bytes(content.encode())
    written = _goal_invoke(
        runner, goal_id, "write", goal_id, path, "--body-file", str(source), "--json"
    )
    assert written.exit_code == 0, written.output
    url = json.loads(written.stdout)["url"]
    assert url.endswith("notes/why%3Fnow%231.md")
    assert client.get(url).content == source.read_bytes()
    read = _goal_invoke(runner, goal_id, "read", goal_id, path)
    assert read.exit_code == 0, read.output
    assert read.stdout_bytes == source.read_bytes()
    updated = _goal_invoke(
        runner, goal_id, "write", goal_id, path, "--body-file", "-", "--json", stdin=b"updated\r\n"
    )
    assert updated.exit_code == 0, updated.output
    structured = _goal_invoke(runner, goal_id, "read", goal_id, path, "--json")
    assert structured.exit_code == 0, structured.output
    assert json.loads(structured.stdout) == {
        "goal_id": goal_id, "path": path, "content": "updated\r\n"
    }
    listed = _goal_invoke(runner, goal_id, "list", goal_id, "--json")
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout) == {"goal_id": goal_id, "artifacts": [path]}
    for revision, changes in ((1, {"completed": True}), (2, {"active": False})):
        changed = client.patch(
            f"/api/goals/{goal_id}", json={"expected_revision": revision, **changes}
        )
        assert changed.status_code == 200, changed.text
        assert client.get(url).content == b"updated\r\n"
    deleted = _goal_invoke(runner, goal_id, "delete", goal_id, path, "--json")
    assert deleted.exit_code == 0, deleted.output
    assert json.loads(deleted.stdout) == {
        "goal_id": goal_id, "path": "artifacts/" + path, "ok": True
    }
    assert client.get(url).status_code == 404


def test_goal_artifact_list_consumes_root_and_nested_pages_and_folded_index(
    artifact_cli: tuple[TestClient, CliRunner],
) -> None:
    from planner.files.catalog.api import _cache
    from planner.files.catalog.config import PAGE_SIZE

    client, runner = artifact_cli
    goal_id = _create_goal(client)
    root = Path(cast(Any, client.app).state.config.db_path).parent / "files" / "goals" / goal_id
    nested = root / "artifacts" / "notes"
    nested.mkdir(parents=True)
    paths = [f"notes/{index:03}.md" for index in range(PAGE_SIZE + 1)]
    for path in paths:
        (root / "artifacts" / path).write_text("note")
    # Newer unrelated root files put the artifacts folder beyond the first page.
    for index in range(PAGE_SIZE + 1):
        (root / f"{index:03}.md").write_text("outside artifacts")
    listed = _goal_invoke(runner, goal_id, "list", goal_id, "--json")
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout)["artifacts"] == paths
    (root / "artifacts" / "index.html").write_text("<p>site</p>")
    _cache.clear()
    folded = _goal_invoke(runner, goal_id, "list", goal_id, "--json")
    assert folded.exit_code == 0, folded.output
    assert json.loads(folded.stdout)["artifacts"] == ["index.html"]
    asset = _goal_invoke(runner, goal_id, "read", goal_id, paths[0])
    assert asset.exit_code == 0 and asset.stdout == "note"


def test_goal_record_commands_emit_saved_pages_in_human_and_json_modes(
    artifact_cli: tuple[TestClient, CliRunner],
) -> None:
    client, runner = artifact_cli
    for as_json in (False, True):
        options = ["--json"] if as_json else []
        document = {
            "kind": "headroom.page", "version": 1, "title": "雪 · café", "summary": "",
            "blocks": [{"type": "text", "text": "Saved goal content"}],
        }
        created = runner.invoke(
            main, ["goal", "create", *options],
            input=json.dumps({
                "title": "Inspect context", "project_id": "project_other", "page": document,
                "kind": "binary", "primary_checklist_block_id": None,
            }),
        )
        assert created.exit_code == 0, created.output
        record = json.loads(created.stdout)
        goal_id = record["id"]
        saved_page = record["page"]
        assert saved_page["title"] == document["title"]
        assert saved_page["blocks"][0]["text"] == "Saved goal content"
        shown = runner.invoke(main, ["goal", "show", goal_id, *options])
        assert shown.exit_code == 0, shown.output
        assert json.loads(shown.stdout)["page"] == saved_page
        edited = runner.invoke(
            main, ["goal", "edit", goal_id, *options],
            input=json.dumps({
                "expected_revision": record["revision"],
                "page": {**saved_page, "summary": "Updated context"},
            }),
        )
        assert edited.exit_code == 0, edited.output
        changed_page = json.loads(edited.stdout)["page"]
        assert changed_page["summary"] == "Updated context"
        assert client.get(f"/api/goals/{goal_id}").json()["page"] == changed_page
        listed = runner.invoke(main, ["goal", "list", *options])
        assert listed.exit_code == 0, listed.output
        listed_goal = next(
            row for row in json.loads(listed.stdout)["goals"] if row["id"] == goal_id
        )
        assert listed_goal["page"] == changed_page
        readings = runner.invoke(main, ["goal", "read", goal_id, *options])
        assert readings.exit_code == 0, readings.output
        assert json.loads(readings.stdout)["goal_id"] == goal_id
