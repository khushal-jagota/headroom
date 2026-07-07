"""E2E for the W3b one-CLI rework — real `plan serve` subprocess, CLI over HTTP. Non-anchored
names (the test_eNN_ anchors are reserved for the SPEC acceptance items). No browser: these
drive the CLI + API surfaces only. System A never runs here (PLAN_TEST_MODE) — no real gateway."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_BIN = REPO_ROOT / ".venv" / "bin" / "plan"


def test_ticket_create_sprint_item_parents_it(server, cli, api) -> None:
    # --item -> --sprint-item: the option renamed; still parents the ticket under the item.
    iid = cli(server, "item", "create", "--title", "Item A", "--project", "Vylo")["id"]
    tid = cli(server, "ticket", "create", "--title", "Child", "--sprint-item", iid)["id"]
    detail = api.get(server, f"/api/tickets/{tid}")
    assert detail["sprint_item_id"] == iid


def test_ticket_list_day_filter(server, cli, api) -> None:
    # `ticket list --day today` scopes to the day's board (day_tickets join).
    t_on = cli(server, "ticket", "create", "--title", "On today")["id"]
    t_off = cli(server, "ticket", "create", "--title", "Off day")["id"]
    cli(server, "ticket", "set", t_on, "--day", "today")   # add t_on to today's day

    listed = cli(server, "ticket", "list", "--day", "today")
    ids = [t["id"] for t in listed["tickets"]]
    assert t_on in ids
    assert t_off not in ids

    # --date is the ISO alias of --day (the fake clock's planning date is 2026-07-04): same set.
    dated = [t["id"] for t in cli(server, "ticket", "list", "--date", "2026-07-04")["tickets"]]
    assert dated == ids

    # the unscoped list still returns both.
    all_ids = [t["id"] for t in cli(server, "ticket", "list")["tickets"]]
    assert t_on in all_ids and t_off in all_ids


def test_queue_pickup_command_removed(server) -> None:
    # `queue pickup` is dropped (the board's per-ticket status shows what is ready).
    proc = subprocess.run(
        [str(PLAN_BIN), "queue", "pickup", "--json"],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=30,
    )
    assert proc.returncode != 0                     # no such command
    assert "pickup" in (proc.stderr + proc.stdout)  # click's "No such command 'pickup'"
