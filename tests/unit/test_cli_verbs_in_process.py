"""CLI verb coverage against an in-process Panels application."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path
from typing import Any, cast

import httpx
import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient
from tests.support.probe import seed_probe_worker_type

from planner.cli.main import main as cli_main
from planner.conversation.contracts import ConversationBackendKey
from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationSystem,
)
from planner.conversation.snapshot import BackendModel, BackendSnapshot
from planner.core.clock import build_clock
from planner.core.config import load_config
from planner.core.db import connect, create_schema
from planner.core.server import create_app
from planner.worker_types.configuration import load_worker_runtime_definitions

JsonObject = dict[str, Any]


@pytest.fixture(autouse=True)
def scrub_ambient_plan_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.startswith("PLAN_"):
            monkeypatch.delenv(key)


class ServerHandle:
    base = "http://testserver"
    db_path: Path


class ApiHelper:
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def get(self, _server: ServerHandle, path: str) -> JsonObject:
        response = self.client.get(path)
        assert response.status_code == 200, response.text
        return cast(JsonObject, response.json())


@pytest.fixture
def cli_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[ServerHandle, Callable[..., JsonObject], ApiHelper]]:
    db_path = tmp_path / "cli-verbs.db"
    with closing(connect(str(db_path))) as conn:
        create_schema(conn)
        # The probe Worker type is stored like any other, so the server reads it from here.
        seed_probe_worker_type(conn)
        load_worker_runtime_definitions(conn)
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_FAKE_NOW": "2026-07-04T12:00:00",
        },
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
            content: bytes | None = None,
        ) -> httpx.Response:
            del timeout
            path = httpx.URL(url).raw_path.decode()
            return cast(
                httpx.Response,
                client.request(
                    method,
                    path,
                    json=json,
                    params=params,
                    headers=headers,
                    content=content,
                ),
            )

        monkeypatch.setattr(httpx, "request", request)
        server = ServerHandle()
        server.db_path = db_path

        def invoke(
            _server: ServerHandle,
            *args: str,
            ticket_id: str | None = None,
            actor: str | None = None,
            sprint_item_id: str | None = None,
            stdin: str | None = None,
        ) -> JsonObject:
            # The identity a launched Worker runs under, which is both variables
            # together: worker_conversation_role_materials sets PLAN_ACTOR beside the
            # Ticket id. The CLI sends what the environment says and claims nothing else.
            env = {"PLAN_SERVER_URL": server.base}
            if ticket_id is not None:
                env["PLAN_TICKET_ID"] = ticket_id
                env["PLAN_ACTOR"] = "worker"
            if actor is not None:
                env["PLAN_ACTOR"] = actor
            if sprint_item_id is not None:
                env["PLAN_SPRINT_ITEM_ID"] = sprint_item_id
            result = CliRunner().invoke(cli_main, [*args, "--json"], input=stdin, env=env)
            assert result.exit_code == 0, result.output
            return cast(JsonObject, json.loads(result.stdout))

        yield server, invoke, ApiHelper(client)




@pytest.fixture
def server(
    cli_app: tuple[ServerHandle, Callable[..., JsonObject], ApiHelper],
) -> ServerHandle:
    return cli_app[0]


@pytest.fixture
def cli(
    cli_app: tuple[ServerHandle, Callable[..., JsonObject], ApiHelper],
) -> Callable[..., JsonObject]:
    return cli_app[1]


@pytest.fixture
def api(
    cli_app: tuple[ServerHandle, Callable[..., JsonObject], ApiHelper],
) -> ApiHelper:
    return cli_app[2]








def test_ticket_cli_forwards_the_whole_launch_configuration_create_and_set(
    server: ServerHandle,
    cli: Callable[..., JsonObject],
    api: ApiHelper,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backend and the model travel together, at both doors the CLI has.

    A model id belongs to the backend that named it, so neither door can carry one of them
    on its own: creation takes the model with the backend it overrides to, and changing a
    Ticket's choice afterwards is a command that names the whole configuration.
    """

    # Both fictional selections come from scripted provider catalogues at the real boundary.
    async def snapshot(
        backend_key: ConversationBackendKey,
        **kwargs: object,
    ) -> BackendSnapshot:
        model = {
            ConversationBackendKey.hermes: "hermes-model",
            ConversationBackendKey.codex: "gpt-99-sol",
        }[backend_key]
        return BackendSnapshot(
            backend_key=backend_key,
            installed=True,
            executable_path=f"/scripted/{backend_key}",
            version="1.0.0",
            identity=None,
            available_models=(BackendModel(model),),
            reasoning_effort_options=(),
            default_model_id=model,
            default_reasoning_effort=None,
            update_advisory=None,
            diagnoses=(),
        )

    monkeypatch.setattr(
        cast(FastAPI, api.client.app).state.conversation.backend_snapshots,
        "snapshot",
        snapshot,
    )
    created = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--employee-backend",
        "hermes",
        "--employee-launch-model",
        "hermes-model",
        "--title",
        "CLI backend selection",
        "--kickoff-note",
        "Keep Kickoff pristine",
    )
    assert created["employee_backend"] == "hermes"
    assert created["employee_launch_model"] == "hermes-model"
    updated = cli(
        server,
        "ticket",
        "employee-configuration",
        created["id"],
        "--backend",
        "codex",
        "--model",
        "gpt-99-sol",
    )
    assert updated["employee_backend"] == "codex"
    assert updated["employee_launch_model"] == "gpt-99-sol"
    stored = api.get(server, f"/api/tickets?detail=full&id={created['id']}")
    assert stored["employee_backend"] == "codex"
    assert stored["employee_launch_model"] == "gpt-99-sol"





    # `trouble` only accepts writes during an active claimed worker step. The refusal
    # assertion above covers its argument parsing. Writer tests cover the active path.








