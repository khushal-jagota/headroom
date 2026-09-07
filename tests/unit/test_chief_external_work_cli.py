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
from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationSystem,
)
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app

ServerHandle = object


@pytest.fixture(autouse=True)
def scrub_ambient_plan_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.startswith("PLAN_"):
            monkeypatch.delenv(key)


def _run(_server: object, *args: str, actor: str | None = "chief") -> Any:
    env = {"PLAN_SERVER_URL": "http://testserver"}
    if actor is not None:
        env["PLAN_ACTOR"] = actor
    return CliRunner().invoke(cli_main, list(args), env=env)


@pytest.fixture
def server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    db_path = tmp_path / "chief-cli.db"
    with connect(str(db_path)) as conn:
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
            path = httpx.URL(url).raw_path.decode()
            return cast(
                httpx.Response,
                client.request(method, path, json=json, params=params, headers=headers),
            )

        monkeypatch.setattr(httpx, "request", request)
        yield object()


def _file(tmp_path: Path, name: str, text: str) -> str:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_real_server_chief_external_work_terse_output_and_actor_rejection(
    server: ServerHandle, tmp_path: Path
) -> None:
    note = _file(tmp_path, "note.md", "Complete report and reconciliation reason")
    success = _file(tmp_path, "success.md", "Success")

    created = _run(
        server,
        "chief",
        "create-ticket-from-external-work",
        "--title",
        "Terse import",
        "--worker-type",
        "coding",
        "--stage",
        "needs_success",
        "--kickoff-note-file",
        note,
    )
    assert created.exit_code == 0, created.output
    ticket_id = created.stdout.split()[0]
    assert ticket_id.startswith("t_")
    assert "external work created" in created.stdout
    assert "needs_success" in created.stdout

    ordinary = _run(
        server,
        "ticket",
        "create",
        "--title",
        "To reconcile",
        "--worker-type",
        "coding",
        "--json",
        actor=None,
    )
    assert ordinary.exit_code == 0, ordinary.output
    ordinary_id = json.loads(ordinary.stdout)["id"]
    kickoff = _run(
        server,
        "ticket",
        "approve",
        ordinary_id,
        "--ceiling",
        "none",
        "--at-cap",
        "propose",
        "--json",
        actor=None,
    )
    assert kickoff.exit_code == 0, kickoff.output
    reconciled = _run(
        server,
        "chief",
        "reconcile-ticket-from-external-work",
        ordinary_id,
        "--stage",
        "needs_approach",
        "--kickoff-note-file",
        note,
        "--success-file",
        success,
    )
    assert reconciled.exit_code == 0, reconciled.output
    assert ordinary_id in reconciled.stdout
    assert "external work reconciled" in reconciled.stdout
    assert "needs_approach" in reconciled.stdout

    rejected = _run(
        server,
        "chief",
        "create-ticket-from-external-work",
        "--title",
        "Rejected import",
        "--worker-type",
        "coding",
        "--stage",
        "needs_success",
        "--kickoff-note-file",
        note,
        "--json",
        actor="worker",
    )
    assert rejected.exit_code == 1
    assert json.loads(rejected.stderr)["error"]["code"] == "agent_forbidden"
