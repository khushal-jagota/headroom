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
from tests.support.probe import install_probe_registry, uninstall_probe_registry

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
            actor: str | None = None,
            stdin: str | None = None,
        ) -> JsonObject:
            env = {"PLAN_SERVER_URL": server.base}
            if ticket_id is not None:
                env["PLAN_TICKET_ID"] = ticket_id
            if actor is not None:
                env["PLAN_ACTOR"] = actor
            result = CliRunner().invoke(
                cli_main, [*args, "--json"], input=stdin, env=env
            )
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


@pytest.fixture
def probe_worker_type() -> Iterator[None]:
    install_probe_registry()
    try:
        yield
    finally:
        uninstall_probe_registry()


def test_worker_type_list_json_preserves_the_registry_manifest(
    probe_worker_type: None,
    server: ServerHandle,
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    expected = api.get(server, "/api/worker-types")

    listed = cli(server, "worker-type", "list")

    assert listed == expected
    assert listed["worker_types"][-1]["worker_type"] == "probe"


def test_worker_type_list_human_output_uses_registry_order(
    probe_worker_type: None,
    cli_app: tuple[ServerHandle, Callable[..., JsonObject], ApiHelper],
) -> None:
    server, _, api = cli_app
    expected = api.get(server, "/api/worker-types")

    result = CliRunner().invoke(
        cli_main,
        ["worker-type", "list"],
        env={"PLAN_SERVER_URL": server.base},
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        item["worker_type"] for item in expected["worker_types"]
    ]
    assert result.stdout.splitlines()[-1] == "probe"


@pytest.mark.parametrize(
    "command",
    [
        ("schedule", "create"),
        ("ticket", "create"),
        ("chief", "create-ticket-from-external-work"),
    ],
)
def test_worker_type_inputs_point_to_the_listing_command(
    command: tuple[str, ...],
) -> None:
    result = CliRunner().invoke(cli_main, [*command, "--help"])

    assert result.exit_code == 0, result.output
    assert "panels worker-type list" in result.output


def test_day_cli_round_trips_midday_reconciliation(
    server: ServerHandle, cli: Callable[..., JsonObject]
) -> None:
    updated = cli(
        server,
        "day",
        "set",
        "midday-reconciliation",
        "--date",
        "2026-07-04",
        "--value",
        "The morning bet still holds.",
    )
    shown = cli(
        server,
        "day",
        "show",
        "2026-07-04",
        "midday_reconciliation",
    )

    assert updated["midday_reconciliation"] == "The morning bet still holds."
    assert (
        shown["parts"]["midday_reconciliation"]["value"]
        == "The morning bet still holds."
    )


def test_record_reads_share_manifests_selection_and_identity(
    cli_app: tuple[ServerHandle, Callable[..., JsonObject], ApiHelper],
) -> None:
    server, cli, _ = cli_app
    cli(
        server,
        "project",
        "set",
        "project_other",
        "summary",
        "--value",
        "Shared work.",
    )
    sprint = cli(
        server,
        "sprint",
        "create",
        "--name",
        "Current sprint",
        "--date-start",
        "2026-06-29",
        "--date-end",
        "2026-07-12",
        "--primary-bet",
        "Keep the grammar shared.",
    )
    item = cli(
        server,
        "sprint",
        "item",
        "create",
        "--title",
        "Record reads",
        "--project-id",
        "project_other",
        "--sprint",
        sprint["id"],
        "--body-file",
        "-",
        stdin="Show one part. 🌱",
    )
    ticket = cli(
        server,
        "ticket",
        "create",
        "--title",
        "Build record reads",
        "--worker-type",
        "coding",
        "--sprint-item",
        item["id"],
        "--kickoff-note",
        "Approved context.",
    )
    cli(
        server,
        "day",
        "add-ticket",
        ticket["id"],
        "--date",
        "2026-07-04",
    )

    ticket_manifest = cli(server, "ticket", "show", ticket["id"])
    worker_manifest = cli(
        server,
        "worker",
        "my-ticket",
        ticket_id=ticket["id"],
        actor="worker",
    )
    sprint_manifest = cli(server, "sprint", "show", "current")
    item_part = cli(server, "sprint", "item", "show", item["id"], "body")
    day_manifest = cli(server, "day", "show", "2026-07-04")
    day_part = cli(
        server,
        "day",
        "show",
        "--date",
        "2026-07-04",
        "notes,focus",
    )
    project_part = cli(server, "project", "show", "project_other", "summary")

    assert list(ticket_manifest["manifest"])[0] == "kickoff"
    assert ticket_manifest["header"]["ticket_status"] == "awaiting_approval"
    assert worker_manifest["header"]["worker"] == "panels-worker-coding"
    assert worker_manifest["header"]["id"] == ticket["id"]
    assert sprint_manifest["header"]["id"] == sprint["id"]
    assert list(sprint_manifest["manifest"]) == [
        "limiting_factor",
        "primary_bet",
        "supports",
        "premortem",
        "mid_where_we_stand",
        "mid_whats_changed",
        "mid_what_to_adjust",
        "outcomes",
        "solo_reflection",
        "joint_discussion",
        "updates_to_thinking",
        "carry_forward",
    ]
    assert item_part["parts"]["body"]["value"] == "Show one part. 🌱"
    assert sum(item_part["header"]["rollup"].values()) == 1
    assert "tickets" not in day_manifest
    assert "tickets" not in day_manifest["header"]
    assert "tickets" not in day_manifest["manifest"]
    assert list(day_part["parts"]) == ["notes", "focus"]
    assert project_part["parts"]["summary"]["value"] == "Shared work."

    human = CliRunner().invoke(
        cli_main,
        ["ticket", "show", ticket["id"], "kickoff"],
        env={"PLAN_SERVER_URL": server.base},
    )
    assert human.exit_code == 0, human.output
    assert "ticket_status: awaiting_approval" in human.stdout
    assert "value: null" in human.stdout
    assert "proposal:" in human.stdout

    invalid = CliRunner().invoke(
        cli_main,
        ["project", "show", "project_other", "missing", "--json"],
        env={"PLAN_SERVER_URL": server.base},
    )
    assert invalid.exit_code == 1
    error = json.loads(invalid.stderr)["error"]
    assert error["message"] == (
        "unknown part names: missing; valid part names: summary"
    )


def test_planning_worker_cli_claims_authorize_day_midday_and_sprint_writes(
    planning_worker_registry: None,
    server: ServerHandle,
    cli: Callable[..., JsonObject],
) -> None:
    day_ticket = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "planning-day",
        "--title",
        "Plan the day",
    )
    midday_ticket = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "planning-midday-check",
        "--title",
        "Reconcile midday",
    )
    sprint_ticket = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "planning-sprint",
        "--title",
        "Plan the sprint",
    )

    day = cli(
        server,
        "day",
        "set",
        "focus",
        "--date",
        "2026-07-04",
        "--value",
        "Ship the planning boundary.",
        ticket_id=day_ticket["id"],
        actor="worker",
    )
    midday = cli(
        server,
        "day",
        "set",
        "midday-reconciliation",
        "--date",
        "2026-07-04",
        "--value",
        "The morning bet still holds.",
        ticket_id=midday_ticket["id"],
        actor="worker",
    )
    sprint = cli(
        server,
        "sprint",
        "create",
        "--name",
        "Planning proof",
        "--date-start",
        "2026-07-06",
        "--date-end",
        "2026-07-19",
        "--limiting-factor",
        "No durable plan.",
        "--primary-bet",
        "Use one canonical sprint.",
        "--supports",
        "Keep it small.",
        "--premortem",
        "The plan drifts.",
        ticket_id=sprint_ticket["id"],
        actor="worker",
    )
    sprint_readback = cli(server, "sprint", "show", sprint["id"], "primary_bet")

    assert day["focus"] == "Ship the planning boundary."
    assert midday["midday_reconciliation"] == "The morning bet still holds."
    assert (
        sprint_readback["parts"]["primary_bet"]["value"]
        == "Use one canonical sprint."
    )


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
        "--project",
        "Vylo",
    )
    assert created["cadence"] == "every_planning_day"
    assert created["enabled"] is True
    assert created["placement_mode"] == "current_sprint"

    listed = cli(server, "schedule", "list")
    assert {item["id"] for item in listed["schedules"]} == {
        "schedule_weekly_sprint_checkpoint",
        created["id"],
    }

    day_four = cli(
        server,
        "schedule",
        "set",
        created["id"],
        "cadence",
        "--value",
        "current-sprint-day-four",
    )
    assert day_four["cadence"] == "current_sprint_day_four"

    backlog = cli(
        server,
        "schedule",
        "create",
        "--title",
        "Backlog planning",
        "--worker-type",
        "coding",
        "--time",
        "09:00",
        "--backlog",
    )
    assert backlog["placement_mode"] == "backlog"
    assert backlog["sprint_item_id"] is None

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
    item_id = cli(
        server,
        "sprint",
        "item",
        "create",
        "--title",
        "Scheduled target",
        "--project",
        "Vylo",
    )["id"]
    exact = cli(
        server,
        "schedule",
        "set",
        created["id"],
        "sprint-item",
        "--value",
        item_id,
    )
    assert exact["placement_mode"] == "sprint_item"
    assert exact["sprint_item_id"] == item_id
    assert exact["project_id"] is None
    updated = cli(
        server,
        "schedule",
        "set",
        created["id"],
        "placement",
        "--value",
        "backlog",
    )
    assert updated["placement_mode"] == "backlog"
    assert updated["project_id"] == "project_vylo"

    shown = cli(server, "schedule", "show", created["id"])
    assert shown["occurrences"] == []
    assert api.get(server, f"/api/schedules/{created['id']}")["enabled"] is False


