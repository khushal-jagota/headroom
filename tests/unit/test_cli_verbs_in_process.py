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
        "orchestrator": {"kind": "sprint_item", "id": item["id"]},
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
    # Historical records are seeded directly. Live calendar writers are retired.
    with connect(str(server.db_path)) as conn:
        conn.execute(
            "INSERT INTO sprints(id,name,date_start,date_end,primary_bet,created_at,updated_at) "
            "VALUES ('sp_historical','Historical','2026-06-29','2026-07-12','Preserved',1,1)"
        )
        conn.execute(
            "INSERT INTO days(id,focus,created_at,updated_at) "
            "VALUES ('day_2026-07-04','Preserved focus',1,1)"
        )
    sprint = {"id": "sp_historical"}
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
    with connect(str(server.db_path)) as conn:
        conn.execute(
            "INSERT INTO sprint_outcomes(sprint_id,outcome_id) VALUES (?,?)",
            (sprint["id"], item["id"]),
        )
    historical = cli(server, "sprint", "outcome", "list", sprint["id"])
    assert historical["outcome_groups"][0]["committed"] is True
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
    assert error["message"] == ("unknown part names: missing; valid part names: summary")


def test_calendar_cli_writes_are_retired_and_planning_types_reject_creation(
    server: ServerHandle, api: ApiHelper
) -> None:
    for args in (("day", "set", "focus", "--value", "old"), ("sprint", "create", "--name", "old")):
        result = CliRunner().invoke(cli_main, list(args))
        assert result.exit_code != 0
        assert "Calendar planning writes are retired" in result.output
    for worker_type in ("personal", "planning-day", "planning-midday-check", "planning-sprint"):
        response = api.client.post(
            "/api/tickets", json={"title": "Retired", "worker_type": worker_type}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "validation"


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
        "Useful daily work",
        "--worker-type",
        "coding",
        "--time",
        "08:30",
        "--kickoff-note",
        "Gather first",
        "--project",
        "Vylo",
    )
    assert created["cadence"] == "daily"
    assert created["enabled"] is True
    assert created["orchestrator"] == {"kind": "chief", "id": "chief"}
    listed = cli(server, "schedule", "list")
    assert {item["id"] for item in listed["schedules"]} == {created["id"]}
    item = cli(server, "orchestrator", "create", "--title", "Scheduled target", "--project", "Vylo")
    assigned = cli(server, "schedule", "set", created["id"], "sprint-item", "--value", item["id"])
    assert assigned["orchestrator"] == {"kind": "sprint_item", "id": item["id"]}
    assert assigned["project_id"] == "project_vylo"
    updated = cli(server, "schedule", "set", created["id"], "enabled", "--value", "false")
    assert updated["enabled"] is False
    assert cli(server, "schedule", "show", created["id"])["occurrences"] == []
    assert api.get(server, f"/api/schedules/{created['id']}")["enabled"] is False
    rejected = CliRunner().invoke(
        cli_main,
        [
            "schedule",
            "set",
            created["id"],
            "cadence",
            "--value",
            "current-sprint-day-four",
            "--json",
        ],
    )
    assert rejected.exit_code != 0
    assert "cadence must be one of: daily" in rejected.output


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
    assert api.get(server, f"/api/tickets?detail=full&id={tid}")["recap"] == "Ready to approve."
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


def test_legacy_item_membership_assigns_explicit_orchestrators(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> None:
    tid = cli(server, "ticket", "create", "--worker-type", "coding", "--title", "Assignment")["id"]
    item = cli(
        server,
        "orchestrator",
        "create",
        "--title",
        "Shared intent",
        "--project-id",
        "project_other",
    )
    cli(server, "sprint", "item", "add-ticket", item["id"], tid)
    detail = api.get(server, f"/api/tickets?detail=full&id={tid}")
    assert detail["orchestrator"] == {"kind": "sprint_item", "id": item["id"]}
    cli(server, "sprint", "item", "remove-ticket", item["id"], tid)
    assert api.get(server, f"/api/tickets?detail=full&id={tid}")["orchestrator"] == {
        "kind": "chief",
        "id": "chief",
    }


def test_ticket_assignment_keeps_project_and_holder_validation(
    server: ServerHandle, cli: Callable[..., JsonObject], api: ApiHelper
) -> None:
    item = cli(
        server,
        "orchestrator",
        "create",
        "--title",
        "Assignment target",
        "--project-id",
        "project_vylo",
    )
    tid = cli(server, "ticket", "create", "--worker-type", "coding", "--title", "Place me")["id"]
    placed = cli(
        server, "ticket", "place", tid, "--project-id", "project_vylo", "--sprint-item", item["id"]
    )
    assert placed["project_id"] == "project_vylo"
    assert placed["orchestrator"] == {"kind": "sprint_item", "id": item["id"]}
    chief = cli(server, "ticket", "assign", tid, "--chief")
    assert chief["orchestrator"] == {"kind": "chief", "id": "chief"}
    assert chief["project_id"] == "project_vylo"
    assert chief["ceiling_holder"] == placed["ceiling_holder"]


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
