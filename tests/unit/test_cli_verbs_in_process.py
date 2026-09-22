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
from tests.support.probe import seed_probe_worker_type

from planner.cli.main import main as cli_main
from planner.conversation.in_memory_conversation_system import (
    InMemoryConversationSystem,
)
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
            result = CliRunner().invoke(
                cli_main, [*args, "--json"], input=stdin, env=env
            )
            assert result.exit_code == 0, result.output
            return cast(JsonObject, json.loads(result.stdout))

        yield server, invoke, ApiHelper(client)


def test_structured_create_and_supervisor_revision_use_neutral_ticket_commands(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> None:
    item = cli(
        server,
        "sprint",
        "item",
        "create",
        "--title",
        "Neutral supervisor flow",
        "--project-id",
        "project_other",
    )
    create_body = {
        "title": "Review through neutral commands",
        "worker_type": "coding",
        "project_id": "project_other",
        "sprint_item_id": item["id"],
        "kickoff_note": "Review this intake.",
    }
    ticket = cli(
        server,
        "ticket",
        "create",
        "--input-json",
        "-",
        actor="sprint_item_supervisor",
        sprint_item_id=item["id"],
        stdin=json.dumps(create_body),
    )
    cli(
        server,
        "send-message",
        "--ticket",
        ticket["id"],
        "--message",
        "Prepare for review.",
        actor="sprint_item_supervisor",
        sprint_item_id=item["id"],
    )

    revised = cli(
        server,
        "ticket",
        "proposal",
        ticket["id"],
        "revise",
        actor="sprint_item_supervisor",
        sprint_item_id=item["id"],
        stdin="Keep the Brief focused.",
    )

    assert revised["pending_proposal"] is None
    detail = api.get(server, f"/api/tickets?detail=full&id={ticket['id']}")
    assert detail["pending_proposal"] is None
    assert detail["stage"] == "needs_brief"


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


def test_send_message_cli_mode_reaches_the_current_conversation_system(
    server: ServerHandle, cli: Callable[..., JsonObject]
) -> None:
    ticket = cli(
        server,
        "ticket",
        "create",
        "--title",
        "Receive messages",
        "--worker-type",
        "coding",
        "--kickoff-note",
        "Keep the delivery path exact.",
    )

    started = cli(
        server,
        "send-message",
        "--ticket",
        ticket["id"],
        "--message",
        "Start with the default.",
    )
    queued = cli(
        server,
        "send-message",
        "--ticket",
        ticket["id"],
        "--message",
        "Wait next.",
        "--mode",
        "queue",
    )
    injected = cli(
        server,
        "send-message",
        "--ticket",
        ticket["id"],
        "--message",
        "Use this now.",
        "--mode",
        "steer",
    )
    sent_now = cli(
        server,
        "send-message",
        "--ticket",
        ticket["id"],
        "--message",
        "Interrupt and run this.",
        "--mode",
        "send_now",
    )
    idle_steer = cli(
        server,
        "send-message",
        "--chief",
        "--message",
        "There is no turn yet.",
        "--mode",
        "steer",
    )

    assert started["fate"] == "started"
    assert queued["fate"] == "queued"
    assert queued["queue_position"] == 1
    assert injected["fate"] == "injected"
    assert sent_now["fate"] == "started"
    assert idle_steer["fate"] == "started"
    assert idle_steer["conversation_id"] is not None


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
        "--body-file",
        "-",
        stdin="Show one part. 🌱",
    )
    cli(server, "sprint", "outcome", "add", sprint["id"], item["id"])
    empty = cli(server, "sprint", "outcome", "list", sprint["id"])
    assert empty["outcome_groups"][0]["committed"] is True
    assert empty["outcome_groups"][0]["tickets"] == []
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

    assert list(ticket_manifest["manifest"])[0] == "brief"
    assert ticket_manifest["header"]["ticket_status"] == "awaiting_approval"
    assert worker_manifest["header"]["worker"] == "panels-worker-coding"
    assert worker_manifest["header"]["id"] == ticket["id"]
    assert sprint_manifest["header"]["id"] == sprint["id"]
    assert list(sprint_manifest["manifest"]) == [
        "primary_bet",
        "kickoff",
        "checkpoint",
        "review",
    ]
    assert item_part["parts"]["body"]["value"] == "Show one part. 🌱"
    assert "rollup" not in item_part["header"]
    assert "tickets" not in day_manifest
    assert "tickets" not in day_manifest["header"]
    assert "tickets" not in day_manifest["manifest"]
    assert list(day_part["parts"]) == ["notes", "focus"]
    assert project_part["parts"]["summary"]["value"] == "Shared work."

    next_sprint = cli(
        server,
        "sprint",
        "create",
        "--name",
        "Next sprint",
        "--date-start",
        "2026-07-13",
        "--date-end",
        "2026-07-26",
    )
    carried = cli(
        server,
        "sprint",
        "outcome",
        "carry",
        sprint["id"],
        item["id"],
        "--to",
        next_sprint["id"],
        "--ticket",
        ticket["id"],
    )
    assert carried["ticket_ids"] == [ticket["id"]]
    cli(server, "sprint", "outcome", "remove", next_sprint["id"], item["id"])
    tracking = cli(server, "sprint", "outcome", "list", next_sprint["id"])
    assert tracking["outcome_groups"][0]["committed"] is False
    assert [t["id"] for t in tracking["outcome_groups"][0]["tickets"]] == [ticket["id"]]

    human = CliRunner().invoke(
        cli_main,
        ["ticket", "show", ticket["id"], "brief"],
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
        "--kickoff",
        "No durable plan. Keep it small and watch for drift.",
        "--primary-bet",
        "Use one canonical sprint.",
        ticket_id=sprint_ticket["id"],
        actor="worker",
    )
    sprint_readback = cli(server, "sprint", "show", sprint["id"], "primary_bet")

    assert day["focus"] == "Ship the planning boundary."
    assert midday["midday_reconciliation"] == "The morning bet still holds."
    assert (
        sprint_readback["parts"]["primary_bet"]["value"] == "Use one canonical sprint."
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
    stored = api.get(server, f"/api/tickets?detail=full&id={created['id']}")
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
    assert exact["placement_mode"] == "current_sprint"
    assert exact["sprint_item_id"] == item_id
    assert exact["project_id"] == "project_vylo"
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
    assert updated["sprint_item_id"] == item_id
    assert updated["project_id"] == "project_vylo"

    shown = cli(server, "schedule", "show", created["id"])
    assert shown["occurrences"] == []
    assert api.get(server, f"/api/schedules/{created['id']}")["enabled"] is False


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
    created = api.get(server, f"/api/tickets?detail=full&id={tid}")
    assert created["stage"] == "needs_brief"
    assert created["pending_proposal"]["body"] == "intake context from user"

    accepted_kickoff = cli(
        server,
        "ticket",
        "proposal",
        tid,
        "accept",
        "--ceiling",
        "none",
        "--kickoff-note-file",
        "-",
        stdin="updated intake",
    )
    assert accepted_kickoff["stage"] == "needs_success_condition"
    assert accepted_kickoff["ceiling"] == "needs_success_condition"
    assert accepted_kickoff["field_values"].get("brief") == "updated intake"

    cli(
        server,
        "ticket",
        "proposal",
        tid,
        "submit",
        ticket_id=tid,
        stdin="success body",
    )
    cli(
        server,
        "ticket",
        "edit",
        tid,
        "--input-json",
        "-",
        ticket_id=tid,
        stdin=json.dumps({"recap": "Ready to approve."}),
    )
    assert (
        api.get(server, f"/api/tickets?detail=full&id={tid}")["recap"]
        == "Ready to approve."
    )
    approved = cli(server, "ticket", "proposal", tid, "accept", "--ceiling", "none")
    assert approved["stage"] == "needs_what_changes"
    assert approved["field_values"].get("success_condition") == "success body"

    cli(
        server,
        "ticket",
        "edit",
        tid,
        "--input-json",
        "-",
        ticket_id=tid,
        stdin=json.dumps({"guidance": "approach note"}),
    )
    cli(
        server,
        "ticket",
        "edit",
        tid,
        "--input-json",
        "-",
        ticket_id=tid,
        stdin=json.dumps({"guidance_append": "additional approach note"}),
    )
    appended_detail = api.get(server, f"/api/tickets?detail=full&id={tid}")
    assert appended_detail["guidance"] == "approach note\n\nadditional approach note"
    cli(
        server,
        "ticket",
        "edit",
        tid,
        "--input-json",
        "-",
        ticket_id=tid,
        stdin=json.dumps({"guidance": "replaced approach note"}),
    )
    detail = api.get(server, f"/api/tickets?detail=full&id={tid}")
    assert detail["guidance"] == "replaced approach note"

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
        ticket_id=new_worker_id,
        stdin="stages note",
    )
    new_worker_detail = api.get(server, f"/api/tickets?detail=full&id={new_worker_id}")
    assert new_worker_detail["guidance"] == "stages note"

    copied = cli(server, "ticket", "copy", tid)
    assert "CLI approve ticket" in copied["text"]
    assert "updated intake" in copied["text"]
    assert "replaced approach note" in copied["text"]

    # `trouble` only accepts writes during an active claimed worker step. The refusal
    # assertion above covers its argument parsing. Writer tests cover the active path.


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
    )

    cli(server, "sprint", "item", "add-ticket", item["id"], tid)
    detail = api.get(server, f"/api/tickets?detail=full&id={tid}")
    assert detail["sprint_item_id"] == item["id"]
    assert detail["effective_sprint_id"] == sprint["id"]

    renamed = cli(
        server, "sprint", "set", "current", "name", "--value", "Renamed CLI sprint"
    )
    assert renamed["id"] == sprint["id"]
    assert renamed["name"] == "Renamed CLI sprint"

    cli(server, "sprint", "item", "remove-ticket", item["id"], tid)
    detail = api.get(server, f"/api/tickets?detail=full&id={tid}")
    assert detail["sprint_item_id"] is None
    assert detail["effective_sprint_id"] == sprint["id"]


