from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from planner.cli.main import main as cli_main
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app


def _invoke(*args: str) -> Any:
    return CliRunner().invoke(cli_main, [*args, "--json"])


@pytest.fixture(autouse=True)
def scrub_ambient_plan_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.startswith("PLAN_"):
            monkeypatch.delenv(key)


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    db_path = tmp_path / "sprint-item-delete-cli.db"
    with connect(str(db_path)) as conn:
        create_schema(conn)
    config = load_config(
        path=None,
        env={"PLAN_TEST_MODE": "1", "PLAN_DB_PATH": str(db_path)},
    )
    app = create_app(config, build_clock(config), lambda: connect(str(db_path)))
    with TestClient(app) as test_client:
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
                test_client.request(
                    method,
                    httpx.URL(url).raw_path.decode(),
                    json=json,
                    params=params,
                    headers=headers,
                ),
            )

        monkeypatch.setattr(httpx, "request", request)
        yield test_client


def test_sprint_item_delete_cli_requires_yes_refuses_children_and_deletes(
    client: TestClient,
) -> None:
    created_item = _invoke(
        "sprint",
        "item",
        "create",
        "--title",
        "Protected item",
        "--project",
        "Vylo",
    )
    assert created_item.exit_code == 0, created_item.output
    child_item_id = json.loads(created_item.stdout)["id"]
    created_ticket = _invoke(
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Protected work",
        "--sprint-item",
        child_item_id,
    )
    assert created_ticket.exit_code == 0, created_ticket.output
    child_ticket_id = json.loads(created_ticket.stdout)["id"]

    no_confirmation = _invoke("sprint", "item", "delete", child_item_id)
    assert no_confirmation.exit_code == 1
    assert json.loads(no_confirmation.stderr)["error"]["message"] == (
        "permanent deletion requires --yes"
    )

    child_refusal = _invoke("sprint", "item", "delete", child_item_id, "--yes")
    assert child_refusal.exit_code == 1
    assert json.loads(child_refusal.stderr)["error"] == {
        "code": "validation",
        "message": "sprint item has child tickets",
        "detail": {
            "sprint_item_id": child_item_id,
            "ticket_ids": [child_ticket_id],
        },
    }
    assert client.get(f"/api/items/{child_item_id}").status_code == 200

    deletable = _invoke(
        "sprint",
        "item",
        "create",
        "--title",
        "Redundant item",
        "--project",
        "Vylo",
    )
    assert deletable.exit_code == 0, deletable.output
    deletable_item_id = json.loads(deletable.stdout)["id"]
    deleted_result = _invoke(
        "sprint",
        "item",
        "delete",
        deletable_item_id,
        "--yes",
    )
    assert deleted_result.exit_code == 0, deleted_result.output
    deleted = json.loads(deleted_result.stdout)
    assert deleted["ok"] is True
    assert deleted["sprint_item_id"] == deletable_item_id
    assert client.get(f"/api/items/{deletable_item_id}").status_code == 404
