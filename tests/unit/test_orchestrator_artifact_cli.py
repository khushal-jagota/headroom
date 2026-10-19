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
        "/api/items",
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
        ["orchestrator", *args],
        input=stdin,
        env={
            "PLAN_SERVER_URL": "http://testserver",
            "PLAN_ACTOR": "sprint_item_supervisor",
            "PLAN_SPRINT_ITEM_ID": actor_item_id,
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
        "sprint_item_id": item_id,
        "path": "notes/original.md",
        "content": content,
    }
    assert client.get(f"/api/items/{item_id}/workspace").json()["goal"] == "The goal stays here."




@pytest.mark.parametrize("operation", ["read", "write", "list"])
def test_foreign_orchestrator_artifacts_remain_refused(
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
    assert client.get(f"/files/sprint-items/{foreign}/artifacts/context.md").content == b"private"






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