def test_ticket_place_sends_one_coherent_placement_patch(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> None:
    sprint = cli(
        server,
        "sprint",
        "create",
        "--name",
        "Placement sprint",
        "--date-start",
        "2026-07-01",
        "--date-end",
        "2026-07-14",
    )
    item = cli(
        server,
        "sprint",
        "item",
        "create",
        "--title",
        "Placement item",
        "--project-id",
        "project_vylo",
    )
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Place me",
        "--backlog",
    )["id"]

    placed = cli(
        server,
        "ticket",
        "place",
        ticket_id,
        "--project-id",
        "project_vylo",
        "--sprint",
        "current",
        "--sprint-item",
        item["id"],
    )
    assert placed["project_id"] == "project_vylo"
    assert placed["sprint_id"] == sprint["id"]
    assert placed["sprint_item_id"] == item["id"]

    backlog = cli(
        server,
        "ticket",
        "place",
        ticket_id,
        "--project-id",
        "project_vylo",
        "--backlog",
        "--clear-sprint-item",
    )
    assert backlog["project_id"] == "project_vylo"
    assert backlog["sprint_id"] is None
    assert backlog["sprint_item_id"] is None


def test_the_ceiling_and_a_settled_value_are_set_like_every_other_field(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> None:
    """One route changes a field, and the CLI reaches it the ordinary way."""
    tid = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Set the ceiling",
        "--kickoff-note",
        "intake",
    )["id"]
    cli(server, "ticket", "approve", tid, "--ceiling", "needs_success_condition")

    raised = cli(server, "ticket", "set", tid, "ceiling", "--value", "consequences")
    assert raised["ceiling"] == "needs_consequences"

    corrected = cli(
        server,
        "ticket",
        "set-value",
        tid,
        "brief",
        "--value",
        "corrected intake",
    )
    assert corrected["field_values"]["brief"] == "corrected intake"
    read = f"/api/tickets?detail=full&id={tid}"
    assert api.get(server, read)["stage"] == "needs_success_condition"