def test_ticket_create_sprint_item_parents_it(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> None:
    # --item -> --sprint-item: the option renamed; still parents the ticket under the item.
    iid = cli(
        server,
        "sprint",
        "item",
        "create",
        "--title",
        "Item A",
        "--project",
        "Vylo",
        "--priority",
        "P1",
    )["id"]
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
    assert detail["priority"] == "P1"
    assert detail["resolved_priority_anchors"]["sprint_item"] == {
        "id": iid,
        "title": "Item A",
        "priority": "P1",
    }
    assert [
        t["id"] for t in cli(server, "ticket", "list", "--sprint-item", iid)["tickets"]
    ] == [tid]


def test_ticket_list_day_filter(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> None:
    # `ticket list --day today` scopes to the day's board (day_tickets join).
    t_on = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", "On today"
    )["id"]
    t_off = cli(
        server, "ticket", "create", "--worker-type", "coding", "--title", "Off day"
    )["id"]
    cli(server, "day", "add-ticket", t_on, "--date", "today")  # add t_on to today's day
    cli(server, "day", "remove-ticket", t_off, "--date", "today")

    listed = cli(server, "ticket", "list", "--day", "today")
    ids = [t["id"] for t in listed["tickets"]]
    assert t_on in ids
    assert t_off not in ids

    # The fake clock's planning date is 2026-07-04: an ISO --day gives the same set.
    dated = [
        t["id"] for t in cli(server, "ticket", "list", "--day", "2026-07-04")["tickets"]
    ]
    assert dated == ids

    # the unscoped list still returns both.
    all_ids = [t["id"] for t in cli(server, "ticket", "list")["tickets"]]
    assert t_on in all_ids and t_off in all_ids


def test_project_create_list_and_project_id_item_filter(
    server: ServerHandle, cli: Callable[..., JsonObject]
) -> None:
    missing_priority = CliRunner().invoke(
        cli_main, ["project", "create", "--name", "Missing Priority", "--json"]
    )
    assert missing_priority.exit_code == 2
    assert "Missing option '--priority'" in missing_priority.output

    project = cli(
        server,
        "project",
        "create",
        "--name",
        "Alpha One",
        "--priority",
        "P1",
    )
    assert project["id"] == "project_alpha_one"
    assert project["name"] == "Alpha One"
    assert project["priority"] == "P1"

    listed_projects = cli(server, "project", "list")
    listed_by_id = {entry["id"]: entry for entry in listed_projects["projects"]}
    assert listed_by_id[project["id"]]["priority"] == "P1"
    assert listed_by_id["project_other"]["priority"] is None

    reassessed = cli(
        server,
        "project",
        "set",
        project["id"],
        "priority",
        "--value",
        "P0",
    )
    assert reassessed["priority"] == "P0"
    assert reassessed["name"] == "Alpha One"

    invalid_priority = CliRunner().invoke(
        cli_main,
        ["project", "set", project["id"], "priority", "--value", "urgent", "--json"],
    )
    assert invalid_priority.exit_code == 1
    assert "priority must be P0, P1, P2, or P3" in invalid_priority.output

    cleared_priority = CliRunner().invoke(
        cli_main,
        ["project", "set", project["id"], "priority", "--clear", "--json"],
    )
    assert cleared_priority.exit_code == 1
    assert "priority cannot be cleared" in cleared_priority.output

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
    assert (
        created["fields"]["kickoff"]["proposal"]["body"] == "intake context from user"
    )

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
    approved = cli(
        server, "ticket", "approve", tid, "--ceiling", "none", "--at-cap", "propose"
    )
    assert approved["stage"] == "needs_approach"
    assert approved["fields"]["success"]["value"] == "success body"

    cli(
        server,
        "worker",
        "note",
        tid,
        "approach",
        "--body-file",
        "-",
        stdin="approach note",
    )
    cli(
        server,
        "worker",
        "note",
        tid,
        "approach",
        "--append",
        "--body-file",
        "-",
        stdin="additional approach note",
    )
    appended_detail = api.get(server, f"/api/tickets/{tid}")
    assert (
        appended_detail["fields"]["approach"]["user_note"]
        == "approach note\n\nadditional approach note"
    )
    cli(
        server,
        "worker",
        "note",
        tid,
        "approach",
        "--replace",
        "--body-file",
        "-",
        stdin="replaced approach note",
    )
    detail = api.get(server, f"/api/tickets/{tid}")
    assert detail["fields"]["approach"]["user_note"] == "replaced approach note"

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
    assert "replaced approach note" in copied["text"]


def test_sprint_item_ticket_commands_move_atomically_and_to_backlog(
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
    item = cli(
        server,
        "sprint",
        "item",
        "create",
        "--title",
        "CLI item",
        "--project-id",
        "project_other",
        "--sprint",
        "current",
    )

    cli(server, "sprint", "item", "move-ticket", item["id"], tid)
    detail = api.get(server, f"/api/tickets/{tid}")
    assert detail["sprint_item_id"] == item["id"]
    assert detail["effective_sprint_id"] == sprint["id"]

    renamed = cli(
        server, "sprint", "set", "current", "name", "--value", "Renamed CLI sprint"
    )
    assert renamed["id"] == sprint["id"]
    assert renamed["name"] == "Renamed CLI sprint"

    cli(server, "sprint", "item", "move-ticket-to-backlog", item["id"], tid)
    detail = api.get(server, f"/api/tickets/{tid}")
    assert detail["sprint_item_id"] is None
    assert detail["effective_sprint_id"] is None
