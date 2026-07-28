"""CLI verb coverage against an in-process Panels application."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterator
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

JsonObject = dict[str, Any]


@pytest.fixture(autouse=True)
def scrub_ambient_plan_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in tuple(os.environ):
        if key.startswith("PLAN_"):
            monkeypatch.delenv(key)


class ServerHandle:
    base = "http://testserver"


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
    with connect(str(db_path)) as conn:
        create_schema(conn)
    config = load_config(
        path=None,
        env={
            "PLAN_TEST_MODE": "1",
            "PLAN_DB_PATH": str(db_path),
            "PLAN_FAKE_NOW": "2026-07-04T12:00:00",
        },
    )
    app = create_app(config, build_clock(config), lambda: connect(str(db_path)))

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
        server = ServerHandle()

        def invoke(
            _server: ServerHandle,
            *args: str,
            ticket_id: str | None = None,
            stdin: str | None = None,
        ) -> JsonObject:
            env = {"PLAN_SERVER_URL": server.base}
            if ticket_id is not None:
                env["PLAN_TICKET_ID"] = ticket_id
            result = CliRunner().invoke(cli_main, [*args, "--json"], input=stdin, env=env)
            assert result.exit_code == 0, result.output
            return cast(JsonObject, json.loads(result.stdout))

        yield server, invoke, ApiHelper(client)


@pytest.fixture
def server(cli_app: tuple[ServerHandle, Callable[..., JsonObject], ApiHelper]) -> ServerHandle:
    return cli_app[0]


@pytest.fixture
def cli(
    cli_app: tuple[ServerHandle, Callable[..., JsonObject], ApiHelper],
) -> Callable[..., JsonObject]:
    return cli_app[1]


@pytest.fixture
def api(cli_app: tuple[ServerHandle, Callable[..., JsonObject], ApiHelper]) -> ApiHelper:
    return cli_app[2]


def test_ticket_cli_forwards_the_whole_launch_configuration_create_and_set(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> None:
    """The backend and the model travel together, at both doors the CLI has.

    A model id belongs to the backend that named it, so neither door can carry one of them
    on its own: creation takes the model with the backend it overrides to, and changing a
    Ticket's choice afterwards is a command that names the whole configuration.
    """
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
        "a-codex-model",
    )
    assert updated["employee_backend"] == "codex"
    stored = api.get(server, f"/api/tickets/{created['id']}")
    assert stored["employee_backend"] == "codex"
    assert stored["employee_launch_model"] == "a-codex-model"


def test_schedule_cli_creates_lists_updates_and_shows_run_state(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> None:
    created = cli(
        server,
        "schedule",
        "create",
        "--title",
        "Morning planning",
        "--worker-type",
        "coding",
        "--time",
        "08:30",
        "--kickoff-note",
        "Gather first",
    )
    assert created["cadence"] == "every_planning_day"
    assert created["enabled"] is True

    listed = cli(server, "schedule", "list")
    assert [item["id"] for item in listed["schedules"]] == [created["id"]]

    updated = cli(
        server,
        "schedule",
        "set",
        created["id"],
        "enabled",
        "--value",
        "false",
    )
    assert updated["enabled"] is False

    shown = cli(server, "schedule", "show", created["id"])
    assert shown["occurrences"] == []
    assert api.get(server, f"/api/schedules/{created['id']}")["enabled"] is False


def test_ticket_create_sprint_item_parents_it(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> None:
    # --item -> --sprint-item: the option renamed; still parents the ticket under the item.
    iid = cli(server, "sprint", "item", "create", "--title", "Item A", "--project", "Vylo")["id"]
    tid = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Child",
        "--sprint-item",
        iid,
    )["id"]
    detail = api.get(server, f"/api/tickets/{tid}")
    assert detail["sprint_item_id"] == iid
    assert [t["id"] for t in cli(server, "ticket", "list", "--sprint-item", iid)["tickets"]] == [
        tid
    ]


def test_ticket_list_day_filter(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> None:
    # `ticket list --day today` scopes to the day's board (day_tickets join).
    t_on = cli(server, "ticket", "create", "--worker-type", "coding", "--title", "On today")["id"]
    t_off = cli(server, "ticket", "create", "--worker-type", "coding", "--title", "Off day")["id"]
    cli(server, "day", "add-ticket", t_on, "--date", "today")  # add t_on to today's day
    cli(server, "day", "remove-ticket", t_off, "--date", "today")

    listed = cli(server, "ticket", "list", "--day", "today")
    ids = [t["id"] for t in listed["tickets"]]
    assert t_on in ids
    assert t_off not in ids

    # The fake clock's planning date is 2026-07-04: an ISO --day gives the same set.
    dated = [t["id"] for t in cli(server, "ticket", "list", "--day", "2026-07-04")["tickets"]]
    assert dated == ids

    # the unscoped list still returns both.
    all_ids = [t["id"] for t in cli(server, "ticket", "list")["tickets"]]
    assert t_on in all_ids and t_off in all_ids


def test_project_create_list_and_project_id_item_filter(
    server: ServerHandle, cli: Callable[..., JsonObject]
) -> None:
    project = cli(server, "project", "create", "--name", "Alpha One")
    assert project["id"] == "project_alpha_one"
    assert project["name"] == "Alpha One"

    listed_projects = cli(server, "project", "list")
    assert project["id"] in {entry["id"] for entry in listed_projects["projects"]}

    item = cli(
        server,
        "sprint",
        "item",
        "create",
        "--title",
        "Project id backlog item",
        "--project-id",
        project["id"],
    )
    listed_items = cli(server, "sprint", "item", "list", "--project-id", project["id"])
    assert [entry["id"] for entry in listed_items["items"]] == [item["id"]]


def test_queue_pickup_command_removed() -> None:
    # `queue` is no longer a CLI command group; approval is homed on ticket/sprint item.
    result = CliRunner().invoke(cli_main, ["queue", "pickup", "--json"])
    assert result.exit_code != 0
    assert "queue" in result.output


def test_ticket_approval_copy_and_worker_note_shape(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> None:
    tid = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "CLI approve ticket",
        "--kickoff-note",
        "intake context from user",
    )["id"]
    created = api.get(server, f"/api/tickets/{tid}")
    assert created["stage"] == "needs_kickoff"
    assert created["fields"]["kickoff"]["proposal"]["body"] == "intake context from user"

    accepted_kickoff = cli(
        server,
        "ticket",
        "approve",
        tid,
        "--ceiling",
        "none",
        "--at-cap",
        "propose",
        "--kickoff-note-file",
        "-",
        stdin="updated intake",
    )
    assert accepted_kickoff["stage"] == "needs_success"
    assert accepted_kickoff["ceiling"] == "needs_success"
    assert accepted_kickoff["at_cap"] == "propose"
    assert accepted_kickoff["fields"]["kickoff"]["value"] == "updated intake"

    cli(
        server,
        "worker",
        "propose",
        "--body-file",
        "-",
        "--recap",
        "Ready to approve.",
        ticket_id=tid,
        stdin="success body",
    )
    approved = cli(server, "ticket", "approve", tid, "--ceiling", "none", "--at-cap", "propose")
    assert approved["stage"] == "needs_approach"
    assert approved["fields"]["success"]["value"] == "success body"

    cli(server, "worker", "note", tid, "approach", "--body-file", "-", stdin="approach note")
    detail = api.get(server, f"/api/tickets/{tid}")
    assert detail["fields"]["approach"]["user_note"] == "approach note"

    new_worker_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "new_worker",
        "--title",
        "CLI new worker note",
    )["id"]
    cli(
        server,
        "worker",
        "note",
        new_worker_id,
        "stages",
        "--body-file",
        "-",
        stdin="stages note",
    )
    new_worker_detail = api.get(server, f"/api/tickets/{new_worker_id}")
    assert new_worker_detail["fields"]["stages"]["user_note"] == "stages note"

    copied = cli(server, "ticket", "copy", tid)
    assert "CLI approve ticket" in copied["text"]
    assert "updated intake" in copied["text"]
    assert "approach note" in copied["text"]


def test_sprint_ticket_commands_use_sprint_option_and_current_selector(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> None:
    sprint = cli(
        server,
        "sprint",
        "create",
        "--name",
        "CLI sprint",
        "--date-start",
        "2026-07-01",
        "--date-end",
        "2026-07-14",
    )
    tid = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Loose sprint ticket",
    )["id"]

    cli(server, "sprint", "add-ticket", tid, "--sprint", "current")
    assert api.get(server, f"/api/tickets/{tid}")["sprint_id"] == sprint["id"]

    renamed = cli(server, "sprint", "set", "current", "name", "--value", "Renamed CLI sprint")
    assert renamed["id"] == sprint["id"]
    assert renamed["name"] == "Renamed CLI sprint"

    cli(server, "sprint", "remove-ticket", tid, "--sprint", "current")
    assert api.get(server, f"/api/tickets/{tid}")["sprint_id"] is None
