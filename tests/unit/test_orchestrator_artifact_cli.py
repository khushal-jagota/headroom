"""Artifact CLI recovery and preservation through the existing HTTP authority."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from click.testing import CliRunner, Result
from fastapi.testclient import TestClient
from tests.support.in_memory_conversation_system import InMemoryConversationSystem

from planner.cli.main import main
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect
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
    ["", "雪 · café\r\nend", "\x1b[31mred\x1b[0m"],
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


def _create_goal(client: TestClient, holder_id: str) -> str:
    response = client.post(
        "/api/goals",
        json={"title": "Durable context", "project_id": "project_other", "kind": "binary",
               "primary_checklist_block_id": None, "holder_kind": "manager", "holder_id": holder_id}
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def _goal_invoke(
    runner: CliRunner, holder_id: str, *args: str, stdin: bytes | None = None
) -> Result:
    return runner.invoke(
        main,
        ["goal", "artifact", *args],
        input=stdin,
        env={
            "PLAN_SERVER_URL": "http://testserver",
            "PLAN_ACTOR": "manager",
            "PLAN_MANAGER_ID": holder_id,
        },
    )


def test_goal_artifact_commands_preserve_targets_text_and_lifecycle(
    artifact_cli: tuple[TestClient, CliRunner], tmp_path: Path
) -> None:
    client, runner = artifact_cli
    holder_id = _create_item(client)
    goal_id = _create_goal(client, holder_id)
    path = "notes/why?now#1.md"
    content = "雪 · café\r\nno final newline"
    source = tmp_path / "note.md"
    source.write_bytes(content.encode())
    written = _goal_invoke(
        runner, holder_id, "write", goal_id, path, "--body-file", str(source), "--json"
    )
    assert written.exit_code == 0, written.output
    url = json.loads(written.stdout)["url"]
    assert url.endswith("notes/why%3Fnow%231.md")
    assert client.get(url).content == source.read_bytes()
    read = _goal_invoke(runner, holder_id, "read", goal_id, path)
    assert read.exit_code == 0, read.output
    assert read.stdout_bytes == source.read_bytes()
    updated = _goal_invoke(
        runner, holder_id, "write", goal_id, path, "--body-file", "-", "--json",
        stdin=b"updated\r\n"
    )
    assert updated.exit_code == 0, updated.output
    structured = _goal_invoke(runner, holder_id, "read", goal_id, path, "--json")
    assert structured.exit_code == 0, structured.output
    assert json.loads(structured.stdout) == {
        "goal_id": goal_id, "path": path, "content": "updated\r\n"
    }
    listed = _goal_invoke(runner, holder_id, "list", goal_id, "--json")
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout) == {"goal_id": goal_id, "artifacts": [path]}
    for changes in ({"completed": True}, {"active": False}):
        changed = client.patch(
            f"/api/goals/{goal_id}", json=changes
        )
        assert changed.status_code == 200, changed.text
        assert client.get(url).content == b"updated\r\n"
    deleted = _goal_invoke(runner, holder_id, "delete", goal_id, path, "--json")
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
    holder_id = _create_item(client)
    goal_id = _create_goal(client, holder_id)
    root = Path(cast(Any, client.app).state.config.db_path).parent / "files" / "goals" / goal_id
    nested = root / "artifacts" / "notes"
    nested.mkdir(parents=True)
    paths = [f"notes/{index:03}.md" for index in range(PAGE_SIZE + 1)]
    for path in paths:
        (root / "artifacts" / path).write_text("note")
    # Newer unrelated root files put the artifacts folder beyond the first page.
    for index in range(PAGE_SIZE + 1):
        (root / f"{index:03}.md").write_text("outside artifacts")
    listed = _goal_invoke(runner, holder_id, "list", goal_id, "--json")
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.stdout)["artifacts"] == paths
    (root / "artifacts" / "index.html").write_text("<p>site</p>")
    _cache.clear()
    folded = _goal_invoke(runner, holder_id, "list", goal_id, "--json")
    assert folded.exit_code == 0, folded.output
    assert json.loads(folded.stdout)["artifacts"] == ["index.html"]
    asset = _goal_invoke(runner, holder_id, "read", goal_id, paths[0])
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


def _page_invoke(
    runner: CliRunner, actor: str, manager_id: str | None, *args: str, stdin: str | None = None
) -> Result:
    return runner.invoke(
        main, list(args), input=stdin,
        env={
            "PLAN_SERVER_URL": "http://testserver", "PLAN_ACTOR": actor,
            "PLAN_MANAGER_ID": manager_id or "",
        },
    )


def test_page_cli_targets_preserve_authority_and_documents(
    artifact_cli: tuple[TestClient, CliRunner],
) -> None:
    """New selectors must address each holder without changing the caller identity."""
    client, runner = artifact_cli
    manager_id = _create_item(client, "Keep this purpose.")
    schema = _page_invoke(runner, "manager", manager_id, "page", "schema", "--json")
    document = json.loads(schema.stdout)["examples"]["checklist_and_workers"]["page"]
    for actor, selector in (("manager", ["--manager", manager_id]), ("chief", ["--chief"])):
        edited = _page_invoke(
            runner, actor, manager_id, "page", "edit", *selector, "--json",
            stdin=json.dumps({"page": document}),
        )
        assert edited.exit_code == 0, edited.output
        accepted = json.loads(edited.stdout)
        assert accepted["page"] == document and accepted["page_revision"] == 1
        shown = _page_invoke(runner, actor, manager_id, "page", "show", *selector)
        assert shown.exit_code == 0, shown.output
        assert json.loads(shown.stdout) == accepted
    denied = _page_invoke(
        runner, "manager", manager_id, "page", "edit", "--chief", "--json",
        stdin=json.dumps({"page": document}),
    )
    assert denied.exit_code == 1
    assert json.loads(denied.stderr)["error"]["code"] == "agent_forbidden"
    manifest = _invoke(runner, manager_id, "show", manager_id, "--json")
    assert manifest.exit_code == 0, manifest.output
    assert json.loads(manifest.stdout)["manifest"]["page"]["character_count"] == len(
        json.dumps(document, ensure_ascii=False)
    )
    expanded = _invoke(runner, manager_id, "show", manager_id, "page", "--json")
    assert expanded.exit_code == 0, expanded.output
    assert json.loads(expanded.stdout)["parts"]["page"]["value"] == document
    foreign_id = _create_item(client)
    foreign = _page_invoke(
        runner, "manager", manager_id, "page", "edit", "--manager", foreign_id, "--json",
        stdin=json.dumps({"page": document}),
    )
    assert foreign.exit_code == 1
    assert json.loads(foreign.stderr)["error"]["code"] == "agent_forbidden"
    assert client.get(f"/api/managers/{foreign_id}/page").json()["page"] is None
    invalid = _page_invoke(
        runner, "manager", manager_id, "page", "edit", "--manager", manager_id, stdin="{}"
    )
    assert invalid.exit_code == 1 and "panels page schema" in invalid.stderr
    for invalid_input in ("{", "[]", '{"page":{"kind":"wrong"}}'):
        malformed = _page_invoke(
            runner, "manager", manager_id, "page", "edit", "--manager", manager_id,
            stdin=invalid_input,
        )
        assert malformed.exit_code == 1 and "panels page schema" in malformed.stderr
    unchanged = client.get(f"/api/managers/{manager_id}/page").json()
    assert unchanged["page_revision"] == 1 and unchanged["page"] == document
    cleared = _page_invoke(
        runner, "manager", manager_id, "page", "edit", "--manager", manager_id, "--json",
        stdin='{"page":null}',
    )
    assert cleared.exit_code == 0, cleared.output
    assert json.loads(cleared.stdout) == {"page": None, "page_revision": 2}
    workspace = client.get(f"/api/managers/{manager_id}/workspace").json()
    assert workspace["goal"] == "Keep this purpose."


def test_workspace_commands_follow_all_held_goal_cardinalities(
    artifact_cli: tuple[TestClient, CliRunner],
) -> None:
    """An inactive held goal still supplies the sole-goal view used by the browser."""
    client, runner = artifact_cli
    manager_id = _create_item(client)
    empty = _invoke(runner, manager_id, "workspace", manager_id)
    assert empty.exit_code == 0, empty.output
    assert f"Edit: panels page edit --manager {manager_id}" in empty.stdout
    created = client.post("/api/goals", json={
        "title": "Held outcome", "project_id": "project_other", "kind": "binary",
        "holder_kind": "manager", "holder_id": manager_id, "active": False,
        "primary_checklist_block_id": None,
    })
    assert created.status_code == 200, created.text
    goal_id = created.json()["id"]
    sole = _invoke(runner, manager_id, "workspace", manager_id)
    assert sole.exit_code == 0, sole.output
    assert f"Edit: panels goal edit {goal_id}" in sole.stdout
    assert "own document is hidden" in sole.stdout
    second = client.post("/api/goals", json={
        "title": "Second outcome", "project_id": "project_other", "kind": "binary",
        "holder_kind": "manager", "holder_id": manager_id,
        "primary_checklist_block_id": None,
    })
    assert second.status_code == 200, second.text
    multiple = _invoke(runner, manager_id, "workspace", manager_id)
    assert multiple.exit_code == 0, multiple.output
    assert f"Edit: panels page edit --manager {manager_id}" in multiple.stdout
    assert "Held outcome" in multiple.stdout and "Second outcome" in multiple.stdout
    manifest = _invoke(runner, manager_id, "show", manager_id, "--json")
    assert manifest.exit_code == 0, manifest.output
    assert json.loads(manifest.stdout)["manifest"]["page"]["character_count"] == 0
