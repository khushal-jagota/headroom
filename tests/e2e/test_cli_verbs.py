"""E2E for the W3b one-CLI rework — real `panels serve` subprocess, CLI over HTTP. Non-anchored
names (the test_eNN_ anchors are reserved for the SPEC acceptance items). No browser: these
drive the CLI + API surfaces only. The readiness loop never runs in test mode."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_BIN = REPO_ROOT / ".venv" / "bin" / "panels"


def test_ticket_create_sprint_item_parents_it(server, cli, api) -> None:
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


def test_ticket_list_day_filter(server, cli, api) -> None:
    # `ticket list --day today` scopes to the day's board (day_tickets join).
    t_on = cli(server, "ticket", "create", "--worker-type", "coding", "--title", "On today")["id"]
    t_off = cli(server, "ticket", "create", "--worker-type", "coding", "--title", "Off day")["id"]
    cli(server, "day", "add-ticket", t_on, "--date", "today")   # add t_on to today's day

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


def test_project_create_list_and_project_id_item_filter(server, cli) -> None:
    project = cli(server, "project", "create", "--name", "Alpha One")
    assert project["id"] == "project_alpha_one"
    assert project["name"] == "Alpha One"

    listed_projects = cli(server, "project", "list")
    assert project["id"] in {entry["id"] for entry in listed_projects["projects"]}

    item = cli(
        server,
        "sprint", "item", "create",
        "--title", "Project id backlog item",
        "--project-id", project["id"],
    )
    listed_items = cli(server, "sprint", "item", "list", "--project-id", project["id"])
    assert [entry["id"] for entry in listed_items["items"]] == [item["id"]]


def test_queue_pickup_command_removed(server) -> None:
    # `queue` is no longer a CLI command group; approval is homed on ticket/sprint item.
    proc = subprocess.run(
        [str(PLAN_BIN), "queue", "pickup", "--json"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    assert proc.returncode != 0                     # no such command
    assert "queue" in (proc.stderr + proc.stdout)   # click's "No such command 'queue'"


def test_ticket_approval_copy_events_and_worker_note_shape(server, cli, api) -> None:
    tid = cli(
        server,
        "ticket", "create", "--worker-type", "coding", "--title", "CLI approve ticket",
        "--kickoff-note", "intake context from user",
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
        "worker", "propose", "--body-file", "-", "--recap", "Ready to approve.",
        ticket_id=tid,
        stdin="success body",
    )
    approved = cli(server, "ticket", "approve", tid, "--ceiling", "none", "--at-cap", "propose")
    assert approved["stage"] == "needs_approach"
    assert approved["fields"]["success"]["value"] == "success body"

    cli(server, "worker", "note", tid, "approach", "--body-file", "-", stdin="approach note")
    detail = api.get(server, f"/api/tickets/{tid}")
    assert detail["fields"]["approach"]["user_note"] == "approach note"

    copied = cli(server, "ticket", "copy", tid)
    assert "CLI approve ticket" in copied["text"]
    assert "updated intake" in copied["text"]
    assert "approach note" in copied["text"]
    events = cli(server, "ticket", "events", tid)
    assert "events" in events
    assert any(event["kind"] == "proposal_accepted" for event in events["events"])


def test_sprint_ticket_commands_use_sprint_option_and_current_selector(server, cli, api) -> None:
    sprint = cli(
        server,
        "sprint", "create", "--name", "CLI sprint",
        "--date-start", "2026-07-01", "--date-end", "2026-07-14",
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
