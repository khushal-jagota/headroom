"""E2E for the W3b one-CLI rework — real `panels serve` subprocess, CLI over HTTP. Non-anchored
names (the test_eNN_ anchors are reserved for the SPEC acceptance items). No browser: these
drive the CLI + API surfaces only. The worker-step readiness loop never runs in test mode."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from tests.e2e.harness import ApiHelper, JsonObject, ServerHandle

from planner.environments.app import _write_app_entrypoints

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_BIN = REPO_ROOT / ".venv" / "bin" / "panels"
OPS_ROOT = REPO_ROOT / "ops" / "panels-environments"
DEPLOYED_APP_SHA = "0123456789abcdef0123456789abcdef01234567"


def test_installed_panels_preserves_worker_identity_for_read_and_write(
    tmp_path: Path,
    server: ServerHandle,
    cli: Callable[..., JsonObject],
    api: ApiHelper,
) -> None:
    ticket_id = cli(
        server,
        "ticket",
        "create",
        "--worker-type",
        "coding",
        "--title",
        "Installed CLI worker",
    )["id"]
    home = tmp_path / "operator"
    command_directory = home / ".local" / "bin"
    command_directory.mkdir(parents=True)
    shutil.copy2(OPS_ROOT / "panels", command_directory / "panels")
    app = home / "Deployments" / "Panels" / "current" / "app"
    app.mkdir(parents=True)
    (app / ".venv").symlink_to(Path(sys.prefix))
    _write_app_entrypoints(app, app_sha=DEPLOYED_APP_SHA)
    outside_checkout = tmp_path / "outside-checkout"
    outside_checkout.mkdir()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PLAN_")}
    environment.update(
        {
            "HOME": str(home),
            "PATH": f"{command_directory}:/usr/bin:/bin",
            "PLAN_SERVER_URL": server.base,
            "PLAN_TICKET_ID": ticket_id,
            "PLAN_ACTOR": "worker",
            "PLAN_APP_ROOT": "/ambient/wrong-app",
            "PLAN_APP_SHA": "76543210fedcba9876543210fedcba9876543210",
            "PYTHONPATH": "/ambient/package",
        }
    )

    local_status = subprocess.run(
        ["panels", "environment", "status", "--json"],
        cwd=outside_checkout,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(local_status.stdout)["app"] == {
        "state": "healthy",
        "summary": "configured app identity",
        "sha": DEPLOYED_APP_SHA,
    }

    identity = subprocess.run(
        ["panels", "worker", "my-ticket", "--json"],
        cwd=outside_checkout,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(identity.stdout)["id"] == ticket_id

    proposal = subprocess.run(
        [
            "panels",
            "worker",
            "propose",
            "--body-file",
            "-",
            "--recap",
            "Installed CLI retained worker context.",
            "--json",
        ],
        input="Success through installed panels.",
        cwd=outside_checkout,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert json.loads(proposal.stdout)["id"] == ticket_id
    detail = api.get(server, f"/api/tickets/{ticket_id}")
    assert detail["fields"]["success"]["proposal"]["body"] == "Success through installed panels."


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


def test_queue_pickup_command_removed(server: ServerHandle) -> None:
    # `queue` is no longer a CLI command group; approval is homed on ticket/sprint item.
    proc = subprocess.run(
        [str(PLAN_BIN), "queue", "pickup", "--json"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    assert proc.returncode != 0  # no such command
    assert "queue" in (proc.stderr + proc.stdout)  # click's "No such command 'queue'"


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