@pytest.mark.parametrize(
    "extra",
    [
        {"breif": "private misspelled Brief"},
        {"kickoff_note": "private old key"},
        {"brief": "real Brief", "kickoff_note": "private old key"},
    ],
)
def test_structured_ticket_create_propagates_unknown_key_errors(
    server: ServerHandle, extra: JsonObject
) -> None:
    with connect(str(server.db_path)) as conn:
        before = tuple(conn.iterdump())
    result = CliRunner().invoke(
        cli_main,
        ["ticket", "create", "--input-json", "-", "--json"],
        input=json.dumps({"title": "Rejected CLI intake", "worker_type": "coding", **extra}),
        env={"PLAN_SERVER_URL": server.base},
    )
    assert result.exit_code == 1, result.output
    error = json.loads(result.stderr)["error"]
    assert error["code"] == "validation"
    unknown_key = next(key for key in extra if key != "brief")
    assert error["detail"] == {"field": unknown_key}
    assert "private" not in result.output
    with connect(str(server.db_path)) as conn:
        assert tuple(conn.iterdump()) == before


@pytest.mark.parametrize("scoped", [False, True])
def test_structured_ticket_create_keeps_the_brief(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper, scoped: bool
) -> None:
    body: JsonObject = {
        "worker_type": "coding",
        "title": "Structured Brief",
        "brief": "  Keep the structured Brief.\nSecond paragraph.  ",
    }
    if scoped:
        body["ceiling"] = "needs_implementation"
    created = cli(server, "ticket", "create", "--input-json", "-", stdin=json.dumps(body))
    detail = api.get(server, f"/api/tickets?detail=full&id={created['id']}")
    if scoped:
        assert detail["field_values"]["brief"] == body["brief"]
        assert detail["pending_proposal"] is None
        assert detail["stage"] == "needs_success_condition"
    else:
        assert detail["pending_proposal"]["field"] == "brief"
        assert detail["pending_proposal"]["body"] == body["brief"]
        assert detail["stage"] == "needs_brief"
